import html
import json
import pathlib
import re
from datetime import datetime

import pytest
from liquid import Environment

from app import clock, config, payload, store
from app.builder import Builder
from app.main import create_app
from app.sources import demo
from app.sources.base import ShapeError, SourceUnavailable
from conftest import FIX, FakeHttp, Resp

L = clock.LONDON
TEMPLATE = (pathlib.Path(__file__).resolve().parents[1] / "plugin/full.liquid").read_text()


def at(s):
    return datetime.fromisoformat(s).replace(tzinfo=L)


class Scripted(Builder):
    """Builder whose build_one follows a script per paper: 'ok', 'down', 'shape'."""

    def __init__(self, script):
        super().__init__()
        self.script, self.calls = script, []

    def build_one(self, slot, key):
        self.calls.append(key)
        what = self.script.get(key, "ok")
        if what == "down":
            raise SourceUnavailable("down")
        if what == "shape":
            raise ShapeError("changed")
        ed = demo.edition(key)
        ed.layout, ed.built_at = "summary", slot.id
        return ed


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(config, "RETRY_EVERY_MIN", 0)


def test_builds_every_paper_once_and_locks(fast):
    b = Scripted({})
    b.tick(at("2026-10-21 06:01"))
    assert sorted(b.calls) == sorted(config.ROTATION)
    b.tick(at("2026-10-21 09:00"))
    assert len(b.calls) == len(config.ROTATION)          # locked: no rebuilds until the next edition
    b.tick(at("2026-10-21 16:01"))
    assert len(b.calls) == 2 * len(config.ROTATION)


def test_failed_paper_retries_then_carries_last_good(fast):
    Scripted({}).tick(at("2026-10-20 16:01"))            # yesterday evening: all good
    b = Scripted({"ft": "down"})
    b.tick(at("2026-10-21 06:01"))
    b.tick(at("2026-10-21 06:30"))
    assert b.calls.count("ft") == 2
    assert store.load_edition("2026-10-21-am", "ft") is None
    b.tick(at("2026-10-21 07:05"))                       # past the 60-minute window
    ed = store.load_edition("2026-10-21-am", "ft")
    assert ed is not None and ed.stale and ed.built_at == "2026-10-20-pm"
    assert store.load_state("2026-10-21-am")["ft"]["status"] == "carried"


def test_shape_error_carries_with_data_check(fast):
    Scripted({}).tick(at("2026-10-20 16:01"))
    b = Scripted({"bbc": "shape"})
    b.tick(at("2026-10-21 06:01"))
    b.tick(at("2026-10-21 07:30"))
    ed = store.load_edition("2026-10-21-am", "bbc")
    assert ed.stale and ed.data_check


def test_missing_without_last_good(fast):
    b = Scripted({"times": "down"})
    b.tick(at("2026-10-21 06:01"))
    b.tick(at("2026-10-21 07:30"))
    assert store.load_state("2026-10-21-am")["times"]["status"] == "missing"


def test_current_falls_back_to_last_good_while_preparing(fast):
    Scripted({}).tick(at("2026-10-20 16:01"))
    b = Scripted({})
    slot, ed, preparing = b.current(at("2026-10-21 06:00"), "guardian")
    assert ed is not None and preparing and slot.id == "2026-10-21-am"


def test_one_crashing_paper_does_not_stop_the_edition(fast, monkeypatch):
    class Boom(Scripted):
        def build_one(self, slot, key):
            if key == "i":
                raise RuntimeError("bug")
            return super().build_one(slot, key)
    b = Boom({})
    b.tick(at("2026-10-21 06:01"))
    st = store.load_state("2026-10-21-am")
    assert st["i"]["status"] == "failed" and st["guardian"]["status"] == "ok"


