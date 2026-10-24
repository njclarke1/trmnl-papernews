"""The New York Times via its Top Stories API (free key; 500 calls/day, 5/min, shared across NYT APIs).

`home` returns the home page in the editors' own order: headline, abstract, byline, section, images.
No article text (paywalled), so the evening edition is a summary; the morning edition gets the
printed splash's opening by OCR of the NYT's own front-page PDF (app/sources/printfront.py).

The Article Search API's print metadata (`print_section` "A", `print_page` "1") names the stories on
the printed front. The OCR'd splash must match one of them; they also fill the morning rail.
Field names follow the documented API; verify against a real response in Phase 1.
"""
from __future__ import annotations

from datetime import date, timedelta

from .. import config, text
from ..model import Edition, Story
from .base import Http, ShapeError, SourceUnavailable, best_match, utc_now_iso

API = "https://api.nytimes.com/svc/topstories/v2/home.json"
SEARCH = "https://api.nytimes.com/svc/search/v2/articlesearch.json"
SECTIONS = {"us": "U.S.", "nyregion": "New York", "upshot": "The Upshot", "realestate": "Real Estate"}


def _image(item: dict) -> tuple[str, str, str]:
    media = [m for m in item.get("multimedia") or [] if m.get("url") and m.get("type", "image") == "image"]
    if not media:
        return "", "", ""
    big = sorted(media, key=lambda m: abs(int(m.get("width") or 0) - 1200))[0]
    return big["url"], text.clean(big.get("caption")), text.clean(big.get("copyright"))


def to_story(item: dict) -> Story:
    img, cap, credit = _image(item)
    byline = text.clean(item.get("byline"))
    byline = byline[3:] if byline.lower().startswith("by ") else byline
    section = item.get("subsection") or item.get("section") or ""
    return Story(headline=text.clean(item.get("title")), standfirst=text.clean(item.get("abstract")),
                 byline=byline, section=SECTIONS.get(section, section.title()),
                 url=item.get("url", ""), image_url=img, caption=cap, credit=credit, is_free=False)


def _search_image(doc: dict) -> str:
    mm = doc.get("multimedia")
    if isinstance(mm, dict):                                   # 2025+ shape: {"default": {...}, ...}
        url = (mm.get("default") or {}).get("url", "")
    else:                                                      # older shape: list, relative URLs
        best = sorted((m for m in mm or [] if m.get("url")), key=lambda m: abs(int(m.get("width") or 0) - 1200))
        url = best[0]["url"] if best else ""
    return url if not url or url.startswith("http") else f"https://static01.nyt.com/{url.lstrip('/')}"


def a1_story(doc: dict) -> tuple[Story, str]:
    """(story, text to match a print splash against). The print headline is kept for matching."""
    h = doc.get("headline") or {}
    byline = text.clean((doc.get("byline") or {}).get("original"))
    byline = byline[3:] if byline.lower().startswith("by ") else byline
    st = Story(headline=text.clean(h.get("main")), standfirst=text.clean(doc.get("abstract")), byline=byline,
               section=text.clean(doc.get("section_name")), url=doc.get("web_url", ""),
               image_url=_search_image(doc), is_free=False)
    match_on = " ".join(text.clean(x) for x in (h.get("print_headline"), h.get("main"), doc.get("lead_paragraph"),
                                                doc.get("abstract")) if x)
    return st, match_on


def _ok_lead(item: dict) -> bool:
    return (item.get("item_type", "Article") == "Article" and item.get("section") != "opinion"
            and bool(item.get("title")))


class NytAdapter:
    def __init__(self, http: Http):
        self.http = http

    def stories(self) -> list[Story]:
        if not config.NYT_API_KEY:
            raise SourceUnavailable("NYT_API_KEY not set")
        r = self.http.get(API, params={"api-key": config.NYT_API_KEY})
        try:
            items = r.json()["results"]
        except (KeyError, ValueError) as e:
            raise ShapeError("NYT: no results in Top Stories response") from e
        stories = [to_story(i) for i in items if _ok_lead(i)]
        if len(stories) < 5:
            raise ShapeError(f"NYT: only {len(stories)} usable top stories")
        return stories

    def front_page(self, day: date) -> list[tuple[Story, str]]:
        """Stories on page A1 of `day`'s paper. Most are published online the day before."""
        r = self.http.get(SEARCH, params={"api-key": config.NYT_API_KEY, "begin_date": f"{day - timedelta(days=1):%Y%m%d}",
                                          "end_date": f"{day:%Y%m%d}", "fq": 'print_page:1 AND print_section:"A"',
                                          "sort": "newest"})
        try:
            docs = (r.json().get("response") or {}).get("docs") or []
        except ValueError:
            return []
        # the filter is re-checked here in case the API ignores or reinterprets fq
        return [a1_story(d) for d in docs if str(d.get("print_page", "")) == "1"
                and str(d.get("print_section") or "").upper() == "A" and d.get("web_url")]

    def build(self, slot_kind: str, day_iso: str, review=None, print_lead: Story | None = None) -> Edition:
        stories = self.stories()
        a1: list[tuple[Story, str]] = []
        if slot_kind == "am" and print_lead is not None:
            try:
                a1 = self.front_page(date.fromisoformat(day_iso))
            except SourceUnavailable:
                a1 = []
        if print_lead is None:
            return Edition("nyt", "web_front", stories[0], stories[1:6], built_at=utc_now_iso())

        # No PA round-up covers the NYT. The OCR'd splash must be one of the A1 stories (print metadata);
        # if Article Search is unavailable, one of today's top stories. Print and web headlines differ,
        # so match on headline + deck + opening.
        opening = print_lead.paragraphs[0] if print_lead.paragraphs else ""
        query = f"{print_lead.headline} {print_lead.standfirst} {opening}"
        if a1:
            pool = [Story(headline=m) for _, m in a1]
            idx = best_match(query, pool)
            hint = a1[idx][0] if idx is not None else None
        else:
            idx = best_match(query, stories)
            hint = stories[idx] if idx is not None else None
        if hint is None:                        # didn't match: the web front
            return Edition("nyt", "web_front", stories[0], stories[1:6], built_at=utc_now_iso())
        top = {s.url: s for s in stories}       # the top-stories copy has the better image and caption
        hint = top.get(hint.url, hint)
        for f in ("url", "image_url", "caption", "credit", "section"):
            if not getattr(print_lead, f):
                setattr(print_lead, f, getattr(hint, f))
        # rail: the rest of A1 in the editors' web order, then the web front
        rank = {s.url: i for i, s in enumerate(stories)}
        others = sorted((top.get(s.url, s) for s, _ in a1 if s.url != hint.url), key=lambda s: rank.get(s.url, 99))
        seen = {hint.url} | {s.url for s in others}
        rail = others + [s for s in stories if s.url not in seen]
        return Edition("nyt", "print_front", print_lead, rail[:5], built_at=utc_now_iso(), rail_front=min(len(others), 5))
