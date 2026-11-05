"""Google News RSS headlines for the Telegraph (5 Nov): the site refuses every identity."""
from datetime import datetime

from app import clock, config, store
from app.builder import Builder
from app.model import Story
from app.sources import gnews, printfront
from conftest import FakeHttp, Resp

FEED = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>"site:telegraph.co.uk/news when:1d" - Google News</title>
""" + "".join(f"""<item><title>{h} - The Telegraph</title><link>https://news.google.com/rss/articles/CBMi{i}?oc=5</link>
<pubDate>Thu, 05 Nov 2026 0{i}:00:00 GMT</pubDate><description>&lt;a href="https://news.google.com/rss/articles/CBMi{i}"&gt;{h}&lt;/a&gt;</description>
<source url="https://www.telegraph.co.uk">The Telegraph</source></item>""" for i, h in enumerate([
    "Reeves to scrap stamp duty for first-time buyers", "Army chief warns of drone gap", "Reeves to scrap stamp duty for first-time buyers",
    "NHS waiting lists fall for third month", "Iran strikes test Western resolve", "Ministers - and their advisers - split on China"])) + """
<item><title>Telegraph story syndicated - Yahoo News UK</title><link>https://news.google.com/x</link><source url="https://uk.news.yahoo.com">Yahoo News UK</source></item>
</channel></rss>"""


def at(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=clock.LONDON)


def test_items_strip_publisher_dedupe_and_drop_other_sites():
    got = [s.headline for s in gnews.items(FEED, "telegraph")]
    assert got == ["Reeves to scrap stamp duty for first-time buyers", "Army chief warns of drone gap",
                   "NHS waiting lists fall for third month", "Iran strikes test Western resolve",
                   "Ministers - and their advisers - split on China"]


def gn_http():
    return FakeHttp({"https://news.google.com/rss/search": Resp("g", FEED)})


def test_telegraph_shows_google_headlines_while_the_front_is_looked_for(monkeypatch):
    monkeypatch.setattr(config, "ROTATION", ["telegraph"])
    monkeypatch.setattr(printfront, "front", lambda *a, **k: None)
    b = Builder(gn_http())
    monkeypatch.setattr(b, "_review", lambda slot: {})
    monkeypatch.setattr(b, "prefetch_reviews", lambda at: None)
    b.tick(at("2026-11-05 06:01"))
    assert store.load_state("2026-11-05-am")["telegraph"]["status"] == "failed"     # still looking for the front
    _, ed, preparing = b.current(at("2026-11-05 06:05"))
    assert ed.paper == "telegraph" and ed.kind == "latest" and preparing
    assert ed.lead.headline.startswith("Reeves") and ed.note == "Latest headlines · via Google News"


def test_evening_without_a_front_is_google_headlines(monkeypatch):
    monkeypatch.setattr(printfront, "front", lambda *a, **k: None)
    b = Builder(gn_http())
    monkeypatch.setattr(b, "_review", lambda slot: {})
    ed = b.build_one(clock.edition_for(at("2026-11-05 16:01")), "telegraph")
    assert ed.kind == "latest" and len(ed.rail) == 4 and ed.layout == "briefing"


def test_thin_print_front_rail_is_topped_up_and_labelled(monkeypatch):
    splash = Story("Labour cuts Army", "Deck", ["Para " * 60] * 4, is_free=True)
    monkeypatch.setattr(printfront, "front", lambda *a, **k: printfront.Front(splash, "2", ["Spy chief warns"]))
    b = Builder(gn_http())
    monkeypatch.setattr(b, "_review", lambda slot: {})
    ed = b.build_one(clock.edition_for(at("2026-11-05 06:01")), "telegraph")
    assert ed.kind == "print_front" and ed.rail[0].headline == "Spy chief warns" and len(ed.rail) == 5
    assert ed.rail_front == 1                                     # only the first is from the printed front
