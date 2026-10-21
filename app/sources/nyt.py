"""The New York Times via its Top Stories API (free key; 500 calls/day, 5/min, shared across NYT APIs).

`home` returns the home page in the editors' own order: headline, abstract, byline, section, images.
No article text (paywalled), so the evening edition is a summary; the morning edition gets the
printed splash's opening by OCR of the NYT's own front-page PDF (app/sources/printfront.py).
Field names follow the documented API; verify against a real response in Phase 1.
"""
from __future__ import annotations

from .. import config, text
from ..model import Edition, Story
from .base import Http, ShapeError, SourceUnavailable, best_match, utc_now_iso

API = "https://api.nytimes.com/svc/topstories/v2/home.json"
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

    def build(self, slot_kind: str, day_iso: str, review=None, print_lead: Story | None = None) -> Edition:
        stories = self.stories()
        kind = "web_front"
        # No PA round-up covers the NYT, so the API is the cross-check: the OCR'd splash must be one of
        # today's top stories (print and web headlines differ, so match on headline + deck + opening).
        idx = None
        if print_lead is not None:
            opening = print_lead.paragraphs[0] if print_lead.paragraphs else ""
            idx = best_match(f"{print_lead.headline} {print_lead.standfirst} {opening}", stories)
        if idx is not None:
            hint = stories.pop(idx)
            for f in ("url", "image_url", "caption", "credit", "section"):
                if not getattr(print_lead, f):
                    setattr(print_lead, f, getattr(hint, f))
            lead, kind = print_lead, "print_front"
        else:
            lead = stories.pop(0)               # no print splash, or it didn't match: the web front
        return Edition("nyt", kind, lead, stories[:5], built_at=utc_now_iso())
