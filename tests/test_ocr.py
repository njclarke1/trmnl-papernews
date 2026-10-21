"""OCR splash extraction on synthetic broadsheet fronts (tools/make_synthetic_fronts.py), at
PRODUCTION thresholds (min 150 words, mean confidence 85, dictionary ratio 0.85).
Real fronts will differ: re-tune against the probe's saved images before trusting these."""
import collections
import json
import re
import shutil

import pytest
from PIL import Image

from app.ocr import splash as S
from app.ocr.splash import Line, Word, extract_splash
from conftest import FIX

needs_tess = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
F = FIX / "fronts"
NAME = {"ft": "FINANCIAL TIMES", "times": "THE TIMES"}


def words(s):
    return re.findall(r"[a-z]+", s.lower())


def score(result, truth):
    got, want = collections.Counter(words(result.body)), collections.Counter(words(truth["body"]))
    other = set(words(truth["other_text"])) - set(words(truth["body"]))
    recall = sum(min(got[w], n) for w, n in want.items()) / sum(want.values())
    leak = sum(n for w, n in got.items() if w in other)
    return recall, leak


@needs_tess
@pytest.mark.parametrize("name", ["ft-1200", "ft-1400", "times-1200", "times-1400"])
def test_extracts_splash_at_production_thresholds(name):
    truth = json.loads((F / f"{name}.json").read_text())
    r = extract_splash(Image.open(F / f"{name}.jpg"), paper_name=NAME[name.split("-")[0]])
    recall, leak = score(r, truth)
    assert r.ok, r.reason
    assert recall >= 0.9 and leak == 0
    assert r.continued_page == truth["page"]
    assert words(truth["deck"])[:4] == words(r.deck)[:4]
    assert r.mean_conf >= 85 and r.dict_ratio >= 0.85


@needs_tess
def test_rejects_when_headline_does_not_match_expected():
    r = extract_splash(Image.open(F / "ft-1200.jpg"), paper_name=NAME["ft"],
                       expected_headline="Ministers announce new rail franchise for the north")
    assert not r.ok and "headline" in r.reason


@needs_tess
def test_rejects_small_image():
    im = Image.open(F / "ft-1000.jpg")
    small = im.resize((640, round(im.height * 640 / im.width)))
    r = extract_splash(small, paper_name=NAME["ft"])
    assert not r.ok and "words" in r.reason          # too little legible text to show


@needs_tess
def test_standfirst_cross_check():
    img = Image.open(F / "ft-1400.jpg")
    ok = extract_splash(img, paper_name=NAME["ft"],
                        expected_standfirst="French bond spreads widen as protests and curfews spread")
    bad = extract_splash(img, paper_name=NAME["ft"],
                         expected_standfirst="Premier League clubs agree new television rights settlement")
    assert ok.ok and not bad.ok and "standfirst" in bad.reason


# ---------------------------------------------------------------- gates, on hand-built OCR output
def page(body_words, conf=95.0, junk_conf=None):
    """Masthead, headline, then 3 columns of body lines (10 words each); optionally a clean
    unrelated story far below, to prove its confidence doesn't prop up a garbled splash."""
    lines = [Line([Word("FINANCIAL", 99, 300, 10, 700, 80), Word("TIMES", 99, 720, 10, 1000, 80)])]
    lines.append(Line([Word(w, 99, 40 + i * 140, 200, 40 + i * 140 + 130, 260)
                       for i, w in enumerate("Riots reignite French debt rout".split())]))
    it = iter(body_words)
    for c in range(3):
        for r in range(12):
            ws = []
            for k in range(10):
                w = next(it, None)
                if w is None:
                    break
                ws.append(Word(w, conf, 40 + c * 330 + k * 31, 300 + r * 24, 40 + c * 330 + k * 31 + 28, 318))
            if ws:
                for wd in ws:
                    wd.y0, wd.y1 = 300 + r * 24, 318 + r * 24
                lines.append(Line(ws))
    if junk_conf is not None:
        for r in range(30):
            lines.append(Line([Word("clean", junk_conf, 40 + k * 60, 1500 + r * 24, 90 + k * 60, 1518 + r * 24) for k in range(8)]))
    return lines


TEXT = ("investors dumped french government bonds yesterday as violent protests spread across the country pushing "
        "the gap between french and german borrowing costs to its widest level since the eurozone debt crisis ").split() * 6


def test_gate_low_confidence_from_shown_text_only():
    r = extract_splash(Image.new("L", (10, 10)), lines=page(TEXT[:200], conf=60, junk_conf=99))
    assert not r.ok and "confidence" in r.reason


def test_gate_garbled_text_fails_dictionary():
    garbled = ["xqzt", "brrnk", "flimq", "zzorp", "qwvex", "dumped", "french"] * 30
    r = extract_splash(Image.new("L", (10, 10)), lines=page(garbled[:200]))
    assert not r.ok and "dictionary" in r.reason


def test_dictionary_unavailable_fails_closed(monkeypatch):
    monkeypatch.setattr(S, "_SPELL", None)
    import builtins
    real_import = builtins.__import__

    def no_spell(name, *a, **k):
        if name == "spellchecker":
            raise ImportError("missing")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_spell)
    assert S._dict_ratio(["investors", "dumped"]) == 0.0


def test_clean_hand_built_page_passes():
    r = extract_splash(Image.new("L", (10, 10)), lines=page(TEXT[:200]))
    assert r.ok, r.reason
