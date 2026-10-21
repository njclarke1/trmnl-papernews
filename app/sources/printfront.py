"""Printed front page → splash text by OCR, for papers with no free web text (FT, The Times, the NYT).

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
from ..model import Story, story_from
from ..papers import Paper
from .base import Http, SourceUnavailable

MIN_WIDTH = 1000
PDF_SCALE = 200 / 72                       # render PDFs at 200 dpi (a broadsheet page ≈ 2,400 px wide)


def nyt_pdf(http: Http, day: date) -> str | None:
    """The NYT publishes its own front page as a PDF at a dated URL; Freedom Forum mirrors it daily
    at an undated one, so that copy is only trusted if Last-Modified says it is today's."""
    own = f"https://static01.nyt.com/images/{day:%Y/%m/%d}/nytfrontpage/scan.pdf"
    try:
        http.get(own, binary=True, stream=True).close()
        return own
    except SourceUnavailable:
        pass
    ff = f"https://cdn.freedomforum.org/dfp/pdf{day.day}/NY_NYT.pdf"
    r = http.get(ff, binary=True, stream=True)
    r.close()
    try:
        from email.utils import parsedate_to_datetime
        stamp = parsedate_to_datetime(r.headers.get("Last-Modified", ""))
    except (TypeError, ValueError):
        return None
    return ff if abs((stamp.date() - day).days) <= 1 else None


def to_image(raw: bytes) -> Image.Image:
    if raw[:5] == b"%PDF-":
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(raw)
        try:
            return pdf[0].render(scale=PDF_SCALE, grayscale=True).to_pil()
        finally:
            pdf.close()
    img = Image.open(io.BytesIO(raw))
    img.load()                             # decode now, so a truncated/bomb image fails here
    return img


def find_image(http: Http, paper: Paper, day: date) -> str | None:
    if paper.front_finder == "nytpdf":
        return nyt_pdf(http, day)
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
           expected_standfirst: str = "") -> tuple[Story, str | None] | None:
    """(story, continued_page) or None. Results — including failures — are cached per paper per day,
    so a bad image is OCR'd once, not on every retry."""
    if not (paper.print_front and config.OCR_ENABLED):
        return None
    # a result made without the PA cross-check must not block a checked run once the round-up arrives
    key = f"{paper.key}-{day.isoformat()}-{'checked' if (expected_headline or expected_standfirst) else 'unchecked'}"
    cached = store.cache_get("ocr", key)
    if cached is not None:
        return (story_from(cached["story"]), cached.get("continued")) if cached.get("story") else None
    try:
        url = find_image(http, paper, day)
        if not url:
            return None                      # not published yet: no cache entry, so retries look again
        raw = http.get(url, binary=True).content
    except SourceUnavailable:
        return None                          # transient: no cache entry, retries look again
    try:
        img = to_image(raw)
        if img.width < MIN_WIDTH:
            store.cache_put("ocr", key, {"story": None, "why": f"image only {img.width}px wide"})
            return None
        from ..ocr.splash import extract_splash
        r = extract_splash(img, paper_name=paper.name, expected_headline=expected_headline,
                           expected_standfirst=expected_standfirst)
    except Exception as e:                   # any decode/OCR failure ⇒ summary layout, never a failed paper
        store.cache_put("ocr", key, {"story": None, "why": f"{type(e).__name__}: {e}"})
        return None
    if not r.ok:
        store.cache_put("ocr", key, {"story": None, "why": r.reason, "headline_seen": r.headline})
        return None
    st = Story(headline=r.headline, standfirst=r.deck, paragraphs=r.paragraphs, byline=r.byline, is_free=True)
    store.cache_put("ocr", key, {"story": st.__dict__, "continued": r.continued_page})
    return st, r.continued_page
