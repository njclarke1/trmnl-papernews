"""Printed front pages → the splash's text (and, for print-only papers, the other front headlines) by OCR.

Used in the morning edition only. Sources, by paper (front_finder):
- nytpdf          the NYT's own dated PDF (Freedom Forum copy if Last-Modified says it is today's)
- frontpages      frontpages.com (FT). Files are dated by *upload* time, the evening before:
                  `/g/2026/10/23/financial-times-234226…` is the 24 October paper.
- tomorrowspapers tomorrowspapers.co.uk (The Times, The Telegraph). Its edition pages don't name
                  the paper, so each image's masthead band is OCR'd to identify it; the map is
                  built once per morning and cached.
The image must be at least 1,000 px wide and the extraction must pass every gate in app.ocr.splash;
otherwise no splash (summary/briefing layout, or the round-up's headline for print-only papers).
"""
from __future__ import annotations

import io
import re
import urllib.parse as up
from dataclasses import dataclass, field
from datetime import date, timedelta

from bs4 import BeautifulSoup
from PIL import Image

from .. import config, store
from ..model import Story, story_from
from ..papers import PAPERS, Paper
from .base import Http, SourceUnavailable

MIN_WIDTH = 1000
PDF_SCALE = 200 / 72                       # render PDFs at 200 dpi (a broadsheet page ≈ 2,400 px wide)
TP = "https://www.tomorrowspapers.co.uk/"
TP_SCAN = 30                               # edition pages to look through each morning
FP_STAMP = re.compile(r"/g/(\d{4})/(\d{2})/(\d{2})/[a-z0-9-]+?-(\d{2})(\d{2})(\d{2})")

# masthead band text → paper. Sunday titles are other papers and never match.
MASTHEADS: list[tuple[str, re.Pattern]] = [
    ("ft", re.compile(r"financial\s*times|\bft\.com\b")),
    ("times", re.compile(r"(?<!sunday )(?<!york )(?<!financial )\bthe\s*times\b(?! of)|thetimes\.(?:com|co\.uk)")),
    ("telegraph", re.compile(r"(?<!sunday )\btelegraph\b|telegraph\.co\.uk")),
    ("guardian", re.compile(r"\bthe\s*guardian\b|theguardian\.com")),
    ("i", re.compile(r"inews\.co\.uk")),
]
NOT_DAILY = re.compile(r"\bsunday\b|\bobserver\b|\bon\s+sunday\b")


# ----------------------------------------------------------------------------- finding the image
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


def frontpages_is_for(url: str, day: date) -> bool:
    """frontpages.com files a paper under its upload time: the night before (18:00 on) or the day."""
    m = FP_STAMP.search(url or "")
    if not m:
        return False
    up_day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return up_day == day or (up_day == day - timedelta(days=1) and int(m.group(4)) >= 18)


def _frontpages(http: Http, paper: Paper, day: date) -> str | None:
    html = http.get(f"https://www.frontpages.com/{paper.front_slug}").text
    og = BeautifulSoup(html, "lxml").find("meta", property="og:image")
    url = og.get("content") if og else ""
    return url if frontpages_is_for(url, day) else None


def identify(img: Image.Image) -> str | None:
    """Which paper is this front? OCR of the masthead band (top 16%)."""
    import os
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = os.environ.get("TESSERACT_CMD", "tesseract")
    band = img.convert("L").crop((0, 0, img.width, int(img.height * 0.16)))
    if band.width < 2000:
        band = band.resize((band.width * 2, band.height * 2), Image.LANCZOS)
    return masthead_key(pytesseract.image_to_string(band, config="--psm 3"))


def masthead_key(band_text: str) -> str | None:
    txt = re.sub(r"\s+", " ", band_text).lower()
    if NOT_DAILY.search(txt):
        return None
    for key, pat in MASTHEADS:
        if pat.search(txt):
            return key
    return None