# ---------------------------------------------------------------- payload + template
@pytest.fixture
def demo_app(monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", True)
    b = Builder()
    b.tick()                                   # what the background thread does in production
    return create_app(b, background=False).test_client()


@pytest.mark.parametrize("state", list(demo.states()))
def test_every_demo_state_renders(demo_app, state):
    data = demo_app.get(f"/demo/{state}.json").get_json()
    out = Environment().from_string(TEMPLATE).render(**data)
    assert "{{" not in out and "{%" not in out
    assert "margin:0!important" in out and "position:fixed" in out          # page-offset fix (rugby lesson)
    assert f'np-{data["paper"]["np"]}' in out
    if data["layout"] == "full":
        flow = json.loads(html.unescape(re.search(r'data-flow="([^"]*)"', out).group(1)))
        assert flow and flow[0]["p"]
    assert ("CACHED" in out) == (state == "cached")


def test_demo_layouts(demo_app):
    lay = {s: demo_app.get(f"/demo/{s}.json").get_json()["layout"] for s in demo.states()}
    assert lay["guardian"] == "full" and lay["ft"] == "summary" and lay["ft-print"] == "full"
    assert lay["indy"] == "poster" and lay["bbc"] == "bulletin" and lay["i"] == "briefing"
    assert lay["no-image"] == "full"


def test_edition_endpoint_and_health(demo_app):
    h = demo_app.get("/healthz").get_json()
    assert h["ok"] and all(v["status"] == "ok" for v in h["papers"].values())
    e = demo_app.get("/edition").get_json()
    assert e["layout"] in ("full", "summary", "poster", "bulletin", "briefing")
    assert sum(d["on"] for d in e["dots"]) == 1
    assert demo_app.get("/edition/ft").get_json()["paper"]["key"] == "ft"
    assert demo_app.get("/edition/nope").status_code == 404


def test_fonts_served(demo_app):
    r = demo_app.get("/static/fonts/SourceSerif4-Variable.ttf")
    assert r.status_code == 200 and len(r.data) > 100000


def test_empty_payload_before_first_edition():
    slot = clock.edition_for(at("2026-10-21 06:00"))
    p = payload.build(None, slot)
    out = Environment().from_string(TEMPLATE).render(**p)
    assert p["layout"] == "empty" and "being prepared" in out


def test_print_front_labels(demo_app):
    d = demo_app.get("/demo/ft-print.json").get_json()
    assert d["lead"]["kind"] == "Today’s front page" and "page 4" in d["lead"]["jump"]
    d = demo_app.get("/demo/ft.json").get_json()
    assert d["lead"]["kind"] == "The lead" and d["rail_grid"] is True


# ---------------------------------------------------------------- review follow-ups
def test_retry_spacing_uses_the_edition_clock(monkeypatch):
    monkeypatch.setattr(config, "RETRY_EVERY_MIN", 5)
    b = Scripted({"ft": "down"})
    b.tick(at("2026-10-21 06:01"))
    b.tick(at("2026-10-21 06:04"))                      # too soon: no retry
    assert b.calls.count("ft") == 1
    b.tick(at("2026-10-21 06:07"))
    assert b.calls.count("ft") == 2


def test_late_start_gets_a_full_retry_window(fast):
    Scripted({}).tick(at("2026-10-20 16:01"))
    b = Scripted({"bbc": "down"})
    b.tick(at("2026-10-21 08:00"))                      # NAS rebooted long after the lock
    assert store.load_state("2026-10-21-am")["bbc"]["status"] == "failed"   # not carried after one try
    b.script["bbc"] = "ok"
    b.tick(at("2026-10-21 08:30"))
    assert store.load_state("2026-10-21-am")["bbc"]["status"] == "ok"
    b2 = Scripted({"times": "down"})                   # evening edition, started at 18:00
    b2.tick(at("2026-10-21 18:00")); b2.tick(at("2026-10-21 18:59"))
    assert store.load_state("2026-10-21-pm")["times"]["status"] == "failed"   # window runs from 18:00
    b2.tick(at("2026-10-21 19:05"))
    assert store.load_state("2026-10-21-pm")["times"]["status"] == "carried"


def test_restart_never_rebuilds_a_locked_edition(fast):
    b = Scripted({})
    slot = clock.edition_for(at("2026-10-21 06:01"))
    ed = demo.edition("guardian"); ed.built_at = "ORIGINAL"; ed.layout = "summary"
    store.save_edition(slot.id, ed)                     # written, then the container died before state
    b.tick(at("2026-10-21 06:02"))
    assert "guardian" not in b.calls
    assert store.load_edition(slot.id, "guardian").built_at == "ORIGINAL"


def test_state_saved_after_each_paper(fast, monkeypatch):
    saves = []
    real = store.save_state
    monkeypatch.setattr(store, "save_state", lambda sid, st: (saves.append(sid), real(sid, st)))
    Scripted({}).tick(at("2026-10-21 06:01"))
    assert len(saves) >= len(set(config.ROTATION))


def test_healthz_is_read_only(monkeypatch):
    calls = []
    monkeypatch.setattr(Builder, "tick", lambda self, at=None: calls.append(1) or {})
    c = create_app(background=False).test_client()
    h = c.get("/healthz").get_json()
    assert h["ok"] and calls == [] and "papers" in h


def test_demo_mode_never_writes_last_good(monkeypatch, data_dir):
    monkeypatch.setattr(config, "DEMO_MODE", True)
    Builder().tick(at("2026-10-21 06:01"))
    assert not (data_dir / "lastgood").exists()


def test_file_urls_refused_outside_demo():
    with pytest.raises(SourceUnavailable):
        Builder()._raw("file:///etc/passwd")


def test_payload_never_shows_paid_paragraphs():
    ed = demo.edition("guardian"); ed.lead.is_free = False; ed.layout = "full"
    p = payload.build(ed, clock.edition_for(at("2026-10-21 07:00")))
    assert p["layout"] != "full" and json.loads(html.unescape(p["lead"]["flow_json"])) == []


def test_dots_one_per_paper_even_when_weighted(monkeypatch):
    monkeypatch.setattr(config, "ROTATION", ["guardian", "bbc", "guardian", "ft"])
    p = payload.build(demo.edition("guardian"), clock.edition_for(at("2026-10-21 07:00")))
    assert len(p["dots"]) == 3 and [d["on"] for d in p["dots"]] == [True, False, False]


def test_old_edition_files_still_load(data_dir):
    path = data_dir / "editions" / "2026-10-21-am"; path.mkdir(parents=True)
    d = demo.edition("bbc").to_dict(); d["retired_field"] = 1; d["lead"]["also_retired"] = "x"
    (path / "bbc.json").write_text(json.dumps(d))
    (path / "ft.json").write_bytes(b"\xff\xfe not json")
    assert store.load_edition("2026-10-21-am", "bbc").paper == "bbc"
    assert store.load_edition("2026-10-21-am", "ft") is None


# ---------------------------------------------------------------- the real build path, end to end
PA = """<p>The Financial Times reports riots have reignited a French debt rout, with borrowing costs and
curfews spreading as Macron struggles to restore order.</p><p>The Guardian leads on Thames Water.</p>"""


def ft_routes(front_image: bytes):
    links = "".join(f'<a href="/content/{i:08d}-0000-0000-0000-000000000000">Story {i}</a>' for i in range(1, 7))
    paid = lambda h: ('<html><head><script type="application/ld+json">' + json.dumps({"@graph": [
        {"@type": "WebPage", "isAccessibleForFree": False}, {"@type": "NewsArticle", "headline": h,
         "description": "Standfirst", "articleBody": "secret paid text " * 200}]}) + "</script></head><body></body></html>")
    return {
        "https://www.expressandstar.com/uk-news/what-the-papers-say-october-21": PA,
        "https://www.expressandstar.com/uk-news/": '<a href="/uk-news/what-the-papers-say-october-21-123">x</a>',
        "https://www.frontpages.com/financial-times": '<meta property="og:image" content="https://www.frontpages.com/g/2026/10/21/ft.jpg">',
        "https://www.frontpages.com/g/": Resp("img", content=front_image),
        "https://www.ft.com/rss": "<rss><channel></channel></rss>",
        "https://www.ft.com/content/00000001": paid("Riots reignite French debt rout as Macron struggles to restore order"),
        "https://www.ft.com/content/": paid("Another FT story"),
        "https://www.ft.com": f"<html><body><a href='/content/00000001-0000-0000-0000-000000000000'>Riots reignite French debt rout as Macron struggles to restore order</a>{links}</body></html>",
    }


@pytest.mark.skipif(__import__("shutil").which("tesseract") is None, reason="tesseract not installed")
def test_ft_morning_edition_from_printed_front():
    b = Builder(FakeHttp(ft_routes((FIX / "fronts" / "ft-1400.jpg").read_bytes())))
    ed = b.build_one(clock.edition_for(at("2026-10-21 06:01")), "ft")
    assert ed.kind == "print_front" and ed.layout == "full" and ed.continued_page == "4"
    assert "Investors" in ed.lead.paragraphs[0] and "secret" not in " ".join(ed.lead.paragraphs)
    assert ed.lead.url.startswith("https://www.ft.com/content/00000001")     # QR links to the matched article
    assert all("secret" not in " ".join(s.paragraphs) for s in ed.rail)


def test_ft_evening_edition_is_headlines_only():
    b = Builder(FakeHttp(ft_routes(b"")))
    ed = b.build_one(clock.edition_for(at("2026-10-21 16:01")), "ft")
    assert ed.kind == "web_front" and ed.lead.paragraphs == [] and ed.layout == "briefing"


def test_truncated_front_image_falls_back_to_summary():
    raw = (FIX / "fronts" / "ft-1400.jpg").read_bytes()[:20000]
    b = Builder(FakeHttp(ft_routes(raw)))
    ed = b.build_one(clock.edition_for(at("2026-10-21 06:01")), "ft")
    assert ed.lead.paragraphs == [] and ed.layout != "full"
