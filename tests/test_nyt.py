"""The New York Times: Top Stories API adapter, printed-front PDF finder, and the morning OCR path."""
import io
import shutil
from datetime import date, datetime

import pytest
from PIL import Image

from app import clock, config
from app.builder import Builder
from app.ocr.splash import CONTINUED_RE
from app.papers import PAPERS
from app.sources import printfront
from app.sources.base import ShapeError, SourceUnavailable
from app.sources.nyt import NytAdapter, to_story
from conftest import FIX, FakeHttp, Resp

API = "https://api.nytimes.com/svc/topstories/v2/home.json"
PDF_URL = "https://static01.nyt.com/images/2026/10/21/nytfrontpage/scan.pdf"


def item(title, section="world", item_type="Article", abstract="", **kw):
    return {"title": title, "section": section, "subsection": kw.pop("subsection", ""), "item_type": item_type,
            "abstract": abstract, "byline": kw.pop("byline", "By A Reporter"),
            "url": kw.pop("url", f"https://www.nytimes.com/2026/10/21/{section}/{title[:12].lower().replace(' ', '-')}.html"),
            "multimedia": kw.pop("multimedia", []), **kw}


def api(items):
    return Resp(API, json_data={"status": "OK", "results": items})


STORIES = [
    item("Opinion | Europe Has Been Here Before", section="opinion"),
    item("Interactive: Where the Protests Spread", item_type="Interactive"),
    item("Riots Reignite French Debt Rout as Macron Struggles to Restore Order", subsection="europe",
         abstract="Investors dumped French government bonds as violent protests spread across the country.",
         byline="By Liz Alderman and Aurelien Breeden",
         multimedia=[{"url": "https://static01.nyt.com/s.jpg", "width": 600, "type": "image", "caption": "Small"},
                     {"url": "https://static01.nyt.com/l.jpg", "width": 1050, "type": "image",
                      "caption": "Riot police in Lyon.", "copyright": "Photo Agency"},
                     {"url": "https://static01.nyt.com/x.jpg", "width": 2048, "type": "image", "caption": "Huge"}]),
    item("U.S. Resumes Intelligence Sharing With Ukraine", section="us"),
    item("Fed Signals It Will Hold Rates Steady", section="business"),
    item("Britain Weighs Nationalizing Its Biggest Water Company", section="world"),
    item("A Drought Is Emptying Mexico City’s Reservoirs", section="world"),
    item("Mayor Unveils a Plan for Congestion on Bridges", section="nyregion"),
]


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(config, "NYT_API_KEY", "test-key")


def test_api_order_skips_opinion_and_non_articles(key):
    stories = NytAdapter(FakeHttp({API: api(STORIES)})).stories()
    assert stories[0].headline.startswith("Riots Reignite")
    assert all(not s.headline.startswith(("Opinion", "Interactive")) for s in stories)
    lead = stories[0]
    assert lead.byline == "Liz Alderman and Aurelien Breeden" and lead.section == "Europe"
    assert lead.image_url.endswith("l.jpg") and lead.caption == "Riot police in Lyon." and lead.credit == "Photo Agency"
    assert lead.is_free is False and lead.paragraphs == []
    assert [s.section for s in stories[1:]][:1] == ["U.S."] and stories[-1].section == "New York"


def test_evening_edition_is_the_web_front(key):
    ed = NytAdapter(FakeHttp({API: api(STORIES)})).build("pm", "2026-10-21")
    assert ed.paper == "nyt" and ed.kind == "web_front" and ed.lead.headline.startswith("Riots")
    assert len(ed.rail) == 5 and ed.continued_page is None


def test_no_key_is_an_outage(monkeypatch):
    monkeypatch.setattr(config, "NYT_API_KEY", "")
    with pytest.raises(SourceUnavailable):
        NytAdapter(FakeHttp({})).stories()


def test_too_few_stories_is_a_shape_error(key):
    with pytest.raises(ShapeError):
        NytAdapter(FakeHttp({API: api(STORIES[:4])})).stories()


def test_continued_line_reads_section_pages():
    assert CONTINUED_RE.search("Continued on Page A8").group(1).upper() == "A8"
    assert CONTINUED_RE.search("Continued on page 4").group(1) == "4"


# ----------------------------------------------------------------------------- printed front (PDF)
def test_finder_prefers_the_nyts_own_dated_pdf():
    http = FakeHttp({PDF_URL: Resp(PDF_URL, content=b"%PDF-1.4")})
    assert printfront.find_image(http, PAPERS["nyt"], date(2026, 10, 21)) == PDF_URL


def test_freedom_forum_copy_only_if_dated_today():
    ff = "https://cdn.freedomforum.org/dfp/pdf21/NY_NYT.pdf"
    old = FakeHttp({ff: Resp(ff, content=b"%PDF", headers={"Last-Modified": "Wed, 21 Sep 2026 04:00:00 GMT"})})
    assert printfront.find_image(old, PAPERS["nyt"], date(2026, 10, 21)) is None
    new = FakeHttp({ff: Resp(ff, content=b"%PDF", headers={"Last-Modified": "Wed, 21 Oct 2026 04:00:00 GMT"})})
    assert printfront.find_image(new, PAPERS["nyt"], date(2026, 10, 21)) == ff


def as_pdf(jpg: bytes) -> bytes:
    out = io.BytesIO()
    Image.open(io.BytesIO(jpg)).convert("L").save(out, "PDF", resolution=200)
    return out.getvalue()


def test_pdf_renders_at_full_width():
    jpg = (FIX / "fronts" / "ft-1400.jpg").read_bytes()
    img = printfront.to_image(as_pdf(jpg))
    assert abs(img.width - Image.open(io.BytesIO(jpg)).width) <= 2


needs_tess = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")


def morning(stories):
    pdf = as_pdf((FIX / "fronts" / "ft-1400.jpg").read_bytes())   # a synthetic broadsheet stands in for the NYT
    b = Builder(FakeHttp({PDF_URL: Resp(PDF_URL, content=pdf), API: api(stories),
                          "https://www.expressandstar.com/": SourceUnavailable("no PA today")}))
    return b.build_one(clock.edition_for(datetime(2026, 10, 21, 6, 1, tzinfo=clock.LONDON)), "nyt")


@needs_tess
def test_morning_edition_from_printed_pdf(key):
    ed = morning(STORIES)
    assert ed.kind == "print_front" and ed.layout == "full" and ed.continued_page == "4"
    assert "Investors" in ed.lead.paragraphs[0]
    assert ed.lead.url.endswith(".html") and "riots-reigni" in ed.lead.url      # QR goes to the matched story
    assert ed.note == PAPERS["nyt"].note_print
    assert all(not s.headline.startswith("Riots") for s in ed.rail)             # the lead isn't repeated


@needs_tess
def test_unmatched_splash_falls_back_to_the_web_front(key):
    others = [s for s in STORIES if not s["title"].startswith("Riots")]
    ed = morning(others)
    assert ed.kind == "web_front" and ed.lead.paragraphs == [] and ed.continued_page is None
