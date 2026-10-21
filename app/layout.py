"""Choose a layout from the data that actually arrived (§6), not from the source alone.

full      body text (free) present
summary   standfirst + photo; 2×2 rail when 4 stories, else 3-up
poster    big photo above a big headline
bulletin  photo + numbered list with one-line summaries
briefing  short lead + numbered briefs (headlines only)
"""
from __future__ import annotations

from . import config
from .model import Edition
from .papers import Paper

NEEDS_IMAGE = {"summary", "poster", "bulletin"}


def choose(ed: Edition, paper: Paper) -> str:
    lead = ed.lead
    if lead.is_free is not False and lead.body_chars >= config.MIN_BODY_CHARS:
        return "full"
    # print fronts carry a shorter opening (300–500 words) but are complete to the jump
    if ed.kind == "print_front" and lead.is_free is not False and lead.paragraphs and lead.body_chars >= 900:
        return "full"
    layout = paper.fallback_layout
    if layout in NEEDS_IMAGE and not lead.image_url:
        return "briefing"
    if layout == "bulletin" and not any(s.standfirst for s in ed.rail):
        return "summary" if lead.image_url else "briefing"
    return layout
