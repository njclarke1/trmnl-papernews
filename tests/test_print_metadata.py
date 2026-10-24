"""Printed-front identification from publisher metadata (Guardian page 1, NYT A1) and the second round-up (BBC)."""
from datetime import datetime

import pytest

from app import clock, config
from app.builder import Builder
from app.model import Story
from app.sources import bbc_papers, papers_review
from app.sources.base import SourceUnavailable
from app.sources.guardian import GuardianAdapter, on_front
from app.sources.nyt import NytAdapter, a1_story
from app.sources.papers_review import ReviewEntry
from conftest import FakeHttp, Resp
from test_nyt import API as NYT_TOP, STORIES as NYT_STORIES, api as nyt_api
from test_sources import g_item

DAY = "2026-10-21"


# ----------------------------------------------------------------------------- Guardian page 1
def printed(i, page="1", book="theguardian/mainsection", date=DAY, words=500, tone="tone/news", headline=None):
    it = g_item(i, tone=tone)
    it["fields"].update(newspaperPageNumber=page, newspaperEditionDate=f"{date}T00:00:00Z", wordcount=str(words),
                        publication="The Guardian")
    if headline:
        it["fields"]["headline"] = headline
    it["tags"].append({"id": book, "type": "newspaper-book"})
    return it


def test_on_front_means_main_book_page_one_today():
    assert on_front(printed(1), DAY)
    assert not on_front(printed(1, page="2"), DAY)
    assert not on_front(printed(1, book="theguardian/g2"), DAY)            # supplements number from 1 too
    assert not on_front(printed(1, date="2026-10-20"), DAY)


@pytest.fixture
def gkey(monkeypatch):
    monkeypatch.setattr(config, "GUARDIAN_API_KEY", "k")


def g_http(front, picks):
    return FakeHttp({"https://content.guardianapis.com/search": Resp("s", json_data={"response": {"results": front}}),
                     "https://content.guardianapis.com/uk": Resp("u", json_data={"response": {"editorsPicks": picks}})})


def test_guardian_morning_uses_printed_page_one(gkey):
    front = [printed(1, words=400, headline="Pensioners face winter fuel squeeze"),
             printed(2, words=1100, headline="Burnham orders radical rethink of options to fix Thames Water"),
             printed(3, book="theguardian/g2", words=3000), printed(4, page="5", words=2000),
             printed(5, words=900, tone="tone/comment")]
    picks = [g_item(i) for i in (7, 8, 9, 2)]
    review = ReviewEntry("guardian", "The Guardian leads on Andy Burnham ordering a radical rethink of options to fix Thames Water",
                         "radical rethink of options to fix Thames Water")
    ed = GuardianAdapter(g_http(front, picks)).build("am", DAY, review)
    assert ed.kind == "print_front" and ed.lead.url.endswith("/x/2")
    assert ed.lead.headline == "radical rethink of options to fix Thames Water"
    assert [s.url[-4:] for s in ed.rail][:3] == ["/x/1", "/x/7", "/x/8"]      # rest of page 1, then the web front
    assert all(s.url[-4:] not in ("/x/3", "/x/4", "/x/5") for s in [ed.lead, *ed.rail])
    assert all(not s.paragraphs for s in ed.rail) and len(ed.lead.paragraphs) > 3


def test_guardian_page_one_without_round_up_leads_on_the_longest(gkey):
    front = [printed(1, words=400), printed(2, words=1100)]
    ed = GuardianAdapter(g_http(front, [g_item(i) for i in range(7, 12)])).build("am", DAY, None)
    assert ed.kind == "print_front" and ed.lead.url.endswith("/x/2")


def test_guardian_without_print_metadata_falls_back_to_picks(gkey):
    ed = GuardianAdapter(g_http([], [g_item(i) for i in range(1, 7)])).build("am", DAY, None)
    assert ed.kind == "web_front" and ed.lead.url.endswith("/x/1")


# ----------------------------------------------------------------------------- NYT A1
SEARCH = "https://api.nytimes.com/svc/search/v2/articlesearch.json"


def doc(title, print_headline, lead, url, page="1", section="A", mm=None):
    return {"headline": {"main": title, "print_headline": print_headline}, "lead_paragraph": lead, "abstract": "",
            "web_url": url, "print_page": page, "print_section": section, "section_name": "World",
            "byline": {"original": "By Someone Here"}, "multimedia": mm if mm is not None else []}


RIOTS_URL = NYT_STORIES[2]["url"]
A1 = [doc("Riots Reignite French Debt Rout", "Unrest in France Rattles Bond Markets",
          "Investors dumped French government bonds yesterday as violent protests spread.", RIOTS_URL),
      doc("Fed Signals It Will Hold Rates Steady", "Fed Holds Steady", "The Federal Reserve signalled...",
          NYT_STORIES[4]["url"]),
      doc("A Feature on Page A12", "Feature", "Long read", "https://www.nytimes.com/x.html", page="12"),
      doc("Mayor's Bridge Plan", "Bridges", "The mayor...", "https://www.nytimes.com/2026/10/20/nyregion/bridges.html")]


