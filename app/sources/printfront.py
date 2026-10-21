"""Printed front page → splash text by OCR, for papers with no free web text (FT, The Times).

Morning edition only. The image must be tonight's (date checked), at least 1,000 px wide, and the
extraction must pass every gate in app.ocr.splash; otherwise None and the paper uses summary layout.
"""
from __future__ import annotations

import io
import re
import urllib.parse as up
from datetime import date

from bs4 import BeautifulSoup
from PIL import Image

from .. import config, store
from ..model import Story
from ..papers import Paper
from .base import Http, SourceUnavailable

MIN_WIDTH = 1000


def find_image(http: Http, paper: Paper, day: date) -> str | None:
    if paper.front_finder == "frontpages":
        html = http.get(f"https://www.frontpages.com/{paper.front_slug}").text
        og = BeautifulSoup(html, "lxml").find("meta", property="og:image")
        url = og.get("content") if og else ""
        return url if url and f"/{day:%Y/%m/%d}/" in url else None
    # tomorrowspapers.co.uk: nightly entries named YYYY-MM-DD-xxxxxx
    idx = http.get("https://www.tomorrowspapers.co.uk/").text
    entries = [up.urljoin("https://www.tomorrowspapers.co.uk/", a["href"])
               for a in BeautifulSoup(idx, "lxml").find_all("a", href=True) if f"/{day.isoformat()}-" in a["href"]]
    want = re.compile(r"\bthe times\b(?!.*(financial|sunday|new york|india|malta))", re.I)
    for u in list(dict.fromkeys(entries))[:30]:
        soup = BeautifulSoup(http.get(u).text, "lxml")
        if soup.title and want.search(soup.title.get_text()):
            og = soup.find("meta", property="og:image")
            imgs = [i.get("src") for i in soup.find_all("img") if "uploads" in (i.get("src") or "")]
            return (og.get("content") if og else None) or (imgs[0] if imgs else None)
    return None


def splash(http: Http, paper: Paper, day: date, expected_headline: str = "",
           expected_standfirst: str = "") -> tuple[Story, int | None] | None:
    """(story, continued_page) or None. Results — including failures — are cached per paper per day,
    so a bad image is OCR'd once, not on every retry."""
    if not (paper.print_front and config.OCR_ENABLED):
        return None
    key = f"{paper.key}-{day.isoformat()}"
    cached = store.cache_get("ocr", key)
    if cached is not None:
        return (Story(**cached["story"]), cached.get("continued")) if cached.get("story") else None
    try:
        url = find_image(http, paper, day)
        if not url:
            return None                      # not published yet: no cache entry, so retries look again
        img = Image.open(io.BytesIO(http.get(url, binary=True).content))
    except (SourceUnavailable, OSError):
        return None
    if img.width < MIN_WIDTH:
        store.cache_put("ocr", key, {"story": None, "why": f"image only {img.width}px wide"})
        return None
    from ..ocr.splash import extract_splash
    r = extract_splash(img, paper_name=paper.name, expected_headline=expected_headline,
                       expected_standfirst=expected_standfirst)
    if not r.ok:
        store.cache_put("ocr", key, {"story": None, "why": r.reason, "headline_seen": r.headline})
        return None
    st = Story(headline=r.headline, standfirst=r.deck, paragraphs=r.paragraphs, byline=r.byline, is_free=True)
    store.cache_put("ocr", key, {"story": st.__dict__, "continued": r.continued_page})
    return st, r.continued_page
