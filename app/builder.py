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

from . import clock, config, images, layout, store, weather
from .model import Edition, Story
from .papers import PAPERS
from .sources import bbc_papers, demo, gnews, papers_review, printfront
from .sources.base import Http, NotPublished, ShapeError, SourceUnavailable
from .sources.guardian import GuardianAdapter
from .sources.nyt import NytAdapter
from .sources.web import WebAdapter

log = logging.getLogger("frontpage")
IMAGE_SIZE = {"full": "full", "summary": "wide", "poster": "wide", "bulletin": "wide", "briefing": "side"}


class Builder:
    def __init__(self, http: Http | None = None):
        self.http = http or Http()
        self._lock = threading.Lock()
        self._reviews: dict[tuple, dict] = {}       # (day, source) → entries; successes only
        self._review_failed: dict[tuple, float] = {}  # (day, source) → when it last failed
        self._rot_lock = threading.Lock()
        self._rot_pos: int | None = None            # index into config.ROTATION (poll mode)
        self._rot_at = 0.0
        self._pruned = None                          # day /data was last pruned
        self.served: list[tuple[str, str]] = []      # (time, paper) of recent /edition polls, for /healthz

    # ------------------------------------------------------------------ one paper
    REVIEW_SOURCES = {"pa": papers_review.fetch, "bbc": bbc_papers.fetch}

    def _review_source(self, day, name: str, backoff_s: int = 240) -> dict:
        """One round-up for the morning of `day`, persisted once found (PA's drops off its index by
        morning, so it is fetched the night before — see prefetch_reviews)."""
        key = (day.isoformat(), name)
        if key in self._reviews:
            return self._reviews[key]
        hit = store.cache_get("review", f"{name}-{day.isoformat()}")
        if hit:
            self._reviews[key] = {k: papers_review.ReviewEntry(**v) for k, v in hit.items()}
            return self._reviews[key]
        if time.time() - self._review_failed.get(key, 0) < backoff_s:
            return {}                         # failed recently: don't refetch for every paper
        try:
            got = self.REVIEW_SOURCES[name](self.http, day.isoformat())
        except SourceUnavailable as e:
            log.warning("papers review (%s) unavailable: %s", name, e)
            got = {}
        if got:
            self._reviews[key] = got
            store.cache_put("review", f"{name}-{day.isoformat()}", {k: e.__dict__ for k, e in got.items()})
        else:
            self._review_failed[key] = time.time()    # not cached: a later retry looks again
        return got

    def prefetch_reviews(self, at: datetime) -> None:
        """21:00–05:59: collect tomorrow morning's round-ups while they are on their index pages."""
        if config.DEMO_MODE or not (at.hour >= 21 or at.hour < 6):
            return
        day = (at + timedelta(days=1)).date() if at.hour >= 21 else at.date()
        for name in self.REVIEW_SOURCES:
            self._review_source(day, name, backoff_s=900)

    def _review(self, slot: clock.Slot) -> dict:
        if slot.kind != "am" or config.DEMO_MODE:
            return {}
        entries, conflict = papers_review.combine(self._review_source(slot.day, "pa"), self._review_source(slot.day, "bbc"))
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
            if str(slot.day.weekday()) not in paper.print_days:
                raise NotPublished(f"{paper.key} does not print on {slot.day:%A}s")
            entry = self._review(slot).get(key)
            if paper.source == "print":
                ed = self._print_only(slot, paper, entry)
            elif paper.source == "guardian":
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
                if ed.kind == "print_front" and print_lead is None:      # splash named by the round-ups only
                    ed.note = "Splash named by the paper reviews"
        ed.built_at = ed.built_at or clock.now().isoformat(timespec="seconds")
        if not ed.note:
            ed.note = (paper.note_print if ed.kind == "print_front" and paper.note_print
                       else "Latest stories · from the feed" if ed.kind == "latest" else paper.note_web)
        ed.layout = layout.choose(ed, paper)
        ed.image = self.build_image(ed)
        if not ed.image and ed.layout in layout.NEEDS_IMAGE:
            ed.layout = "briefing"                      # photo layouts need a photo that actually processed
            ed.image = self.build_image(ed)
        return ed

    def _print_only(self, slot: clock.Slot, paper, entry) -> Edition:
        """Papers that block automated visitors: the morning printed front, held all day."""
        if slot.kind == "pm":                       # the evening edition is the morning's front, unchanged
            am = store.load_edition(f"{slot.day.isoformat()}-am", paper.key)
            if am is not None:
                return am                           # no morning front: today's paper is still read below
        f = printfront.front(self.http, paper, slot.day,
                             expected_headline=entry.quoted if entry else "",
                             expected_standfirst=entry.summary if entry else "")
        rail = [Story(headline=h) for h in (f.others if f else [])]
        if f and f.story:
            on_front = len(rail)
            if on_front < 3:                            # few headlines read off the front: top up from Google News
                seen = {s.headline.lower() for s in rail} | {f.story.headline.lower()}
                rail += [s for s in self._gnews(paper) if s.headline.lower() not in seen][:5 - on_front]
            ed = Edition(paper.key, "print_front", f.story, rail, continued_page=f.continued,
                         rail_front=min(on_front, 5) if len(rail) > on_front else -1)
        elif entry and rail:                        # splash unreadable, rest of the front read: the round-up's splash
            head = papers_review.as_headline(entry.quoted) or papers_review.headline_from(entry.summary)
            deck = "" if papers_review.headline_from(entry.summary) == head else entry.summary
            ed = Edition(paper.key, "print_front", Story(headline=head, standfirst=deck, is_free=False), rail,
                         note="Printed front · splash from the paper reviews")
        else:                                       # no front yet: Google News headlines stand in while we keep looking
            latest = self._gnews_edition(paper)
            if slot.kind == "pm" and latest:            # evening with no morning front: the headlines are the edition
                return latest
            if latest:
                store.cache_put("standin", f"{paper.key}-{slot.id}", latest.to_dict())
            raise SourceUnavailable(f"{paper.key}: printed front not readable yet")
        ed.built_at = clock.now().isoformat(timespec="seconds")
        return ed

    def _gnews(self, paper) -> list[Story]:
        return gnews.headlines(self.http, paper.gnews, paper.key) if paper.gnews else []

    def _gnews_edition(self, paper) -> Edition | None:
        heads = self._gnews(paper)
        if len(heads) < 4:
            return None
        return Edition(paper.key, "latest", heads[0], heads[1:6], note="Latest headlines · via Google News",
                       built_at=clock.now().isoformat(timespec="seconds"), rail_front=0)

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
        try:
            if not config.DEMO_MODE:
                weather.current()                   # hourly; requests only read the cache
        except Exception:
            log.exception("weather refresh failed")
        try:
            self.prefetch_reviews(at.astimezone(clock.LONDON))
        except Exception:
            log.exception("round-up prefetch failed")
        slot = clock.edition_for(at)
        if self._pruned != slot.day:                # once a day: keep /data from growing forever
            try:
                log.info("pruned %d old files", store.prune(now=at.timestamp()))
            except OSError:
                log.exception("prune failed")
            self._pruned = slot.day
        state = store.load_state(slot.id)
        now_s = at.timestamp()
        for key in dict.fromkeys(config.ROTATION):          # a paper listed twice is still built once
            st = state.setdefault(key, {"status": "pending", "attempts": 0, "first": 0, "last": 0, "error": ""})
            if st["status"] in ("ok", "carried", "missing", "not_published"):
                continue
            # an edition already on disk is locked, even if a restart lost the state entry
            if store.load_edition(slot.id, key) is not None:
                st["status"] = "ok"
                store.save_state(slot.id, state)
                continue
            # the retry window runs from the lock, or from the first attempt if we started late
            start = max(slot.lock_at.timestamp(), st.get("first") or now_s)
            window_end = start + config.RETRY_WINDOW_MIN * 60
            if PAPERS[key].source == "print":       # fronts are posted through the morning: keep looking all slot
                window_end = float("inf")
            if st["attempts"] and now_s > window_end:
                self._carry(slot, key, st)
                store.save_state(slot.id, state)
                continue
            every = config.RETRY_EVERY_MIN * 60
            if PAPERS[key].source == "print" and st["attempts"] >= 12:
                every = max(every, 20 * 60)             # after the first hour, as often as tomorrowspapers is re-read
            if st["attempts"] and now_s - st["last"] < every:
                continue
            st["attempts"] += 1
            st["first"] = st.get("first") or now_s
            st["last"] = now_s
            try:
                ed = self.build_one(slot, key)
                store.save_edition(slot.id, ed)
                st.update(status="ok", error="", kind=ed.kind, layout=ed.layout)
                log.info("%s %s: %s / %s", slot.id, key, ed.kind, ed.layout)
            except NotPublished as e:
                st.update(status="not_published", error=str(e))
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
        return {"slot": slot.id, "papers": store.load_state(slot.id), "recent_polls": self.served}

    def _carry(self, slot: clock.Slot, key: str, st: dict) -> None:
        prev = store.last_good(key)
        if prev is None or PAPERS[key].source == "print":     # yesterday's printed front is not today's paper
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
                    self.served = (self.served + [(at.strftime("%H:%M:%S"), k)])[-15:]
                    return k
        return None

    @staticmethod
    def _stand_in(key: str, slot: clock.Slot):
        """The copy shown while a paper's edition is being built. A print-only paper's last good copy is an
        earlier day's front, so it shows only this slot's Google News headlines, if any (else nothing)."""
        if key in PAPERS and PAPERS[key].source == "print":
            d = store.cache_get("standin", f"{key}-{slot.id}")
            return Edition.from_dict(d) if d else None
        return store.last_good(key)

    def current(self, at: datetime | None = None, paper: str | None = None):
        """(slot, edition, preparing) for the paper to show now; falls back to the last good copy."""
        at = at or clock.now()
        slot = clock.edition_for(at)
        state = store.load_state(slot.id)
        available = [k for k in dict.fromkeys(config.ROTATION)
                     if state.get(k, {}).get("status") != "not_published"
                     and (store.load_edition(slot.id, k) or self._stand_in(k, slot))]
        key = paper or self.next_paper(at, available)
        if key is None:
            return slot, None, True
        ed = store.load_edition(slot.id, key)
        preparing = False
        if ed is None:
            ed = self._stand_in(key, slot)
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
