"""Fixes from the first live probe (24 Oct 2026): blocked sites, print-only papers, round-ups, encoding."""
from datetime import date, datetime

import pytest
import requests

from app import clock, config, store, text
from app.builder import Builder
from app.model import Edition, Story
from app.ocr.splash import Line, Word, front_headlines
from app.papers import PAPERS
from app.sources import bbc_papers, papers_review, printfront
from app.sources.base import Http, SourceUnavailable, page_free_flag
from app.sources.papers_review import ReviewEntry
from app.sources.web import WebAdapter, bigger_thumb
from conftest import FakeHttp, Resp

L = clock.LONDON


def at(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=L)


# ----------------------------------------------------------------------------- reading pages
def test_subscriber_only_marker_counts_as_paid():
    html = '<script>dataLayer=[{"topic_tags":"budget,politics long read,is-subscriber-only"}]</script>'
    assert page_free_flag([], {}, html) is False
    assert page_free_flag([], {}, '<script>{"subscriber_only":false}</script>') is None


def test_page_furniture_is_dropped_from_bodies():
    blob = ("- Published\nFrance is on high alert ahead of a new day of protests.\nImage caption, Police in Paris\n"
            "Become an Independent member to bookmark this article\nAlready a member? Log in\n"
            "Sweeping restrictions have been imposed.")
    assert text.paragraphs_from_blob(blob) == ["France is on high alert ahead of a new day of protests.",
                                               "Sweeping restrictions have been imposed."]


def test_pages_without_a_charset_are_read_as_utf8():
    class S:
        headers = {}

        def get(self, url, timeout=20, **kw):
            r = requests.Response()
            r._content, r.status_code, r.url = "UK to invest £100m".encode(), 200, url
            r.headers["Content-Type"] = "text/html"
            r.encoding = "ISO-8859-1"
            return r
    assert Http(S()).get("https://www.bbc.co.uk/news").text == "UK to invest £100m"


def test_feed_thumbnails_are_requested_large():
    u = "https://images.ft.com/v3/image/raw/x.jpg?source=next-home-page&dpr=2&width=580&fit=scale-down"
    assert "width=1200" in bigger_thumb(u) and bigger_thumb("https://ichef.bbci.co.uk/a.jpg") == "https://ichef.bbci.co.uk/a.jpg"


def test_homepage_links_follow_redirects():
    links = "".join(f'<a href="/article/story-number-{i}/">Story {i}</a>' for i in range(6))
    http = FakeHttp({"https://spectator.com": Resp("https://spectator.com/", links)})
    kind, cands = WebAdapter(PAPERS["spectator"], http).candidates()
    assert kind == "web_front" and cands[0].url == "https://spectator.com/article/story-number-0/"


def test_blocked_article_pages_fall_back_to_the_feed():
    items = "".join(f"<item><title>FT story {i}</title><description>Standfirst {i}</description>"
                    f"<link>https://www.ft.com/content/{i:08d}-0000-0000-0000-000000000000</link>"
                    f'<media:thumbnail url="https://images.ft.com/v3/image/raw/{i}.jpg?width=580"/></item>' for i in range(6))
    rss = f'<rss xmlns:media="http://search.yahoo.com/mrss/"><channel>{items}</channel></rss>'
    http = FakeHttp({"https://www.ft.com/rss/home/uk": rss})        # homepage and articles: unmapped = blocked
    ed = WebAdapter(PAPERS["ft"], http).build("pm", "2026-10-24")
    assert ed.kind == "latest" and ed.lead.headline == "FT story 0" and ed.lead.standfirst == "Standfirst 0"
    assert "width=1200" in ed.lead.image_url and len(ed.rail) >= 3
    assert ed.rail_front == 0                                   # a feed is not a front: "More stories"


# ----------------------------------------------------------------------------- print-only papers
@pytest.mark.parametrize("band,key", [
    ("THE TIMES Saturday October 24 2026 | thetimes.com | No 74,812", "times"),
    ("THE SUNDAY TIMES October 25 2026", None),
    ("FINANCIAL TIMES WEEKEND", "ft"),
    ("The Daily Telegraph Saturday 24 October 2026 telegraph.co.uk", "telegraph"),
    ("DAILY NEWSPAPER OF THE YEAR “e TIMES £3.20 £2.35 to subscribers", "times"),
    ("The Sunday Telegraph", None),
    ("The New York Times", None),
    ("the guardian Saturday 24 October 2026", "guardian"),
])
def test_masthead_identifies_the_paper(band, key):
    assert printfront.masthead_key(band) == key


