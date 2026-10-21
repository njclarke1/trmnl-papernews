import html
import json
import pathlib
import re
import time
from datetime import datetime, timedelta

import pytest
from liquid import Environment

from app import builder as builder_mod, clock, config, payload, store
from app.builder import Builder
from app.main import create_app
from app.model import Edition, Story
from app.sources import demo
from app.sources.base import ShapeError, SourceUnavailable

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
    return create_app(background=False).test_client()


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
