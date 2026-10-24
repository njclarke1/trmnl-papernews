"""Edition → the JSON the LaraPaper template reads. Every string is pre-formatted here; Liquid only prints."""
from __future__ import annotations

import html
import json

from . import clock, config, images, text
from .model import Edition
from .papers import PAPERS

RAIL_COUNT = {"full": 3, "summary": 4, "poster": 3, "bulletin": 5, "briefing": 5}


def kicker_kind(kind: str) -> str:
    return {"print_front": "Today’s front page", "web_front": "The lead"}.get(kind, "Latest")


def build(ed: Edition | None, slot: clock.Slot, *, weather: dict | None = None, preparing: bool = False) -> dict:
    base = {
        "asset_base": config.PUBLIC_BASE_URL,
        "folio": {"date": clock.folio_date(slot.day),          # overnight: yesterday's evening edition keeps its date
                  "issue": f"No. {clock.issue_number(slot.day)}", "edition": slot.label, "place": config.PLACE},
        "weather": weather,
        "dots": [{"on": k == (ed.paper if ed else None)} for k in dict.fromkeys(config.ROTATION)],
    }
    if ed is None:
        return {**base, "layout": "empty", "message": "The first edition is being prepared."}

    p = PAPERS[ed.paper]
    lead = ed.lead
    if lead.is_free is False:                    # defence in depth: paid text never reaches the template
        lead.paragraphs = []
    lay = ed.layout or "summary"
    if lay == "full" and not lead.paragraphs:    # never an empty body zone
        lay = "summary" if ed.image else "briefing"
    rail = [s for s in ed.rail if s.headline][: RAIL_COUNT.get(lay, 3)]
    # "Also on the front" only when every rail story really is from that front (the Guardian's and the
    # NYT's printed fronts are topped up from the website when page 1 has few stories)
    front = ed.kind == "web_front" or (ed.kind == "print_front" and (ed.rail_front < 0 or ed.rail_front >= len(rail)))
    if lay == "summary" and len(rail) not in (3, 4):
        rail = rail[:3]
    para_from_deck = lay == "briefing" and not lead.paragraphs
    if ed.continued_page:
        jump = f"Continued in the paper ▸ page {ed.continued_page}"
    else:
        jump = "Continued — scan the code above ▸" if lead.url else "Continued in the paper ▸"
    tag = "CACHED" if ed.stale else "UPDATING" if preparing else ""
    if ed.data_check:
        tag = "DATA CHECK"
    flow = text.flow_tokens(lead.paragraphs) if lay == "full" else []
    return {
        **base,
        "layout": lay,
        "tint": bool(config.FT_TINT and ed.paper == "ft"),
        "paper": {"key": p.key, "name": p.name, "np": p.np, "sub": "THE i PAPER" if p.key == "i" else ""},
        "qr": images.qr_url(lead.url),
        "lead": {
            "kicker": html.escape(lead.section or "News"),
            "kind": kicker_kind(ed.kind),
            "headline": text.display(lead.headline),
            "lines": text.headline_lines(lead.headline),
            "deck": "" if para_from_deck else text.display(lead.standfirst),
            # a deck printed in capitals (NYT banks: "CITING SIMILAR NAMES") is set roman and spaced, not italic
            "deck_caps": sum(c.isupper() for c in lead.standfirst) > 0.8 * max(1, sum(c.isalpha() for c in lead.standfirst)),
            "byline": html.escape(text.clean(lead.byline) or p.short.title()),
            "image": ed.image or None,
            "caption": html.escape(text.clean(" ".join(x for x in (lead.caption, lead.credit) if x))),
            "flow_json": html.escape(json.dumps(flow, ensure_ascii=False), quote=True),
            "para": text.shy(lead.paragraphs[0] if lead.paragraphs else lead.standfirst),
            "jump": jump,
        },
        "rail_head": "Also<br>on the<br>front" if front else "More<br>stories",
        "list_head": "Top stories" if front else "The briefing",
        "rail_grid": lay == "summary" and len(rail) == 4,
        "rail": [{"n": i + 1, "section": html.escape(s.section or ""), "headline": text.display(s.headline),
                  "summary": text.display(s.standfirst) if lay == "bulletin" else ""} for i, s in enumerate(rail)],
        "foot": {"note": html.escape(ed.note), "tag": tag, "built": ed.built_at[:16].replace("T", " ")},
    }
