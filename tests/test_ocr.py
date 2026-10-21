"""OCR splash extraction on synthetic broadsheet fronts (tools/make_synthetic_fronts.py).
Real fronts will differ: re-tune against the probe's saved images before trusting these thresholds."""
import collections
import json
import re
import shutil

import pytest
from PIL import Image

from app.ocr.splash import extract_splash
from conftest import FIX

pytestmark = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
F = FIX / "fronts"


def words(s):
    return re.findall(r"[a-z]+", s.lower())


def score(result, truth):
    got, want = collections.Counter(words(result.body)), collections.Counter(words(truth["body"]))
    other = set(words(truth["other_text"])) - set(words(truth["body"]))
    recall = sum(min(got[w], n) for w, n in want.items()) / sum(want.values())
    leak = sum(n for w, n in got.items() if w in other)
    return recall, leak


@pytest.mark.parametrize("name,paper", [("ft-1200", "FINANCIAL TIMES"), ("times-1200", "THE TIMES"),
                                        ("ft-1400", "FINANCIAL TIMES")])
def test_extracts_splash_without_other_stories(name, paper):
    truth = json.loads((F / f"{name}.json").read_text())
    r = extract_splash(Image.open(F / f"{name}.jpg"), paper_name=paper, min_words=100)
    recall, leak = score(r, truth)
    assert r.ok, r.reason
    assert recall >= 0.9 and leak == 0
    assert r.continued_page == truth["page"]
    assert set(words(truth["headline"])) <= set(words(r.headline)) | {"line"}
    assert words(truth["deck"])[:4] == words(r.deck)[:4]


def test_rejects_when_headline_does_not_match_expected():
    r = extract_splash(Image.open(F / "ft-1200.jpg"), paper_name="FINANCIAL TIMES", min_words=100,
                       expected_headline="Ministers announce new rail franchise for the north")
    assert not r.ok and "headline" in r.reason


def test_rejects_unreadable_small_image():
    im = Image.open(F / "ft-1000.jpg")
    small = im.resize((640, round(im.height * 640 / im.width)))
    r = extract_splash(small, paper_name="FINANCIAL TIMES", min_words=100)
    assert not r.ok


def test_standfirst_cross_check():
    truth = json.loads((F / "ft-1400.json").read_text())
    ok = extract_splash(Image.open(F / "ft-1400.jpg"), paper_name="FINANCIAL TIMES", min_words=100,
                        expected_standfirst="French bond spreads widen as protests and curfews spread")
    bad = extract_splash(Image.open(F / "ft-1400.jpg"), paper_name="FINANCIAL TIMES", min_words=100,
                         expected_standfirst="Premier League clubs agree new television rights settlement")
    assert ok.ok and not bad.ok and "standfirst" in bad.reason
