"""Shared plumbing for adapters: polite HTTP, block detection, article reading, splash matching."""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse as up
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from .. import config, store, text
from ..model import Story, story_from


class SourceUnavailable(Exception):
    """Outage, block or challenge page → keep last good, show CACHED."""


class ShapeError(Exception):
    """The source changed shape → keep last good, show DATA CHECK."""


class NotPublished(Exception):
    """No paper today (print-only papers on Sundays) → left out of the rotation, nothing carried."""


CHALLENGE = ("cf-chl", "just a moment...", "attention required", "px-captcha", "_incapsula_",
             "access denied", "are you a robot", "captcha-delivery")


FEED_ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.1"


class Http:
    def __init__(self, session: requests.Session | None = None):
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": config.USER_AGENT, "Accept-Language": "en-GB,en;q=0.9"})
        self._last: dict[str, float] = {}

    def get(self, url: str, *, binary: bool = False, **kw):
        host = up.urlparse(url).netloc
        wait = config.HOST_PACING_OVERRIDE.get(host, config.HOST_PACING_S) - (time.time() - self._last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        try:
            r = self.s.get(url, timeout=kw.pop("timeout", 20), **kw)
        except requests.RequestException as e:
            raise SourceUnavailable(f"{host}: {type(e).__name__}") from e
        finally:
            self._last[host] = time.time()
        if r.status_code in (401, 403, 429, 503):
            raise SourceUnavailable(f"{host}: HTTP {r.status_code}")
        if r.status_code >= 400:
            raise SourceUnavailable(f"{host}: HTTP {r.status_code} for {url}")
        if not binary:
            ctype = r.headers.get("Content-Type", "") if hasattr(r, "headers") else ""
            if "charset" not in ctype.lower() and getattr(r, "encoding", None) in (None, "ISO-8859-1"):
                r.encoding = "utf-8"            # every source here is UTF-8; requests' default is latin-1
            head = r.text[:20000].lower()
            if any(c in head for c in CHALLENGE) and "<article" not in head:
                raise SourceUnavailable(f"{host}: challenge page")
        return r

    def feed(self, url: str) -> str:
        """An RSS/Atom/XML feed, fetched politely: feed Accept header and a conditional request
        (If-None-Match / If-Modified-Since). A 304 returns the copy kept from last time."""
        key = hashlib.sha1(url.encode()).hexdigest()
        hit = store.cache_get("feed", key) or {}
        headers = {"Accept": FEED_ACCEPT}
        if hit.get("etag"):
            headers["If-None-Match"] = hit["etag"]
        if hit.get("modified"):
            headers["If-Modified-Since"] = hit["modified"]
        r = self.get(url, headers=headers)
        if getattr(r, "status_code", 200) == 304 and hit.get("body"):
            return hit["body"]
        h = getattr(r, "headers", None) or {}
        body = r.text
        if h.get("ETag") or h.get("Last-Modified"):
            store.cache_put("feed", key, {"etag": h.get("ETag", ""), "modified": h.get("Last-Modified", ""), "body": body})
        return body


# ----------------------------------------------------------------------------- JSON-LD articles
ARTICLE_TYPES = {"NewsArticle", "Article", "ReportageNewsArticle", "AnalysisNewsArticle", "LiveBlogPosting",
                 "OpinionNewsArticle", "BackgroundNewsArticle"}


def jsonld_nodes(html: str) -> list[dict]:
    """Every JSON-LD object on the page: all scripts, lists, @graph members and hasPart children."""
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            o = stack.pop(0)
            if isinstance(o, list):
                stack.extend(o)
                continue
            if not isinstance(o, dict):
                continue
            out.append(o)
            for k in ("@graph", "hasPart", "mainEntity", "mainEntityOfPage"):
                v = o.get(k)
                if isinstance(v, (list, dict)):
                    stack.append(v)
    return out


def _is_type(o: dict) -> bool:
    t = o.get("@type")
    t = t if isinstance(t, list) else [t]
    return bool(ARTICLE_TYPES & set(x for x in t if isinstance(x, str)))


def jsonld_article(html: str) -> dict:
    return next((o for o in jsonld_nodes(html) if _is_type(o)), {})


def _as_bool(v) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() not in ("false", "0", "no")
    return None


PAID_MARKERS = re.compile(r'is-subscriber-only|<meta[^>]+name="article:content_tier"[^>]+content="locked"', re.I)


def page_free_flag(nodes: list[dict], article: dict, html: str = "") -> bool | None:
    """Paid if ANY node on the page says so (publishers put the flag on WebPage, the article or a
    hasPart element), or the page carries the publisher's own subscriber-only marker (the i tags
    paid pieces "is-subscriber-only" in its data layer, with no JSON-LD flag). Otherwise the
    article's own flag; None when nobody says (e.g. the BBC, The New World)."""
    if any(_as_bool(o.get("isAccessibleForFree")) is False for o in nodes):
        return False
    if html and PAID_MARKERS.search(html):
        return False
    return _as_bool(article.get("isAccessibleForFree"))


def _first(v):
    return v[0] if isinstance(v, list) and v else v


def _image(o: dict, soup: BeautifulSoup) -> str:
    im = _first(o.get("image"))
    if isinstance(im, dict):
        im = im.get("url")
    if not im:
        og = soup.find("meta", property="og:image")
        im = og.get("content") if og else ""
    return im or ""


def read_article(http: Http, url: str, max_age_h: float = 6) -> Story:
    """Article metadata + body from the page a normal visitor gets. Cached by URL.
    Leads are read with max_age_h=0 (always fresh), so an article that turns paid is never
    shown from an older free copy; the cache only spares repeat fetches of rail stories."""
    key = hashlib.sha1(url.encode()).hexdigest()
    hit = store.cache_get("article", key) if max_age_h > 0 else None
    if hit and time.time() - hit.get("_at", 0) < max_age_h * 3600:
        return story_from({k: v for k, v in hit.items() if not k.startswith("_")})
    html = http.get(url).text
    soup = BeautifulSoup(html, "lxml")
    nodes = jsonld_nodes(html)
    o = next((n for n in nodes if _is_type(n)), {})
    free = page_free_flag(nodes, o, html)
    body_ld = text.clean(o.get("articleBody", "")) if o.get("articleBody") else ""
    paras: list[str] = []
    try:
        import trafilatura
        tf = trafilatura.extract(html, include_comments=False, include_tables=False, favor_precision=True) or ""
    except Exception:
        tf = ""
    tf_paras = text.paragraphs_from_blob(tf)
    if tf_paras and len(tf) >= 0.8 * len(body_ld) and len(tf_paras) >= 3:
        paras = tf_paras
    elif body_ld:
        paras = text.paragraphs_from_blob(o.get("articleBody", "")) or [body_ld]
    if free is False:
        paras = []                         # never show what the publisher marks as paid
    authors = o.get("author") if isinstance(o.get("author"), list) else [o.get("author")]
    names = [text.clean(a.get("name") if isinstance(a, dict) else a or "") for a in authors]
    names = [n for n in names if n and not n.lower().startswith(("http", "the "))]
    meta_author = soup.find("meta", attrs={"name": "author"})
    author = " and ".join(names) or text.clean(meta_author.get("content") if meta_author else "")
    og_title = soup.find("meta", property="og:title")
    og_desc = soup.find("meta", property="og:description")
    st = Story(
        headline=text.clean(o.get("headline") or (og_title.get("content") if og_title else "")),
        standfirst=text.clean(o.get("description") or (og_desc.get("content") if og_desc else "")),
        paragraphs=paras,
        byline=author,
        section=text.clean(_first(o.get("articleSection")) or ""),
        url=url, image_url=_image(o, soup), is_free=free,
        live="LiveBlogPosting" in (o.get("@type") if isinstance(o.get("@type"), list) else [o.get("@type")]))
    store.cache_put("article", key, {**st.__dict__, "_at": time.time()})
    return st


# ----------------------------------------------------------------------------- splash matching
STOP = set("a an and are as at be but by for from has have he her his in is it its of on or that the their this to "
           "was were which will with would after over under into than then there they not also been says said new "
           "leads front page paper papers reports report splash story".split())


def tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9£]+", (s or "").lower()) if w not in STOP and len(w) > 1}


def match_score(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def best_match(query: str, stories: list[Story], threshold: float = 0.35) -> int | None:
    scored = [(match_score(query, f"{s.headline} {s.standfirst}"), i) for i, s in enumerate(stories)]
    scored = [x for x in scored if len(tokens(query) & tokens(f"{stories[x[1]].headline} {stories[x[1]].standfirst}")) >= 2]
    if not scored:
        return None
    score, i = max(scored)
    return i if score >= threshold else None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
