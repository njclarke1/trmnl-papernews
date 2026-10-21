"""Editions and rotation. All date maths in Europe/London via zoneinfo.

Two locked editions a day. Before the morning lock, the previous day's evening edition is current,
so overnight shows the evening news until 06:00.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import config

LONDON = ZoneInfo(config.TZ)


@dataclass(frozen=True)
class Slot:
    id: str                 # 2026-10-21-am
    kind: str               # am | pm
    day: date
    lock_at: datetime       # tz-aware

    @property
    def label(self) -> str:
        return "Morning edition" if self.kind == "am" else "Evening edition"


def now() -> datetime:
    return datetime.now(LONDON)


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _lock(day: date, kind: str) -> datetime:
    t = _hm(config.AM_LOCK if kind == "am" else config.PM_LOCK)
    return datetime.combine(day, t, tzinfo=LONDON)


def edition_for(at: datetime) -> Slot:
    at = at.astimezone(LONDON)
    d = at.date()
    am, pm = _lock(d, "am"), _lock(d, "pm")
    if at >= pm:
        return Slot(f"{d.isoformat()}-pm", "pm", d, pm)
    if at >= am:
        return Slot(f"{d.isoformat()}-am", "am", d, am)
    y = d - timedelta(days=1)
    return Slot(f"{y.isoformat()}-pm", "pm", y, _lock(y, "pm"))


def issue_number(d: date) -> int:
    return d.timetuple().tm_yday


def rotation_paper(at: datetime, available: list[str]) -> tuple[str | None, int]:
    """Paper for this slot and its index in the configured rotation (for the footer dots).

    Derived from the clock, so restarts don't reset it and every poll inside a slot agrees.
    Papers with no edition are skipped rather than shown blank.
    """
    if config.PIN and config.PIN in available:
        return config.PIN, config.ROTATION.index(config.PIN) if config.PIN in config.ROTATION else 0
    order = [p for p in config.ROTATION if p in available]
    if not order:
        return None, 0
    minutes = int(at.timestamp() // 60)
    key = order[(minutes // config.SLOT_MINUTES) % len(order)]
    return key, config.ROTATION.index(key)


def folio_date(d: date) -> str:
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B %Y')}"
