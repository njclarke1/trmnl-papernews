"""Generic homepage + article adapter (BBC, FT, Times, Independent, i, Telegraph).

Order comes from the homepage, found by article URL *pattern* (not CSS selectors, which change with
every redesign). Fewer than 5 matching links ⇒ fall back to RSS order with kind `latest`.
Bodies come from the article page a normal visitor gets; the publisher's free flag decides.
"""
from __future__ import annotations

import re
import urllib.parse as up

from bs4 import BeautifulSoup

from .. import text
from ..model import Edition, Story
from ..papers import Paper
from . import papers_review
from .base import Http, ShapeError, SourceUnavailable, best_match, read_article, utc_now_iso

MIN_LINKS = 5
RAIL_FETCH = 5


def homepage_links(html: str, base_url: str, pattern: str) -> list[tuple[str, str]]:
    base = up.urlparse(base_url)
    pat = re.compile(pattern)
    seen, out = set(), []
    for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
        u = up.urlparse(up.urljoin(base_url, a["href"]))
        if u.netloc.removeprefix("www.") != base.netloc.removeprefix("www."):
            continue
        path = u.path if pattern.endswith("/?$") else u.path.rstrip("/")
        if pat.match(path) and path not in seen:
            seen.add(path)
            out.append((f"{u.scheme}://{u.netloc}{u.path}", text.clean(a.get_text(" "))))
    return out


def bigger_thumb(url: str) -> str:
    """Feed thumbnails are small (FT: width=580); ask the image service for 1200 px."""
    return re.sub(r"([?&]width=)\d+", r"\g<1>1200", url) if "images.ft.com" in url else url


def rss_items(xml: str) -> list[Story]:
    soup = BeautifulSoup(xml, "xml")
    out = []
    for it in soup.find_all("item"):
        thumb = it.find("media:thumbnail") or it.find("thumbnail") or it.find("media:content") or it.find("content")
        out.append(Story(headline=text.clean(it.title.text if it.title else ""),
                         standfirst=text.clean(it.description.text if it.description else ""),
                         url=text.clean(it.link.text if it.link else ""),
                         image_url=bigger_thumb(thumb.get("url")) if thumb is not None and thumb.get("url") else ""))
    return [s for s in out if s.headline and s.url]


def _merge(article: Story, hint: Story) -> Story:
    """Article page wins; fill gaps from the homepage/RSS hint."""
    for f in ("headline", "standfirst", "image_url", "section"):
        if not getattr(article, f) and getattr(hint, f):
            setattr(article, f, getattr(hint, f))
    return article


class WebAdapter:
    def __init__(self, paper: Paper, http: Http):
        self.p, self.http = paper, http

    def candidates(self) -> tuple[str, list[Story]]:
        rss: list[Story] = []
        for u in self.p.rss:
            try:
                rss = rss_items(self.http.get(u).text)
                if rss:
                    break
            except SourceUnavailable:
                continue
        by_url = {s.url.split("?")[0].rstrip("/"): s for s in rss}
        try:
            r = self.http.get(self.p.home)
            base = getattr(r, "url", "") or self.p.home        # after redirects (spectator.co.uk → .com)
            links = homepage_links(r.text, base if base.startswith("http") else self.p.home, self.p.article_re)
        except SourceUnavailable:
            links = []
        if len(links) >= MIN_LINKS:
            stories = [by_url.get(u.rstrip("/"), Story(headline=t, url=u)) for u, t in links]
            return "web_front", stories
        if rss:
            return "latest", rss
        raise SourceUnavailable(f"{self.p.key}: no homepage links and no RSS")

    def build(self, slot_kind: str, day_iso: str, review=None, print_lead: Story | None = None) -> Edition:
        kind, cands = self.candidates()
        from_feed = kind == "latest"          # RSS order is not a front: the rail is then just 'more stories'
        if slot_kind == "am" and review is not None:
            idx = best_match(review.query, cands)
            if idx is not None:
                cands.insert(0, cands.pop(idx))
                kind = "print_front"          # the round-up identifies the splash; the paper's own headline is shown
        if print_lead is not None:                       # OCR'd printed splash (FT, Times)
            idx = best_match(print_lead.headline, cands, threshold=0.5)
            hint = cands.pop(idx) if idx is not None else Story(headline=print_lead.headline)
            lead = _merge(print_lead, hint)
            lead.url = lead.url or hint.url
            kind = "print_front"
        else:
            try:
                lead = _merge(read_article(self.http, cands[0].url, max_age_h=0), cands[0]) if cands[0].url else cands[0]
            except SourceUnavailable:          # article pages blocked (FT): headline and standfirst from the feed
                lead = cands[0]
            cands = cands[1:]
        if lead.live and lead.body_chars < 1500:
            lead.paragraphs = []                          # live blogs: summary layout, not a wall of updates
        rail = []
        for c in cands[:RAIL_FETCH]:
            try:
                s = _merge(read_article(self.http, c.url), c)
            except SourceUnavailable:
                s = c
            s.paragraphs = []                             # rail never carries body text
            if s.headline:
                rail.append(s)
        if not lead.headline or len(rail) < 3:
            raise ShapeError(f"{self.p.key}: lead missing or only {len(rail)} other stories")
        return Edition(self.p.key, kind, lead, rail, built_at=utc_now_iso(), rail_front=0 if from_feed else -1)
