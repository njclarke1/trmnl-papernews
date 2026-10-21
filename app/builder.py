"""Build and lock editions.

At each lock (06:00, 16:00) every paper is built once. Failures retry every RETRY_EVERY_MIN until
RETRY_WINDOW_MIN after the lock; a paper that still fails carries over its last good copy, tagged
CACHED. A stored edition is never rebuilt, so content stays frozen until the next lock and a
restart doesn't change what's on screen.
"""
from __future__ import annotations

import logging
import pathlib
import threading
import time
from datetime import datetime, timedelta

from . import clock, config, images, layout, store
from .model import Edition
from .papers import PAPERS
from .sources import demo, papers_review, printfront
from .sources.base import Http, ShapeError, SourceUnavailable
from .sources.guardian import GuardianAdapter
from .sources.web import WebAdapter

log = logging.getLogger("frontpage")
IMAGE_SIZE = {"full": "full", "summary": "wide", "poster": "wide", "bulletin": "wide", "briefing": "side"}


class Builder:
    def __init__(self, http: Http | None = None):
        self.http = http or Http()
        self._lock = threading.Lock()
        self._reviews: dict[str, dict] = {}

    # ------------------------------------------------------------------ one paper
    def _review(self, slot: clock.Slot) -> dict:
        if slot.kind != "am" or config.DEMO_MODE:
            return {}
        if slot.id not in self._reviews:
            try:
                self._reviews[slot.id] = papers_review.fetch(self.http, slot.day.isoformat())
            except SourceUnavailable as e:
                log.warning("papers review unavailable: %s", e)
                return {}                     # don't cache a failure: the next retry looks again
        return self._reviews[slot.id]

    def _raw(self, url: str) -> bytes:
        if url.startswith("file://"):
            return pathlib.Path(url[7:]).read_bytes()
        return self.http.get(url, binary=True).content

    def build_one(self, slot: clock.Slot, key: str) -> Edition:
        paper = PAPERS[key]
        if config.DEMO_MODE:
            ed = demo.edition(key)
        else:
            entry = self._review(slot).get(key)
            if paper.source == "guardian":
                ed = GuardianAdapter(self.http).build(slot.kind, slot.day.isoformat(), entry)
            else:
                print_lead, cont = None, None
                if slot.kind == "am" and paper.print_front:
                    res = printfront.splash(self.http, paper, slot.day,
                                            expected_headline=entry.quoted if entry else "",
                                            expected_standfirst=entry.summary if entry else "")
                    if res:
                        print_lead, cont = res
                ed = WebAdapter(paper, self.http).build(slot.kind, slot.day.isoformat(), entry, print_lead)
                ed.continued_page = cont
        ed.built_at = ed.built_at or clock.now().isoformat(timespec="seconds")
        ed.note = ed.note or (paper.note_print if ed.kind == "print_front" and paper.note_print else paper.note_web)
        ed.layout = layout.choose(ed, paper)
        ed.image = self.build_image(ed)
        if not ed.image and ed.layout in layout.NEEDS_IMAGE:
            ed.layout = "briefing"                      # photo layouts need a photo that actually processed
            ed.image = self.build_image(ed)
        return ed

    def build_image(self, ed: Edition) -> str:
        return images.cached_photo(ed.lead.image_url, self._raw, IMAGE_SIZE.get(ed.layout, "wide")) or ""

    # ------------------------------------------------------------------ the edition
    def tick(self, at: datetime | None = None) -> dict:
        if not self._lock.acquire(blocking=False):
            return {}
        try:
            return self._tick(at or clock.now())
        finally:
            self._lock.release()

    def _tick(self, at: datetime) -> dict:
        slot = clock.edition_for(at)
        state = store.load_state(slot.id)
        window_end = slot.lock_at + timedelta(minutes=config.RETRY_WINDOW_MIN)
        changed = False
        for key in config.ROTATION:
            st = state.setdefault(key, {"status": "pending", "attempts": 0, "last": 0, "error": ""})
            if st["status"] in ("ok", "carried", "missing"):
                continue
            if st["attempts"] and at > window_end:
                self._carry(slot, key, st)
                changed = True
                continue
            if time.time() - st["last"] < config.RETRY_EVERY_MIN * 60:
                continue
            st["attempts"] += 1
            st["last"] = time.time()
            changed = True
            try:
                ed = self.build_one(slot, key)
                store.save_edition(slot.id, ed)
                st.update(status="ok", error="", kind=ed.kind, layout=ed.layout)
                log.info("%s %s: %s / %s", slot.id, key, ed.kind, ed.layout)
            except (SourceUnavailable, ShapeError) as e:
                st.update(status="failed", error=str(e), data_check=isinstance(e, ShapeError))
                log.warning("%s %s: %s", slot.id, key, e)
            except Exception as e:                          # never let one paper break the edition
                st.update(status="failed", error=f"{type(e).__name__}: {e}")
                log.exception("%s %s crashed", slot.id, key)
            if st["status"] == "failed" and at > window_end:
                self._carry(slot, key, st)
        if changed:
            store.save_state(slot.id, state)
        return {"slot": slot.id, "papers": state}

    def _carry(self, slot: clock.Slot, key: str, st: dict) -> None:
        prev = store.last_good(key)
        if prev is None:
            st["status"] = "missing"
            return
        prev.stale = True
        prev.data_check = bool(st.get("data_check"))
        store.save_edition(slot.id, prev)
        st["status"] = "carried"

    # ------------------------------------------------------------------ reading
    def current(self, at: datetime | None = None, paper: str | None = None):
        """(slot, edition, preparing) for the paper to show now; falls back to the last good copy."""
        at = at or clock.now()
        slot = clock.edition_for(at)
        state = store.load_state(slot.id)
        available = [k for k in config.ROTATION if store.load_edition(slot.id, k) or store.last_good(k)]
        key = paper or clock.rotation_paper(at, available)[0]
        if key is None:
            return slot, None, True
        ed = store.load_edition(slot.id, key)
        preparing = False
        if ed is None:
            ed = store.last_good(key)
            preparing = state.get(key, {}).get("status") in (None, "pending", "failed")
        return slot, ed, preparing


def run_background(builder: Builder, every_s: int = 60) -> threading.Thread:
    def loop():
        while True:
            try:
                builder.tick()
            except Exception:
                log.exception("tick failed")
            time.sleep(every_s)
    t = threading.Thread(target=loop, name="edition-builder", daemon=True)
    t.start()
    return t
