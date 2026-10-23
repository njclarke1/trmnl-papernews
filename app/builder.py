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
from datetime import datetime

from . import clock, config, images, layout, store
from .model import Edition
from .papers import PAPERS
from .sources import bbc_papers, demo, papers_review, printfront
from .sources.base import Http, ShapeError, SourceUnavailable
from .sources.guardian import GuardianAdapter
from .sources.nyt import NytAdapter
from .sources.web import WebAdapter

log = logging.getLogger("frontpage")
IMAGE_SIZE = {"full": "full", "summary": "wide", "poster": "wide", "bulletin": "wide", "briefing": "side"}


class Builder:
    def __init__(self, http: Http | None = None):
        self.http = http or Http()
        self._lock = threading.Lock()
        self._reviews: dict[tuple, dict] = {}       # (slot, source) → entries; successes only
        self._review_failed: dict[tuple, float] = {}  # (slot, source) → when it last failed
        self._rot_lock = threading.Lock()
        self._rot_pos: int | None = None            # index into config.ROTATION (poll mode)
        self._rot_at = 0.0

    # ------------------------------------------------------------------ one paper
    REVIEW_SOURCES = {"pa": papers_review.fetch, "bbc": bbc_papers.fetch}

    def _review_source(self, slot: clock.Slot, name: str) -> dict:
        key = (slot.id, name)
        if key in self._reviews:
            return self._reviews[key]
        if time.time() - self._review_failed.get(key, 0) < 240:
            return {}                         # failed this tick: don't refetch for every paper
        try:
            got = self.REVIEW_SOURCES[name](self.http, slot.day.isoformat())
        except SourceUnavailable as e:
            log.warning("papers review (%s) unavailable: %s", name, e)
            got = {}
        if got:
            self._reviews[key] = got
        else:
            self._review_failed[key] = time.time()    # not cached: a later retry looks again
        return got

    def _review(self, slot: clock.Slot) -> dict:
        if slot.kind != "am" or config.DEMO_MODE:
            return {}
        entries, conflict = papers_review.combine(self._review_source(slot, "pa"), self._review_source(slot, "bbc"))
        if conflict:
            log.warning("round-ups disagree on the splash for %s: not used", ", ".join(conflict))
        return entries

    def _raw(self, url: str) -> bytes:
        if url.startswith("file://"):
            if not config.DEMO_MODE:               # scraped image URLs must never read local files
                raise SourceUnavailable("file:// image outside demo mode")
            return pathlib.Path(url[7:]).read_bytes()
        if not url.startswith(("http://", "https://")):
            raise SourceUnavailable(f"unsupported image URL scheme: {url[:20]}")
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
                adapter = NytAdapter(self.http) if paper.source == "nyt" else WebAdapter(paper, self.http)
                ed = adapter.build(slot.kind, slot.day.isoformat(), entry, print_lead)
                ed.continued_page = cont if ed.kind == "print_front" else None
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
        now_s = at.timestamp()
        for key in dict.fromkeys(config.ROTATION):          # a paper listed twice is still built once
            st = state.setdefault(key, {"status": "pending", "attempts": 0, "first": 0, "last": 0, "error": ""})
            if st["status"] in ("ok", "carried", "missing"):
                continue
            # an edition already on disk is locked, even if a restart lost the state entry
            if store.load_edition(slot.id, key) is not None:
                st["status"] = "ok"
                store.save_state(slot.id, state)
                continue
            # the retry window runs from the lock, or from the first attempt if we started late
            start = max(slot.lock_at.timestamp(), st.get("first") or now_s)
            window_end = start + config.RETRY_WINDOW_MIN * 60
            if st["attempts"] and now_s > window_end:
                self._carry(slot, key, st)
                store.save_state(slot.id, state)
                continue
            if st["attempts"] and now_s - st["last"] < config.RETRY_EVERY_MIN * 60:
                continue
            st["attempts"] += 1
            st["first"] = st.get("first") or now_s
            st["last"] = now_s
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
            store.save_state(slot.id, state)                # per paper: a restart mid-build loses nothing
        return {"slot": slot.id, "papers": state}

    def status(self, at: datetime | None = None) -> dict:
        """Read-only view of the current edition's build state (for /healthz)."""
        slot = clock.edition_for(at or clock.now())
        return {"slot": slot.id, "papers": store.load_state(slot.id)}

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
    def next_paper(self, at: datetime, available: list[str]) -> str | None:
        """The paper for this fetch. Poll mode advances one place per device fetch (see config)."""
        if config.ROTATION_MODE == "clock" or (config.PIN and config.PIN in available):
            return clock.rotation_paper(at, available)[0]
        rot = config.ROTATION
        if not any(k in available for k in rot):
            return None
        now = at.timestamp()
        with self._rot_lock:
            if self._rot_pos is None:               # first fetch after a start: pick up where the clock is
                first = clock.rotation_paper(at, available)[0]
                self._rot_pos, self._rot_at = rot.index(first), now
            elif now - self._rot_at >= config.MIN_ADVANCE_S:
                self._rot_pos, self._rot_at = self._rot_pos + 1, now
            for step in range(len(rot)):            # skip papers with nothing to show
                k = rot[(self._rot_pos + step) % len(rot)]
                if k in available:
                    self._rot_pos = (self._rot_pos + step) % len(rot)
                    return k
        return None

    def current(self, at: datetime | None = None, paper: str | None = None):
        """(slot, edition, preparing) for the paper to show now; falls back to the last good copy."""
        at = at or clock.now()
        slot = clock.edition_for(at)
        state = store.load_state(slot.id)
        available = [k for k in dict.fromkeys(config.ROTATION) if store.load_edition(slot.id, k) or store.last_good(k)]
        key = paper or self.next_paper(at, available)
        if key is None:
            return slot, None, True
        ed = store.load_edition(slot.id, key)
        preparing = False
        if ed is None:
            ed = store.last_good(key)
            preparing = state.get(key, {}).get("status") in (None, "pending", "failed")
        return slot, ed, preparing


def unique_rotation() -> list[str]:
    return list(dict.fromkeys(config.ROTATION))


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
