"""Fixes from the Ponytail audit (5 Nov 2026)."""
import os
import time
from datetime import datetime, timedelta

from app import clock, config, layout, store, weather
from app.builder import Builder
from app.model import Edition, Story
from app.papers import PAPERS
from app.sources import printfront
from app.sources.guardian import GuardianAdapter, to_story
from app.sources.papers_review import ReviewEntry
from conftest import FakeHttp, Resp
from test_sources import g_item

REAL_CACHED = weather.cached                 # conftest replaces it per test; this is the real one


def at(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=clock.LONDON)


def test_live_blog_lead_is_never_flowed_as_an_article():
    item = g_item(1, typ="liveblog", tone="tone/minutebyminute")
    lead = to_story(item)
    lead.paragraphs = [f"12.{i:02d} GMT Update number {i} with some words in it." for i in range(40)]
    assert lead.live and lead.body_chars > 1500
    ed = Edition("guardian", "web_front", lead, [Story("a"), Story("b"), Story("c")])
    assert layout.choose(ed, PAPERS["guardian"]) != "full"


def test_guardian_premium_content_is_summary_only():
    item = g_item(1)
    item["fields"]["isPremiumContent"] = "true"
    assert to_story(item).is_free is False
    assert to_story(g_item(2)).is_free is True


def test_guardian_round_up_splash_from_picks_is_labelled_honestly(monkeypatch):
    monkeypatch.setattr(config, "GUARDIAN_API_KEY", "k")
    picks = [g_item(i) for i in range(1, 7)]
    picks[2]["fields"]["headline"] = "Burnham orders radical rethink of Thames Water"
    http = FakeHttp({"https://content.guardianapis.com/search": Resp("s", json_data={"response": {"results": []}}),
                     "https://content.guardianapis.com/uk": Resp("u", json_data={"response": {"editorsPicks": picks}})})
    review = ReviewEntry("guardian", "The Guardian leads on a radical rethink of Thames Water",
                         "Burnham orders radical rethink of Thames Water")
    ed = GuardianAdapter(http).build("am", "2026-11-05", review)
    assert ed.kind == "print_front" and ed.lead.url.endswith("/x/3")
    assert ed.rail_front == 0 and ed.note == "Splash named by the paper reviews"   # not "Printed page 1"


def test_poll_rotation_waits_advances_and_skips(monkeypatch):
    monkeypatch.setattr(config, "ROTATION_MODE", "poll")
    monkeypatch.setattr(config, "ROTATION", ["guardian", "bbc", "times"])
    monkeypatch.setattr(config, "MIN_ADVANCE_S", 150)
    monkeypatch.setattr(config, "PIN", "")
    b = Builder(FakeHttp({}))
    avail = ["guardian", "times"]                                     # the BBC has nothing to show
    first = b.next_paper(at("2026-11-05 10:00:00"), avail)
    assert b.next_paper(at("2026-11-05 10:00:10"), avail) == first   # stray poll: stays
    second = b.next_paper(at("2026-11-05 10:02:40"), avail)
    assert second != first and second in avail                        # advances, skipping the BBC
    assert b.next_paper(at("2026-11-05 10:05:20"), avail) == first    # and wraps


def test_weather_is_hidden_when_stale(monkeypatch):
    monkeypatch.setitem(weather._CACHE, "data", {"temp": "12°"})
    monkeypatch.setitem(weather._CACHE, "at", time.time() - 3600)
    assert REAL_CACHED() == {"temp": "12°"}
    monkeypatch.setitem(weather._CACHE, "at", time.time() - 4 * 3600)
    assert REAL_CACHED() is None


def test_ocr_crash_is_not_cached_as_a_verdict(monkeypatch):
    p = PAPERS["telegraph"]
    monkeypatch.setattr(config, "OCR_ENABLED", True)
    monkeypatch.setattr(printfront, "find_image", lambda *a: "https://x/front.jpg")
    calls = []
    def boom(raw):
        calls.append(1)
        raise MemoryError("tesseract killed")
    monkeypatch.setattr(printfront, "to_image", boom)
    http = FakeHttp({"https://x/": Resp("i", content=b"jpg")})
    from datetime import date
    assert printfront.front(http, p, date(2026, 11, 5)).story is None
    printfront.front(http, p, date(2026, 11, 5))
    assert len(calls) == 2                                            # retried, not served from the cache


def test_prune_keeps_recent_and_last_good_photos():
    root = store._root()
    for sub in ("img", "cache/article", "editions/2026-10-01-am", "editions/2026-11-05-am"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    old, new, kept = root / "img" / "old.png", root / "img" / "new.png", root / "img" / "keepme.png"
    art = root / "cache" / "article" / "a.json"
    for f in (old, new, kept, art):
        f.write_bytes(b"x")
    store.save_edition("2026-11-05-am", Edition("bbc", "web_front", Story("x"), [], image="http://h/img/keepme.png"))
    ages = time.time() - 30 * 86400
    for f in (old, kept, art, root / "editions" / "2026-10-01-am"):
        os.utime(f, (ages, ages))
    store.prune(days=14)
    assert not old.exists() and not art.exists() and not (root / "editions" / "2026-10-01-am").exists()
    assert new.exists() and kept.exists() and (root / "editions" / "2026-11-05-am").exists()
