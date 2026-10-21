"""Extract the splash (lead story) from a printed front-page image with Tesseract.

Used only for papers with no free web text (FT, The Times), and only for the morning edition.
Strategy: Tesseract word boxes → lines → classify lines by glyph height relative to body type →
tallest headline below the masthead is the splash → deck and byline beneath it → body lines in the
columns under the headline, read column by column, stopping at "Continued on page N".

It never guesses. If any gate fails, `ok` is False and the caller falls back to the summary layout,
so garbled text never appears under a masthead.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from PIL import Image, ImageOps

CONTINUED_RE = re.compile(r"continued\s+on\s+(?:page\s+)?([A-Z]?\d{1,2})\b", re.I)
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")
STOP = set("a an and are as at be but by for from has have he her his in is it its of on or that the their this "
           "to was were which will with would after over under into than then there they not also been".split())


@dataclass
class Word:
    text: str
    conf: float
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def h(self) -> int:
        return self.y1 - self.y0


@dataclass
class Line:
    words: list[Word]

    @property
    def x0(self): return min(w.x0 for w in self.words)
    @property
    def x1(self): return max(w.x1 for w in self.words)
    @property
    def y0(self): return min(w.y0 for w in self.words)
    @property
    def y1(self): return max(w.y1 for w in self.words)
    @property
    def h(self): return statistics.median(w.h for w in self.words)
    @property
    def cx(self): return (self.x0 + self.x1) / 2
    @property
    def text(self): return " ".join(w.text for w in self.words)


@dataclass
class SplashResult:
    ok: bool
    reason: str = ""
    headline: str = ""
    deck: str = ""
    byline: str = ""
    paragraphs: list[str] = field(default_factory=list)
    continued_page: str | None = None          # "4", or "A8" for the NYT
    words: int = 0
    mean_conf: float = 0.0
    dict_ratio: float = 0.0
    debug: dict = field(default_factory=dict)

    @property
    def body(self) -> str:
        return "\n\n".join(self.paragraphs)


# --------------------------------------------------------------------------- OCR → lines
def ocr_lines(img: Image.Image, upscale_below: int = 2000) -> tuple[list[Line], float]:
    """Run Tesseract and return lines in image coordinates of the *upscaled* image, plus the scale."""
    import pytesseract

    im = ImageOps.grayscale(img)
    scale = 2.0 if im.width < upscale_below else 1.0
    if scale != 1.0:
        im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
    d = pytesseract.image_to_data(im, config="--psm 3", output_type=pytesseract.Output.DICT)
    groups: dict[tuple, list[Word]] = {}
    for i, t in enumerate(d["text"]):
        t = (t or "").strip()
        conf = float(d["conf"][i])
        if not t or conf < 0:
            continue
        key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        groups.setdefault(key, []).append(
            Word(t, conf, d["left"][i], d["top"][i], d["left"][i] + d["width"][i], d["top"][i] + d["height"][i]))
    lines: list[Line] = []
    for ws in groups.values():
        ws.sort(key=lambda w: w.x0)
        # split a Tesseract line only if it clearly bridges two columns: a gap far wider than any
        # justified word space (justified gaps reach ~4x word height on loosely set synthetic text)
        hmed = statistics.median(w.h for w in ws)
        cur = [ws[0]]
        for w in ws[1:]:
            if w.x0 - cur[-1].x1 > 8.0 * hmed:
                lines.append(Line(cur)); cur = [w]
            else:
                cur.append(w)
        lines.append(Line(cur))
    lines.sort(key=lambda l: (l.y0, l.x0))
    return lines, scale


# --------------------------------------------------------------------------- analysis
def _body_height(lines: list[Line]) -> float:
    hs = [round(l.h) for l in lines if len(l.words) >= 4]
    if not hs:
        raise ValueError("no body-like lines")
    return float(statistics.mode(hs))


def _tokens(s: str) -> set[str]:
    return {w.lower().strip("'’-") for w in WORD_RE.findall(s)} - STOP


def _overlap(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / max(1, min(len(ta), len(tb)))


def _clean(s: str) -> str:
    s = re.sub(r"\s+[|¦]\s+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.replace(" ,", ",").replace(" .", ".")


def extract_splash(img: Image.Image, *, paper_name: str = "", expected_headline: str = "",
                   expected_standfirst: str = "", min_words: int = 150, min_conf: float = 85.0,
                   min_dict_ratio: float = 0.85, lines: list[Line] | None = None) -> SplashResult:
    if lines is None:
        lines, _ = ocr_lines(img)
    if not lines:
        return SplashResult(False, "no text found")
    page_h = max(l.y1 for l in lines)
    try:
        hb = _body_height(lines)
    except ValueError as e:
        return SplashResult(False, str(e))

    # masthead: the paper's name, else the tallest line in the top 15% of the page
    top = [l for l in lines if l.y1 < 0.15 * page_h]
    mast = None
    if paper_name:
        mast = next((l for l in top if _overlap(l.text, paper_name) >= 0.6), None)
    if mast is None and top:
        mast = max(top, key=lambda l: l.h)
    floor_y = mast.y1 if mast else 0

    # splash headline: tallest group of lines below the masthead in the top 65%
    cands = [l for l in lines if l.y0 > floor_y and l.y0 < 0.65 * page_h and l.h >= 2.0 * hb and l is not mast]
    if expected_headline:
        scored = [l for l in cands if _overlap(l.text, expected_headline) >= 0.5]
        cands = scored or cands
    if not cands:
        return SplashResult(False, "no headline-sized type below the masthead")
    hmax = max(l.h for l in cands)
    first = min((l for l in cands if l.h >= 0.8 * hmax), key=lambda l: l.y0)
    head = [first]
    for l in sorted(cands, key=lambda l: l.y0):
        if l is first or l.y0 < first.y0:
            continue
        prev = head[-1]
        if abs(l.h - first.h) <= 0.25 * first.h and l.y0 - prev.y1 < 1.2 * first.h and l.x0 < prev.x1 and l.x1 > prev.x0:
            head.append(l)
    headline = _clean(" ".join(l.text for l in head))
    hx0, hx1, hy1 = min(l.x0 for l in head), max(l.x1 for l in head), max(l.y1 for l in head)
    if expected_headline and _overlap(headline, expected_headline) < 0.6:
        return SplashResult(False, "headline does not match the expected splash", headline=headline,
                            debug={"expected": expected_headline})

    # deck (standfirst) and byline directly beneath the headline
    below = [l for l in lines if l.y0 >= hy1 - 2 and l.cx > hx0 and l.cx < hx1]
    deck_lines, by_lines, y = [], [], hy1
    for l in sorted(below, key=lambda l: l.y0):
        if l in head:
            continue
        if l.y0 - y > 2.0 * max(hb, l.h):
            break
        if 1.25 * hb < l.h < 0.8 * first.h and not by_lines:
            deck_lines.append(l); y = l.y1
        elif l.h <= 1.3 * hb and _is_byline(l):
            by_lines.append(l); y = l.y1
        else:
            break
    deck = _clean(" ".join(l.text for l in deck_lines))
    byline = _clean(" ".join(l.text for l in by_lines))
    body_floor = y

    # body: body-sized lines under the headline, inside its horizontal span. Tesseract's box height
    # for a short line swings with its ascenders/descenders (a rendered PDF put "Investors dumped
    # French" at 1.28× body), so allow 1.4× here: the deck has already been taken out above.
    tol = 1.5 * hb
    body = [l for l in lines if l.y0 >= body_floor - 2 and 0.8 * hb <= l.h <= 1.4 * hb
            and l.x0 >= hx0 - tol and l.x1 <= hx1 + tol and l not in by_lines and l not in deck_lines]
    # include the continuation line even if it is set in a different size
    cont = [l for l in lines if CONTINUED_RE.search(l.text) and l.y0 >= body_floor and hx0 - tol <= l.cx <= hx1 + tol]
    if not body:
        return SplashResult(False, "no body text under the headline", headline=headline, deck=deck)
    pitch = statistics.median(sorted(b.y0 - a.y0 for a, b in zip(sorted(body, key=lambda l: l.y0),
                                                                  sorted(body, key=lambda l: l.y0)[1:]) if 0 < b.y0 - a.y0 < 3 * hb) or [hb * 1.3])
    body_top = min(l.y0 for l in body)

    columns = _columns(body + cont, hb)
    paras: list[str] = []
    shown: list[Line] = []
    cont_page = None
    cur: list[str] = []
    for col in columns:
        col.sort(key=lambda l: l.y0)
        if col[0].y0 > body_top + 3 * pitch:      # starts well below the splash body: another story or a caption
            continue
        col_x0 = statistics.median(l.x0 for l in col)
        prev = None
        for l in col:
            if prev is not None and l.y0 - prev.y1 > 2.5 * pitch:
                break                                   # big gap: the story has ended in this column
            m = CONTINUED_RE.search(l.text)
            if m:
                cont_page = m.group(1).upper(); break
            # a heading-sized line between body lines means a new story
            between = [x for x in lines if prev is not None and prev.y1 <= x.y0 < l.y0 and x.h > 1.5 * hb
                       and x.x0 < l.x1 and x.x1 > l.x0]
            if between:
                break
            if prev is None and not paras and not cur and _is_byline(l):
                byline = byline or _clean(l.text); continue
            indent = l.x0 - col_x0 > 0.6 * hb
            if cur and indent:
                paras.append(_join(cur)); cur = []
            cur.append(l.text)
            shown.append(l)
            prev = l
        if cont_page:
            break
    if cur:
        paras.append(_join(cur))
    paras = [_clean(p) for p in paras if p.strip()]

    words = [w for l in shown for w in l.words]          # only the text that will be displayed
    text = " ".join(paras)
    toks = WORD_RE.findall(text)
    conf = statistics.mean(w.conf for w in words) if words else 0.0
    ratio = _dict_ratio(toks)
    res = SplashResult(False, "", headline, deck, byline, paras, cont_page, len(text.split()), round(conf, 1),
                       round(ratio, 3), debug={"body_height": hb, "pitch": pitch, "columns": len(columns)})
    if res.words < min_words:
        res.reason = f"only {res.words} words (< {min_words})"
    elif conf < min_conf:
        res.reason = f"mean confidence {conf:.0f} (< {min_conf:.0f})"
    elif ratio < min_dict_ratio:
        res.reason = f"dictionary ratio {ratio:.2f} (< {min_dict_ratio})"
    elif expected_standfirst and len(_tokens(paras[0] if paras else "") & _tokens(expected_standfirst)) < 3 \
            and len(_tokens(text) & _tokens(expected_standfirst)) < 3:
        res.reason = "body does not share 3 content words with the standfirst"
    else:
        res.ok = True
    return res


def _is_byline(l: Line) -> bool:
    return len(l.words) <= 8 and (bool(re.match(r"^by\b", l.text, re.I)) or _upper_ratio(l.text) > 0.7)


def _upper_ratio(s: str) -> float:
    letters = [c for c in s if c.isalpha()]
    return sum(c.isupper() for c in letters) / max(1, len(letters))


def _columns(lines: list[Line], hb: float) -> list[list[Line]]:
    cols: list[list[Line]] = []
    for l in sorted(lines, key=lambda l: l.x0):
        for c in cols:
            cx0 = statistics.median(x.x0 for x in c)
            if abs(l.x0 - cx0) <= 3 * hb:
                c.append(l); break
        else:
            cols.append([l])
    return sorted(cols, key=lambda c: statistics.median(x.x0 for x in c))


def _join(texts: list[str]) -> str:
    out = ""
    for t in texts:
        if out.endswith("-") and t[:1].islower():
            out = out[:-1] + t
        else:
            out = f"{out} {t}" if out else t
    return out


_SPELL = None


def _dict_ratio(tokens: list[str]) -> float:
    global _SPELL
    words = [t.lower().strip("'’-") for t in tokens if len(t) >= 2]
    if not words:
        return 0.0
    try:
        if _SPELL is None:
            from spellchecker import SpellChecker
            _SPELL = SpellChecker(language="en")
        known = _SPELL.known(words)
        return sum(w in known for w in words) / len(words)
    except Exception:                              # dictionary unavailable: fail closed, never pass unchecked text
        return 0.0