def tp_map(http: Http, day: date) -> dict[str, str]:
    """{paper key: front image URL} for `day` from tomorrowspapers.co.uk, built once and cached."""
    hit = store.cache_get("tp-map", day.isoformat())
    if hit is not None:
        return hit
    idx = http.get(TP).text
    entries = [up.urljoin(TP, a["href"]) for a in BeautifulSoup(idx, "lxml").find_all("a", href=True)
               if f"/{day.isoformat()}-" in a["href"]]
    entries = list(dict.fromkeys(entries))[:TP_SCAN]
    if not entries:
        return {}                                  # not published yet: no cache, retries look again
    found: dict[str, str] = {}
    for u in entries:
        try:
            soup = BeautifulSoup(http.get(u).text, "lxml")
            srcs = [i.get("src") or "" for i in soup.find_all("img")]
            img_url = next((s for s in srcs if "/wp-content/uploads/" in s and day.isoformat() in s), "")
            if not img_url:
                continue
            key = identify(to_image(http.get(img_url, binary=True).content))
        except Exception:                          # one bad entry never spoils the map
            continue
        if key and key not in found:
            found[key] = img_url
    store.cache_put("tp-map", day.isoformat(), found)
    return found


def find_image(http: Http, paper: Paper, day: date) -> str | None:
    if paper.front_finder == "nytpdf":
        return nyt_pdf(http, day)
    url = None
    if paper.front_finder == "frontpages":
        try:
            url = _frontpages(http, paper, day)
        except SourceUnavailable:
            url = None
    if url:
        return url
    try:
        return tp_map(http, day).get(paper.key)
    except SourceUnavailable:
        return None


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


# ----------------------------------------------------------------------------- reading it
@dataclass
class Front:
    story: Story | None = None              # the splash, if it passed every gate
    continued: str | None = None
    others: list[str] = field(default_factory=list)   # other headlines on the front (print-only papers)


def front(http: Http, paper: Paper, day: date, expected_headline: str = "",
          expected_standfirst: str = "") -> Front | None:
    """The printed front, read. None if there is no image yet (retries look again). Results —
    including failures — are cached per paper per day, so a bad image is OCR'd once, not every retry."""
    if not (paper.print_front and config.OCR_ENABLED):
        return None
    # a result made without the round-up cross-check must not block a checked run once it arrives
    key = f"{paper.key}-{day.isoformat()}-{'checked' if (expected_headline or expected_standfirst) else 'unchecked'}"
    cached = store.cache_get("ocr", key)
    if cached is not None:
        return Front(story_from(cached["story"]) if cached.get("story") else None,
                     cached.get("continued"), cached.get("others") or [])
    try:
        url = find_image(http, paper, day)
        if not url:
            return None
        raw = http.get(url, binary=True).content
    except SourceUnavailable:
        return None
    try:
        img = to_image(raw)
        if img.width < MIN_WIDTH:
            store.cache_put("ocr", key, {"story": None, "why": f"image only {img.width}px wide"})
            return Front()
        from ..ocr.splash import extract_splash, front_headlines, ocr_lines
        lines, _ = ocr_lines(img)
        r = extract_splash(img, paper_name=paper.name, expected_headline=expected_headline,
                           expected_standfirst=expected_standfirst, lines=lines)
        others = front_headlines(lines, splash=r.headline or expected_headline, paper_name=paper.name) \
            if paper.source == "print" else []
    except Exception as e:                   # any decode/OCR failure ⇒ no splash, never a failed paper
        store.cache_put("ocr", key, {"story": None, "why": f"{type(e).__name__}: {e}"})
        return Front()
    st = Story(headline=r.headline, standfirst=r.deck, paragraphs=r.paragraphs, byline=r.byline,
               is_free=True) if r.ok else None
    store.cache_put("ocr", key, {"story": st.__dict__ if st else None, "continued": r.continued_page if st else None,
                                 "others": others, "why": "" if r.ok else r.reason, "headline_seen": r.headline})
    return Front(st, r.continued_page if st else None, others)


def splash(http: Http, paper: Paper, day: date, expected_headline: str = "",
           expected_standfirst: str = "") -> tuple[Story, str | None] | None:
    """(story, continued_page) for papers whose web front supplies the rest (FT, NYT)."""
    f = front(http, paper, day, expected_headline, expected_standfirst)
    return (f.story, f.continued) if f and f.story else None
