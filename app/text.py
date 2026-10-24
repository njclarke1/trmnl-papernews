"""Text preparation, done in Python so the template never has to (§6 fit rules).

- Soft hyphens (Pyphen en_GB, words of 6+ letters), never on a paragraph's final word.
- Display copy (headlines, decks, rail) is never hyphenated; compounds are kept whole.
- Headline line budget from plain-text length: ≤70 → 2, ≤110 → 3, else 4.
"""
from __future__ import annotations

import html
import re

import pyphen

_HY = pyphen.Pyphen(lang="en_GB")
_LONG = re.compile(r"[A-Za-z]{6,}")
_COMPOUND = re.compile(r"\b(\w+(?:-\w+)+)\b")
_SENT = re.compile(r"(?<=[.!?’”\"])\s+(?=[A-Z‘“\"0-9£€$])")


def clean(s: str | None) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def shy(text: str, escape: bool = True) -> str:
    """Soft hyphens on all but the final word. escape=False for text set via textContent (flow tokens)."""
    text = html.escape(clean(text), quote=False) if escape else clean(text)
    head, sep, last = text.rpartition(" ")
    if not sep:
        return text
    return _LONG.sub(lambda m: _HY.inserted(m.group(0), hyphen="­"), head) + " " + last


def display(text: str) -> str:
    """Escaped display copy with hyphenated compounds held together."""
    t = html.escape(clean(text), quote=False)
    return _COMPOUND.sub(r'<span class="nw">\1</span>', t)


def headline_lines(headline: str) -> int:
    n = len(clean(headline))
    return 2 if n <= 70 else 3 if n <= 110 else 4


def flow_tokens(paragraphs: list[str]) -> list[dict]:
    """Sentences with paragraph starts marked, for the template's word-flow script."""
    out = []
    for para in paragraphs:
        for i, s in enumerate(_SENT.split(clean(para))):
            if s:
                out.append({"t": shy(s, escape=False), "p": i == 0})
    return out


BOILERPLATE = re.compile(
    r"^[-•·\s]*(published|updated|image source|image caption|media caption|getty images|"
    r"become an independent member|want to bookmark|join today|already a member|sign up to|"
    r"read more:|related:|follow us|share this|listen to this article)\b", re.I)


def paragraphs_from_blob(text: str) -> list[str]:
    """Split extracted article text into paragraphs (newline-separated or one blob), dropping
    page furniture the extractor lets through (BBC "Published", Independent membership prompts)."""
    parts = [clean(p) for p in re.split(r"\n\s*\n|\n", text or "")]
    return [p for p in parts if len(p) > 1 and not BOILERPLATE.match(p)]


def plain_words(text: str) -> int:
    return len(clean(text).split())