def test_tomorrowspapers_map_identifies_each_edition(monkeypatch):
    idx = "".join(f'<a href="/{e}/">x</a>' for e in ("2026-10-24-aaa", "2026-10-24-bbb", "2026-10-23-ccc"))
    page = lambda n: f'<img src="https://www.tomorrowspapers.co.uk/wp-content/uploads/2026/10/2026-10-24-{n}.jpg">'
    http = FakeHttp({"https://www.tomorrowspapers.co.uk/2026-10-24-aaa": page("aaa"),
                     "https://www.tomorrowspapers.co.uk/2026-10-24-bbb": page("bbb"),
                     "https://www.tomorrowspapers.co.uk/wp-content/": Resp("i", content=b"img"),
                     "https://www.tomorrowspapers.co.uk/": idx})
    seen = iter(["telegraph", "times"])
    monkeypatch.setattr(printfront, "to_image", lambda raw: object())
    monkeypatch.setattr(printfront, "identify", lambda img: next(seen))
    m = printfront.tp_map(http, date(2026, 10, 24))
    assert m == {"telegraph": "https://www.tomorrowspapers.co.uk/wp-content/uploads/2026/10/2026-10-24-aaa.jpg",
                 "times": "https://www.tomorrowspapers.co.uk/wp-content/uploads/2026/10/2026-10-24-bbb.jpg"}
    n = len(http.calls)
    assert printfront.tp_map(http, date(2026, 10, 24)) == m and len(http.calls) == n     # all found: cached


def test_tomorrowspapers_map_picks_up_fronts_posted_later(monkeypatch):
    """3 Nov: the map was made from the first 11 fronts and cached; the Telegraph never got a look-in."""
    pages = {"aaa": "times"}
    idx = lambda: "".join(f'<a href="/2026-11-03-{n}/">x</a>' for n in pages)
    class H(FakeHttp):
        def get(self, url, **kw):
            self.calls.append(url)
            if url == printfront.TP:
                return Resp(url, idx())
            n = url.rstrip("/").rsplit("-", 1)[-1].split(".")[0]
            if "/wp-content/" in url:
                return Resp(url, content=n.encode())
            return Resp(url, f'<img src="https://www.tomorrowspapers.co.uk/wp-content/uploads/2026/11/2026-11-03-{n}.jpg">')
    monkeypatch.setattr(printfront, "to_image", lambda raw: raw.decode())
    monkeypatch.setattr(printfront, "identify", lambda name: pages[name])
    clock_s = [1000.0]
    monkeypatch.setattr("time.time", lambda: clock_s[0])
    http = H({})
    assert printfront.tp_map(http, date(2026, 11, 3)) == {"times": printfront.TP + "wp-content/uploads/2026/11/2026-11-03-aaa.jpg"}
    pages["bbb"] = "telegraph"
    n = len(http.calls)
    printfront.tp_map(http, date(2026, 11, 3))
    assert len(http.calls) == n                                       # within 20 min: no new requests
    clock_s[0] += 21 * 60
    m = printfront.tp_map(http, date(2026, 11, 3))
    assert m["telegraph"].endswith("-bbb.jpg")
    assert sum("2026-11-03-aaa" in c for c in http.calls) == 2         # the Times entry was not fetched again


