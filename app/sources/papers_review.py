"""PA's nightly "What the papers say": tonight's printed splash for each paper.

Syndicated to many regional sites; the Express & Star copy is used. Its 2026 format is unverified
until the probe runs — the parser is deliberately forgiving: one paragraph per paper, the paper named
near the start, sometimes with the printed headline in quotes.
"""
from __future__ import annotations

import re
import urllib.parse as up
from dataclasses import dataclass

from bs4 import BeautifulSoup

from .. import text
from ..papers import PAPERS
from .base import Http, match_score, tokens

INDEX = "https://www.expressandstar.com/uk-news/"


@dataclass
class ReviewEntry:
    paper: str
    summary: str
    quoted: str = ""

    @property
    def query(self) -> str:
        return f"{self.quoted} {self.summary}"


def _name_patterns(paper_key: str) -> list[re.Pattern]:
    pats = []
    for n in PAPERS[paper_key].review_names:
        if n == "i":
            pats.append(re.compile(r"(?:^|\bThe )i\b(?! paper)|\bi paper\b|\bthe i\b"))
        elif n in ("Times", "The Times"):
            pats.append(re.compile(r"(?<!Financial )(?<!Sunday )(?<!New York )\b" + re.escape(n) + r"\b(?! of )"))
        elif n == "FT":
            pats.append(re.compile(r"\bFT\b"))
        else:
            pats.append(re.compile(r"\b" + re.escape(n) + r"\b"))
    return pats


QUOTE = re.compile(r"[“\"‘']([^“”\"‘’']{12,140})[”\"’']")


def parse(html_or_text: str) -> dict[str, ReviewEntry]:
    if "<" in html_or_text:
        soup = BeautifulSoup(html_or_text, "lxml")
        paras = [text.clean(p.get_text(" ")) for p in soup.find_all(["p", "li", "h2", "h3"])]
    else:
        paras = [text.clean(p) for p in re.split(r"\n+", html_or_text)]
    paras = [p for p in paras if len(p) > 25]
    out: dict[str, ReviewEntry] = {}
    for key in PAPERS:
        pats = _name_patterns(key)
        for p in paras:
            head = p[:120]
            if any(pt.search(head) for pt in pats):
                q = QUOTE.search(p)
                out[key] = ReviewEntry(key, p, q.group(1).strip() if q else "")
                break
    return out


def as_headline(quoted: str) -> str:
    """A round-up quote usable as the printed headline, or "" (fragments such as 'online safety laws
    have failed to protect children' — lower-case, mid-sentence — keep the paper's own headline)."""
    q = (quoted or "").strip()
    return q if len(q) > 15 and len(q.split()) >= 4 and q[:1].isupper() else ""


def headline_from(summary: str) -> str:
    """A headline-length line from a round-up sentence: 'The Times leads with X.' → 'X'."""
    s = re.sub(r"^(the\s+)?[\w’' ]{1,40}?\b(leads|splashes|opens|reports|says|focuses|carries|runs)\b"
               r"(\s+(with|on|that))?\s+", "", summary.strip(), flags=re.I)
    s = re.split(r"(?<=[.!?])\s", s)[0].rstrip(".")
    return (s[:1].upper() + s[1:])[:140] if s else summary[:140]


def agree(a: ReviewEntry, b: ReviewEntry) -> bool:
    return match_score(a.query, b.query) >= 0.35 and len(tokens(a.query) & tokens(b.query)) >= 2


def combine(pa: dict[str, ReviewEntry], bbc: dict[str, ReviewEntry]) -> tuple[dict[str, ReviewEntry], list[str]]:
    """Two independent round-ups. Both agree, or only one names the splash ⇒ use it (PA's wording first).
    They disagree ⇒ no splash for that paper (its morning edition falls back to the web front, and any
    printed-front OCR runs unchecked). Returns (entries, papers in conflict)."""
    out, conflict = {}, []
    for key in set(pa) | set(bbc):
        a, b = pa.get(key), bbc.get(key)
        if a and b and not agree(a, b):
            conflict.append(key)
            continue
        e = a or b
        if a and b and not a.quoted:
            e = ReviewEntry(key, a.summary, b.quoted)
        out[key] = e
    return out, sorted(conflict)


def fetch(http: Http, day_iso: str) -> dict[str, ReviewEntry]:
    """Latest round-up for `day_iso` (YYYY-MM-DD). Empty dict if not found."""
    idx = http.get(INDEX).text
    soup = BeautifulSoup(idx, "lxml")
    month = {"01": "january", "02": "february", "03": "march", "04": "april", "05": "may", "06": "june", "07": "july",
             "08": "august", "09": "september", "10": "october", "11": "november", "12": "december"}[day_iso[5:7]]
    want = f"what-the-papers-say-{month}-{int(day_iso[8:10])}"
    links = [up.urljoin(INDEX, a["href"]) for a in soup.find_all("a", href=True) if want in a["href"]]
    if not links:
        return {}
    return parse(http.get(links[0]).text)
