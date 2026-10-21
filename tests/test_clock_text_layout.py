from datetime import datetime

import pytest

from app import clock, config, layout, text
from app.model import Edition, Story
from app.papers import PAPERS

L = clock.LONDON


def at(s):
    return datetime.fromisoformat(s).replace(tzinfo=L)


# ---------------------------------------------------------------- editions
@pytest.mark.parametrize("when,slot", [
    ("2026-10-21 05:59", "2026-10-20-pm"),     # overnight shows yesterday's evening edition
    ("2026-10-21 06:00", "2026-10-21-am"),
    ("2026-10-21 15:59", "2026-10-21-am"),
    ("2026-10-21 16:00", "2026-10-21-pm"),
    ("2026-10-21 23:59", "2026-10-21-pm"),
    ("2026-10-25 06:30", "2026-10-25-am"),     # clocks go back at 02:00 that morning
    ("2026-03-29 06:00", "2026-03-29-am"),     # clocks go forward
])
def test_edition_slots(when, slot):
    assert clock.edition_for(at(when)).id == slot


def test_lock_is_local_wall_time_across_dst():
    s = clock.edition_for(at("2026-10-25 07:00"))
    assert s.lock_at.hour == 6 and s.lock_at.utcoffset().total_seconds() == 0      # GMT
    s = clock.edition_for(at("2026-10-24 07:00"))
    assert s.lock_at.hour == 6 and s.lock_at.utcoffset().total_seconds() == 3600   # BST


def test_labels_and_issue():
    assert clock.edition_for(at("2026-10-21 07:00")).label == "Morning edition"
    assert clock.edition_for(at("2026-10-21 02:00")).label == "Evening edition"
    assert clock.issue_number(at("2026-10-21 07:00").date()) == 294
    assert clock.folio_date(at("2026-10-21 07:00").date()) == "Wednesday 21 October 2026"


def test_rotation_is_stable_within_a_slot_and_advances(monkeypatch):
    monkeypatch.setattr(config, "SLOT_MINUTES", 20)
    allp = list(config.ROTATION)
    a, _ = clock.rotation_paper(at("2026-10-21 10:00"), allp)
    b, _ = clock.rotation_paper(at("2026-10-21 10:19"), allp)
    c, _ = clock.rotation_paper(at("2026-10-21 10:20"), allp)
    assert a == b and c != a
    seen = {clock.rotation_paper(at(f"2026-10-21 {10 + m // 60:02d}:{m % 60:02d}"), allp)[0] for m in range(0, 140, 20)}
    assert seen == set(allp)                    # equal weights: each paper once per 7 slots


def test_rotation_skips_missing_and_pin(monkeypatch):
    key, idx = clock.rotation_paper(at("2026-10-21 10:00"), ["ft"])
    assert key == "ft" and idx == config.ROTATION.index("ft")
    assert clock.rotation_paper(at("2026-10-21 10:00"), [])[0] is None
    monkeypatch.setattr(config, "PIN", "times")
    assert clock.rotation_paper(at("2026-10-21 10:00"), list(config.ROTATION))[0] == "times"


# ---------------------------------------------------------------- text
def test_soft_hyphens_never_on_final_word():
    s = text.shy("Ministers approved the extraordinary infrastructure")
    assert "­" in s and "infrastructure" in s.split(" ")[-1] and "­" not in s.split(" ")[-1]


def test_display_keeps_compounds_whole_and_escapes():
    d = text.display("Cost-of-living protests & riots")
    assert '<span class="nw">Cost-of-living</span>' in d and "&amp;" in d


def test_flow_tokens_unescaped_for_textcontent():
    toks = text.flow_tokens(["Fines & penalties rose. Then they fell."])
    assert toks[0]["t"].startswith("Fines & ") and toks[0]["p"] and not toks[1]["p"]


@pytest.mark.parametrize("h,n", [("Short headline", 2), ("x" * 90, 3), ("x" * 140, 4),
                                 ("<span class='nw'>" + "y" * 60 + "</span>", 2)])
def test_headline_budget_counts_plain_text(h, n):
    assert text.headline_lines(h) == n


# ---------------------------------------------------------------- layout
def ed(paper="guardian", kind="web_front", paras=None, free=True, img="http://x/i.jpg", rail_sf=False):
    rail = [Story(f"Story {i}", standfirst="Summary" if rail_sf else "") for i in range(5)]
    return Edition(paper, kind, Story("Lead", "Standfirst", paras or [], image_url=img, is_free=free), rail)


def test_layout_full_needs_free_body():
    long = ["word " * 400]
    assert layout.choose(ed(paras=long), PAPERS["guardian"]) == "full"
    assert layout.choose(ed(paper="ft", paras=long, free=False), PAPERS["ft"]) == "summary"


def test_layout_print_front_accepts_shorter_opening():
    assert layout.choose(ed(paper="ft", kind="print_front", paras=["w " * 500]), PAPERS["ft"]) == "full"


def test_layout_fallbacks():
    assert layout.choose(ed(paper="indy"), PAPERS["indy"]) == "poster"
    assert layout.choose(ed(paper="indy", img=""), PAPERS["indy"]) == "briefing"
    assert layout.choose(ed(paper="bbc", rail_sf=True), PAPERS["bbc"]) == "bulletin"
    assert layout.choose(ed(paper="bbc"), PAPERS["bbc"]) == "summary"     # no summaries for a bulletin
    assert layout.choose(ed(paper="i"), PAPERS["i"]) == "briefing"


def test_weather_codes_and_night_glyph():
    from app import weather
    assert weather.describe(0) == ("clear", "Clear") and weather.describe(63)[0] == "rain"
    assert weather.describe(999)[0] == "cloud"
    assert weather.glyph("clear", True) == "moon" and weather.glyph("rain", True) == "rain"
