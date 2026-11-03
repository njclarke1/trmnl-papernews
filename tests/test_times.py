"""The Times as a hybrid source (3 Nov probe): homepage order via a plainly named reader identity,
paywalled articles as summaries, sport left out, morning splash by OCR, nothing on Sundays."""
from datetime import datetime

import requests

from app import clock, config, store
from app.builder import Builder
from app.model import Story
from app.papers import PAPERS
from app.sources import printfront
from app.sources.base import Http, SourceUnavailable
from app.sources.web import WebAdapter, homepage_links, section_from_url
from conftest import FIX, FakeHttp, Resp

HOME = (FIX / "web" / "times-home.html").read_text()
ARTICLE = (FIX / "web" / "times-article.html").read_text()
TIMES = PAPERS["times"]
BYLINE = "Geraldine Scott, Emma Yeomans, Oliver Wright and Aubrey Allegretti"


def at(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=clock.LONDON)


def test_homepage_order_with_headline_text_and_no_sport():
    links = homepage_links(HOME + '<a href="/sport/football/article/nuno-sacked-nxhccfd3b">Nuno sacked</a>',
                           TIMES.home, TIMES.article_re)
    assert links[0] == ("https://www.thetimes.com/uk/politics/article/yougov-poll-times-tories-labour-reform-g3vqmgc0l",
                        "Tories two points off Labour in best polling since election")
    assert links[1][1] == "Mahmood backs down on immigration curbs"      # picture link first, headline link second
    assert not any("/sport/" in u for u, _ in links) and len(links) >= 10


def test_section_from_url():
    assert section_from_url("https://www.thetimes.com/uk/politics/article/x-1") == "Politics"
    assert section_from_url("https://www.thetimes.com/us/news-today/article/x-1") == "News"
    assert section_from_url("https://spectator.com/article/x/") == ""


class TimesHttp(FakeHttp):
    """Every Times article answers with the saved (paid) article page; the homepage with the saved homepage."""
    def get(self, url, *, binary=False, **kw):
        self.calls.append(url)
        if url.rstrip("/") == "https://www.thetimes.com":
            return Resp(url, HOME)
        if "/article/" in url:
            return Resp(url, ARTICLE)
        raise SourceUnavailable(f"unmapped {url}")


def test_evening_is_homepage_order_and_summary_only():
    ed = WebAdapter(TIMES, TimesHttp({})).build("pm", "2026-11-03")
    assert ed.kind == "web_front" and "yougov" in ed.lead.url
    assert ed.lead.paragraphs == [] and ed.lead.is_free is False            # publisher marks it paid
    assert ed.lead.byline == BYLINE and ed.lead.image_url.startswith("https://www.thetimes.com/imageserver/")
    assert ed.lead.section == "Politics"                                     # not the JSON-LD's "TheTimes"
    assert len(ed.rail) >= 3


def test_morning_print_splash_matched_to_homepage(monkeypatch):
    splash = Story("Mahmood backs down on immigration curbs", "Carers spared", ["Para " * 60] * 4, is_free=True)
    monkeypatch.setattr(printfront, "splash", lambda *a, **k: (splash, "4"))
    b = Builder(TimesHttp({}))
    monkeypatch.setattr(b, "_review", lambda slot: {})
    ed = b.build_one(clock.edition_for(at("2026-11-03 06:01")), "times")
    assert ed.kind == "print_front" and "mbvfl9qq8" in ed.lead.url and ed.lead.paragraphs
    assert ed.note == TIMES.note_print and ed.continued_page == "4"


def test_times_gets_the_named_reader_identity_and_others_do_not(monkeypatch):
    seen = {}

    class S(requests.Session):
        def get(self, url, **kw):
            seen[url] = (kw.get("headers") or {}).get("User-Agent") or self.headers["User-Agent"]
            r = requests.Response(); r.status_code = 200; r._content = b"<html>ok</html>"; r.encoding = "utf-8"
            return r

    monkeypatch.setattr(config, "HOST_PACING_S", 0)
    h = Http(S())
    h.get("https://www.thetimes.com/"); h.get("https://www.bbc.co.uk/news")
    assert seen["https://www.thetimes.com/"].startswith("papernews/")
    assert seen["https://www.bbc.co.uk/news"] == config.USER_AGENT


def test_verifying_device_page_is_a_block():
    class S(requests.Session):
        def get(self, url, **kw):
            r = requests.Response(); r.status_code = 200; r.encoding = "utf-8"
            r._content = b"<html><head><title>Verifying Device</title></head></html>"
            return r
    try:
        Http(S()).get("https://www.thetimes.com/robots.txt")
    except SourceUnavailable:
        return
    raise AssertionError("challenge page was not treated as a block")


def test_morning_ocr_faults_seen_on_3_nov_are_repaired(monkeypatch):
    """The live 3 Nov Times front: OCR caught only the last headline line ("immigration") and ran the
    byline block into the opening paragraph; the rail was OCR fragments."""
    splash = Story("immigration", "Easier right to remain for 300,000 care workers",
                   ["Geraldine Scott Assistant Political Editor Emma Yeomans, Oliver Wright Aubrey Allegretti "
                    "Hundreds of thousands of foreign care workers and their families will be spared Shabana "
                    "Mahmood’s immigration crackdown ina decision that critics claim could cost the taxpayer £10bn."]
                   + ["Para " * 60] * 3, is_free=True)
    monkeypatch.setattr(printfront, "splash", lambda *a, **k: (splash, "6"))
    b = Builder(TimesHttp({}))
    monkeypatch.setattr(b, "_review", lambda slot: {})
    ed = b.build_one(clock.edition_for(at("2026-11-03 06:01")), "times")
    assert ed.kind == "print_front" and "mbvfl9qq8" in ed.lead.url
    assert ed.lead.headline == "Shabana Mahmood backs down on immigration curbs"
    assert ed.lead.paragraphs[0].startswith("Hundreds of thousands") and ed.lead.byline == BYLINE
    assert all(len(s.headline.split()) >= 4 for s in ed.rail)            # homepage headlines, not OCR fragments


def test_ocr_joined_article_is_split():
    from app.ocr.splash import tidy
    assert tidy("crackdown ina decision") == "crackdown in a decision"
    assert tidy("Ina Garten cooks") == "Ina Garten cooks"