def _line(text, y, h, x0=100, conf=95):
    ws, x = [], x0
    for t in text.split():
        ws.append(Word(t, conf, x, y, x + len(t) * h // 2, y + h)); x += len(t) * h // 2 + h // 3
    return Line(ws)


def test_front_headlines_are_the_other_big_type():
    lines = [_line("THE TIMES", 20, 120, 300),
             _line("Curfews imposed as riots spread", 300, 90), _line("across French cities", 400, 90),
             _line("PLUS: FREE GARDENING GUIDE INSIDE", 200, 40, 900),
             _line("Chancellor rules out tax rise on capital gains", 900, 44, 1300),
             _line("Spy chief warns of drone threat", 1500, 40, 1300)]
    lines += [_line("body text words that fill the column nicely", 520 + 30 * i, 18) for i in range(30)]
    out = front_headlines(lines, splash="Curfews imposed as riots spread across French cities", paper_name="THE TIMES")
    assert out == ["Chancellor rules out tax rise on capital gains", "Spy chief warns of drone threat"]


def _front(story=None, others=()):
    return printfront.Front(story, "2" if story else None, list(others))


def test_print_only_paper_morning_then_held_all_day(monkeypatch):
    splash = Story("Curfews for riot-torn French cities", "Deck", ["Para " * 60] * 4, is_free=True)
    monkeypatch.setattr(printfront, "front", lambda *a, **k: _front(splash, ["Chancellor rules out tax rise", "Spy chief warns"]))
    b = Builder(FakeHttp({}))
    monkeypatch.setattr(b, "_review", lambda slot: {})
    am = b.build_one(clock.edition_for(at("2026-10-24 06:01")), "telegraph")
    assert am.kind == "print_front" and am.layout == "full" and [s.headline for s in am.rail][0].startswith("Chancellor")
    store.save_edition("2026-10-24-am", am)
    pm = b.build_one(clock.edition_for(at("2026-10-24 16:01")), "telegraph")
    assert pm.lead.headline == am.lead.headline and pm.built_at == am.built_at


def test_print_only_falls_back_to_the_round_up(monkeypatch):
    monkeypatch.setattr(printfront, "front", lambda *a, **k: _front(None, ["Spy chief warns of drones"]))
    b = Builder(FakeHttp({}))
    entry = ReviewEntry("telegraph", "The Daily Telegraph says Labour has cut £2bn from the Army front line.")
    monkeypatch.setattr(b, "_review", lambda slot: {"telegraph": entry})
    ed = b.build_one(clock.edition_for(at("2026-10-24 06:01")), "telegraph")
    assert ed.lead.headline == "Labour has cut £2bn from the Army front line" and ed.layout == "briefing"


def test_round_up_sentence_alone_is_not_a_page(monkeypatch):
    """3 Nov: the Telegraph's front was unreadable, so the page was one round-up sentence shown twice
    (headline and body, with ', the Daily Telegraph says') above an empty 'Top stories' list."""
    monkeypatch.setattr(printfront, "front", lambda *a, **k: _front(None, []))
    b = Builder(FakeHttp({}))
    entry = ReviewEntry("telegraph", 'The home secretary has "watered down" her migration policy, the Daily Telegraph '
                                     "says. Shabana Mahmood's move comes despite her previous warning.")
    monkeypatch.setattr(b, "_review", lambda slot: {"telegraph": entry})
    with pytest.raises(SourceUnavailable):
        b.build_one(clock.edition_for(at("2026-11-03 06:01")), "telegraph")
    assert papers_review.headline_from(entry.summary) == 'The home secretary has "watered down" her migration policy'


def test_print_only_paper_is_never_carried_from_yesterday(monkeypatch):
    b = Builder(FakeHttp({}))
    store.save_edition("2026-11-02-am", Edition("telegraph", "print_front", Story("Yesterday's splash"), []))
    st = {"attempts": 3}
    b._carry(clock.edition_for(at("2026-11-03 06:01")), "telegraph", st)
    assert st["status"] == "missing" and store.load_edition("2026-11-03-am", "telegraph") is None


def test_print_only_papers_skip_sundays(monkeypatch):
    monkeypatch.setattr(config, "ROTATION", ["times", "telegraph", "bbc"])
    b = Builder(FakeHttp({}))
    monkeypatch.setattr(b, "build_one", Builder.build_one.__get__(b))
    st = b.tick(at("2026-10-25 06:01"))["papers"]
    assert st["times"]["status"] == st["telegraph"]["status"] == "not_published"
    slot, ed, _ = b.current(at("2026-10-25 06:05"), None)
    assert ed is None or ed.paper != "times"


# ----------------------------------------------------------------------------- round-ups
def bbc_review(published):
    paras = "".join(f"<p>{p}</p>" for p in (
        "The Times leads with curfews for riot-torn French cities as unrest spreads.",
        "The Daily Telegraph says Labour has cut £2bn from the Army front line.",
        "The Guardian leads on Ofcom admitting online safety laws have failed children.",
        "The Financial Times reports Healey will give a £20bn boost to the British Business Bank."))
    return (f'<html><head><script type="application/ld+json">{{"@type":"ReportageNewsArticle","datePublished":"{published}"}}'
            f'</script></head><body>{paras}</body></html>')


TOPIC = ('<a href="/news/topics/cpml2v678pxt">The Papers</a><a href="/news/articles/new1">\'A\' and \'B\'</a>'
         '<a href="/news/articles/old2">\'C\' and \'D\'</a>')


def test_bbc_review_from_the_topic_page():
    http = FakeHttp({"https://www.bbc.co.uk/news/topics/cpml2v678pxt": TOPIC,
                     "https://www.bbc.co.uk/news/articles/new1": bbc_review("2026-10-23T22:41:00Z"),
                     "https://www.bbc.co.uk/news/articles/old2": bbc_review("2026-10-22T22:41:00Z")})
    got = bbc_papers.fetch(http, "2026-10-24")
    assert {"times", "telegraph", "guardian", "ft"} <= set(got)
    assert bbc_papers.fetch(http, "2026-10-25") == {}          # the newest is the 24th's: not tomorrow's


def test_bbc_window_is_the_night_before():
    d = date(2026, 10, 24)
    assert bbc_papers.for_day(datetime(2026, 10, 23, 22, 41, tzinfo=L), d)
    assert bbc_papers.for_day(datetime(2026, 10, 24, 0, 30, tzinfo=L), d)
    assert not bbc_papers.for_day(datetime(2026, 10, 24, 0, 30, tzinfo=L), date(2026, 10, 25))
    assert not bbc_papers.for_day(datetime(2026, 10, 23, 9, 0, tzinfo=L), d)


def test_round_ups_are_prefetched_overnight_and_kept(monkeypatch):
    calls = []

    def pa(http, day):
        calls.append(("pa", day))
        return {"times": ReviewEntry("times", "The Times leads with curfews for riot-torn French cities.")}
    monkeypatch.setattr(Builder, "REVIEW_SOURCES", {"pa": pa, "bbc": lambda http, day: {}})
    b = Builder(FakeHttp({}))
    b.prefetch_reviews(at("2026-10-23 22:40"))
    assert calls == [("pa", "2026-10-24")]
    fresh = Builder(FakeHttp({}))                               # e.g. after a restart
    monkeypatch.setattr(Builder, "REVIEW_SOURCES", {"pa": lambda h, d: pytest.fail("refetched"), "bbc": lambda h, d: {}})
    assert "times" in fresh._review(clock.edition_for(at("2026-10-24 06:01")))
    b.prefetch_reviews(at("2026-10-24 14:00"))                  # daytime: nothing


def test_headline_from_round_up_sentence():
    assert papers_review.headline_from("The Times leads with curfews for riot-torn cities. Also...") == "Curfews for riot-torn cities"


# ----------------------------------------------------------------------------- first live edition (24 Oct, 08:14)
def test_round_up_fragments_never_replace_the_papers_headline():
    assert papers_review.as_headline("online safety laws have failed to protect children") == ""
    assert papers_review.as_headline("Curfews for riot-torn French cities") == "Curfews for riot-torn French cities"
    assert papers_review.as_headline("No") == ""


def test_related_lines_are_dropped():
    assert text.paragraphs_from_blob("Related: UK watchdogs urged to act\nReal paragraph here.") == ["Real paragraph here."]


def test_rail_is_only_called_the_front_when_it_is():
    from app import payload
    from app.model import Edition
    from app.sources import demo
    slot = clock.edition_for(at("2026-10-24 08:00"))
    ed = demo.edition("guardian"); ed.kind, ed.layout = "print_front", "full"
    ed.rail_front = 0                                  # page 1 had only the splash: rail from the website
    assert "stories" in payload.build(ed, slot)["rail_head"]
    ed.rail_front = -1
    assert "front" in payload.build(ed, slot)["rail_head"]


def test_fonts_are_served_with_cors(tmp_path, monkeypatch):
    from app.main import create_app
    c = create_app(background=False).test_client()
    r = c.get("/static/fonts/PlayfairDisplay-Variable.ttf")
    assert r.status_code == 200 and r.headers["Access-Control-Allow-Origin"] == "*"


def test_edition_requests_never_wait_on_the_network(monkeypatch):
    """LaraPaper times out a poll at 15 s and then renders an empty page: requests read caches only."""
    from app import weather
    from app.main import create_app
    monkeypatch.setattr(weather, "current", lambda session=None: pytest.fail("network call in a request"))
    c = create_app(background=False).test_client()
    assert c.get("/edition").status_code == 200


def test_headline_flags_become_the_kicker():
    assert text.split_flag("EXCLUSIVE: The power of talking to dead people") == ("Exclusive", "The power of talking to dead people")
    assert text.split_flag("Live – Budget latest") == ("Live", "Budget latest")
    assert text.split_flag("Exclusive deal for teachers") == ("", "Exclusive deal for teachers")


def test_byline_from_meta_author_when_jsonld_has_none():
    from app.sources.base import read_article
    page = ('<html><head><meta name="author" content="Alastair Campbell"><script type="application/ld+json">'
            '{"@type":"Article","headline":"H"}</script></head><body><p>x</p></body></html>')
    assert read_article(FakeHttp({"https://x/": page}), "https://x/a").byline == "Alastair Campbell"


def test_feeds_are_fetched_conditionally():
    seen = []

    class S:
        headers = {}

        def get(self, url, timeout=20, headers=None, **kw):
            seen.append(dict(headers or {}))
            r = requests.Response()
            r.url, r.status_code = url, (304 if headers and headers.get("If-None-Match") == '"v1"' else 200)
            r._content = b"" if r.status_code == 304 else b"<rss><channel><item><title>A</title></item></channel></rss>"
            r.headers["Content-Type"] = "application/rss+xml; charset=utf-8"
            r.headers["ETag"] = '"v1"'
            return r
    h = Http(S())
    first = h.feed("https://example.org/rss.xml")
    second = h.feed("https://example.org/rss.xml")
    assert first == second and "<title>A</title>" in second            # the 304 served the kept copy
    assert seen[0]["Accept"].startswith("application/rss+xml") and seen[1]["If-None-Match"] == '"v1"'
