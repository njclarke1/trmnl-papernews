"""The Guardian via its Content API (free Developer key: 500 calls/day, body text included).

Editors' picks on /uk give the website front in the editors' own order. For the morning edition the
API's own print metadata gives the printed front: articles in today's newspaper edition, main book,
page 1 (`newspaperPageNumber`, `newspaperEditionDate`, `newspaper-book` tag). The round-up's splash
picks the lead among them. If the print metadata is missing, the round-up's splash is matched against
the picks (and a search) as before.
Shapes follow the documented API; verify against a real response in Phase 1.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from .. import config, text
from ..model import Edition, Story
from .base import Http, ShapeError, SourceUnavailable, best_match, utc_now_iso

API = "https://content.guardianapis.com"
FIELDS = "headline,standfirst,trailText,byline,body,thumbnail,isPremiumContent"


def _params(**kw) -> dict:
    return {"api-key": config.GUARDIAN_API_KEY, "show-fields": FIELDS, "show-elements": "image",
            "show-tags": "tone", **kw}


def _image(item: dict) -> tuple[str, str, str]:
    best, cap, credit = "", "", ""
    for el in item.get("elements", []) or []:
        if el.get("type") != "image" or el.get("relation") not in ("main", "thumbnail", None):
            continue
        assets = sorted((a for a in el.get("assets", []) if a.get("typeData", {}).get("width")),
                        key=lambda a: abs(int(a["typeData"]["width"]) - 1200))
        if assets:
            a = assets[0]
            best, cap, credit = a.get("file", ""), a["typeData"].get("caption", ""), a["typeData"].get("credit", "")
            if el.get("relation") == "main":
                break
    return best or (item.get("fields") or {}).get("thumbnail", ""), text.clean(cap), text.clean(credit)


def to_story(item: dict) -> Story:
    f = item.get("fields") or {}
    body_html = f.get("body") or ""
    paras = [text.clean(p.get_text(" ")) for p in BeautifulSoup(body_html, "lxml").find_all("p")] if body_html else []
    img, cap, credit = _image(item)
    tones = {t.get("id") for t in item.get("tags", []) or []}
    return Story(headline=text.clean(f.get("headline") or item.get("webTitle")),
                 standfirst=text.clean(f.get("standfirst") or f.get("trailText")),
                 paragraphs=[p for p in paras if p], byline=text.clean(f.get("byline")),
                 section=item.get("sectionName", ""), url=item.get("webUrl", ""), image_url=img,
                 caption=cap, credit=credit, is_free=True,
                 live="tone/minutebyminute" in tones or item.get("type") == "liveblog")


PRINT_FIELDS = FIELDS + ",newspaperPageNumber,newspaperEditionDate,wordcount,publication"
MAIN_BOOK = "theguardian/mainsection"


def on_front(item: dict, day_iso: str) -> bool:
    """Page 1 of the main book of `day_iso`'s Guardian (supplements number their own pages from 1)."""
    f = item.get("fields") or {}
    books = {t.get("id") for t in item.get("tags", []) or [] if t.get("type") == "newspaper-book"}
    return (str(f.get("newspaperPageNumber", "")) == "1"
            and str(f.get("newspaperEditionDate", "")).startswith(day_iso)
            and f.get("publication", "The Guardian") == "The Guardian"
            and MAIN_BOOK in books)


def _ok_lead(item: dict) -> bool:
    tones = {t.get("id") for t in item.get("tags", []) or []}
    return item.get("type") in ("article", "liveblog") and "tone/comment" not in tones


class GuardianAdapter:
    def __init__(self, http: Http):
        self.http = http

    def picks(self) -> list[dict]:
        if not config.GUARDIAN_API_KEY:
            raise SourceUnavailable("GUARDIAN_API_KEY not set")
        r = self.http.get(f"{API}/uk", params=_params(**{"show-editors-picks": "true", "page-size": 20}))
        try:
            picks = r.json()["response"]["editorsPicks"]
        except (KeyError, ValueError) as e:
            raise ShapeError("Guardian: no editorsPicks in response") from e
        if len(picks) < 4:
            raise ShapeError(f"Guardian: only {len(picks)} editors' picks")
        return picks

    def search(self, query: str, day_iso: str) -> list[dict]:
        r = self.http.get(f"{API}/search", params=_params(q=" ".join(list(query.split())[:8]), **{
            "from-date": day_iso, "order-by": "relevance", "page-size": 5}))
        return r.json().get("response", {}).get("results", [])

    def front_page(self, day_iso: str) -> list[dict]:
        """Today's printed page 1, from the API's print metadata. [] if it isn't there (yet).
        Filtered to the main book; a Saturday edition runs to 100+ articles, so page through."""
        front: list[dict] = []
        for page in range(1, 4):
            r = self.http.get(f"{API}/search", params={**_params(**{
                "use-date": "newspaper-edition", "from-date": day_iso, "to-date": day_iso, "page-size": 50,
                "order-by": "newest", "tag": MAIN_BOOK, "page": page}),
                "show-fields": PRINT_FIELDS, "show-tags": "tone,newspaper-book"})
            try:
                resp = r.json()["response"]
                results = resp["results"]
            except (KeyError, ValueError):
                break
            front += [i for i in results if on_front(i, day_iso)]
            if page >= int(resp.get("pages") or 1):
                break
        return front

    def build(self, slot_kind: str, day_iso: str, review=None) -> Edition:
        if slot_kind == "am":
            try:
                front = self.front_page(day_iso)
            except SourceUnavailable:
                front = []
            if front:
                return self._from_front(front, review)
        items = [i for i in self.picks()]
        leads = [i for i in items if _ok_lead(i)]
        if not leads:
            raise ShapeError("Guardian: no news article among editors' picks")
        stories = [to_story(i) for i in leads]
        kind, printed = "web_front", ""
        if slot_kind == "am" and review is not None:
            idx = best_match(review.query, stories)
            if idx is None:
                found = [to_story(i) for i in self.search(review.query, day_iso) if _ok_lead(i)]
                j = best_match(review.query, found)
                if j is not None:
                    stories.insert(0, found[j]); idx = 0
            if idx is not None:
                stories.insert(0, stories.pop(idx))
                kind, printed = "print_front", review.quoted
        lead = stories[0]
        if printed and len(printed) > 15:
            lead.headline = printed
        return Edition("guardian", kind, lead, stories[1:6], note="", built_at=utc_now_iso())

    def _from_front(self, front: list[dict], review) -> Edition:
        """The printed page 1. Lead: the round-up's splash if it matches; else the longest news story
        (the splash carries the most words on the page)."""
        news = [i for i in front if _ok_lead(i)]
        if not news:
            raise ShapeError("Guardian: page 1 has no news article")
        news.sort(key=lambda i: -int((i.get("fields") or {}).get("wordcount") or 0))
        stories = [to_story(i) for i in news]
        idx = best_match(review.query, stories) if review is not None else None
        if idx is not None:
            stories.insert(0, stories.pop(idx))
            if review.quoted and len(review.quoted) > 15:
                stories[0].headline = review.quoted
        rail = stories[1:]
        try:                                    # fill the rail from the website front
            seen = {s.url for s in stories}
            rail += [s for s in (to_story(i) for i in self.picks() if _ok_lead(i)) if s.url not in seen]
        except (SourceUnavailable, ShapeError):
            pass
        for s in rail:
            s.paragraphs = []
        return Edition("guardian", "print_front", stories[0], rail[:5], note="", built_at=utc_now_iso())