def splash(headline="Unrest in France Rattles Bond Markets"):
    return Story(headline=headline, standfirst="",
                 paragraphs=["Investors dumped French government bonds yesterday as violent protests spread across "
                             "the country, pushing up borrowing costs."], is_free=True)


@pytest.fixture
def nkey(monkeypatch):
    monkeypatch.setattr(config, "NYT_API_KEY", "k")


def n_http(docs):
    routes = {NYT_TOP: nyt_api(NYT_STORIES)}
    if docs is not None:
        routes[SEARCH] = Resp(SEARCH, json_data={"status": "OK", "response": {"docs": docs}})
    return FakeHttp(routes)


def test_nyt_splash_confirmed_by_a1_metadata(nkey):
    ed = NytAdapter(n_http(A1)).build("am", DAY, print_lead=splash())
    assert ed.kind == "print_front" and ed.lead.url == RIOTS_URL
    assert ed.lead.image_url.endswith("l.jpg")                      # the top-stories copy's picture
    rail = [s.url for s in ed.rail]
    assert rail[0] == NYT_STORIES[4]["url"] and "bridges" in rail[1]  # rest of A1, web order first
    assert "x.html" not in " ".join(rail)                            # page A12 is not the front


def test_nyt_splash_not_on_a1_is_rejected(nkey):
    ed = NytAdapter(n_http(A1[1:])).build("am", DAY, print_lead=splash())
    assert ed.kind == "web_front" and ed.lead.paragraphs == []


def test_nyt_without_article_search_falls_back_to_top_stories_check(nkey):
    ed = NytAdapter(n_http(None)).build("am", DAY, print_lead=splash("Riots Reignite French Debt Rout"))
    assert ed.kind == "print_front" and ed.lead.url == RIOTS_URL


def test_nyt_search_images_both_shapes():
    new = a1_story(doc("T", "P", "L", "u", mm={"default": {"url": "https://static01.nyt.com/a.jpg", "width": 1200}}))[0]
    old = a1_story(doc("T", "P", "L", "u", mm=[{"url": "images/2026/b.jpg", "width": 600},
                                               {"url": "images/2026/c.jpg", "width": 1050}]))[0]
    assert new.image_url == "https://static01.nyt.com/a.jpg" and old.image_url == "https://static01.nyt.com/images/2026/c.jpg"
    assert a1_story(doc("T", "P", "L", "u"))[0].byline == "Someone Here"


def test_nyt_evening_makes_one_api_call(nkey):
    http = n_http(A1)
    NytAdapter(http).build("pm", DAY)
    assert http.calls == [NYT_TOP]                                   # 5 calls/min: no needless searches


# ----------------------------------------------------------------------------- BBC round-up + combining
def E(key, summary, quoted=""):
    return ReviewEntry(key, summary, quoted)


def test_combine_rules():
    pa = {"times": E("times", "The Times says curfews imposed in riot-torn French cities"),
          "ft": E("ft", "The FT reports Macron's troubles deepen as French debt rout returns"),
          "i": E("i", "The i reports the two-child cap on childcare benefits will be lifted")}
    bbc = {"times": E("times", "The Times leads with curfews in French cities after riots", "Curfews for riot-torn French cities"),
           "ft": E("ft", "The Financial Times leads on a Budget warning from the Bank of England"),
           "telegraph": E("telegraph", "The Telegraph says Labour has cut £2bn from the Army")}
    out, conflict = papers_review.combine(pa, bbc)
    assert conflict == ["ft"] and "ft" not in out
    assert out["times"].summary.startswith("The Times says") and out["times"].quoted == "Curfews for riot-torn French cities"
    assert set(out) == {"times", "i", "telegraph"}


def test_builder_drops_conflicting_splash_and_backs_off_failed_source(monkeypatch):
    b = Builder(FakeHttp({}))
    calls = {"pa": 0, "bbc": 0}

    def pa(http, day):
        calls["pa"] += 1
        return {"times": E("times", "The Times says curfews imposed in riot-torn French cities"),
                "telegraph": E("telegraph", "The Telegraph says Labour has cut £2bn from the Army")}

    def bbc(http, day):
        calls["bbc"] += 1
        if calls["bbc"] == 1:
            raise SourceUnavailable("down")
        return {"times": E("times", "The Times leads on a royal visit to Canada")}

    monkeypatch.setattr(Builder, "REVIEW_SOURCES", {"pa": pa, "bbc": bbc})
    slot = clock.edition_for(datetime(2026, 10, 21, 6, 1, tzinfo=clock.LONDON))
    assert set(b._review(slot)) == {"times", "telegraph"}           # BBC down: PA alone
    b._review(slot)
    assert calls == {"pa": 1, "bbc": 1}                              # PA cached; BBC not hammered
    b._review_failed.clear()
    r = b._review(slot)
    assert "times" not in r and "telegraph" in r                     # they disagree on the Times
