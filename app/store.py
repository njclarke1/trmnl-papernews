"""Editions and last-good copies on the named Docker volume (survives Portainer redeploys)."""
from __future__ import annotations

import json
import os
import pathlib
import tempfile

from . import config
from .model import Edition


def _root() -> pathlib.Path:
    return pathlib.Path(config.DATA_DIR)


def _write(path: pathlib.Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _read(path: pathlib.Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):                 # missing, unreadable, truncated or not UTF-8
        return None


def save_edition(slot_id: str, ed: Edition) -> None:
    _write(_root() / "editions" / slot_id / f"{ed.paper}.json", ed.to_dict())
    if not ed.stale and not config.DEMO_MODE:   # made-up demo copy must never be carried as news
        _write(_root() / "lastgood" / f"{ed.paper}.json", ed.to_dict())


def _edition(path: pathlib.Path) -> Edition | None:
    d = _read(path)
    try:
        return Edition.from_dict(d) if d else None
    except (TypeError, KeyError, AttributeError):      # format changed beyond repair: treat as absent
        return None


def load_edition(slot_id: str, paper: str) -> Edition | None:
    return _edition(_root() / "editions" / slot_id / f"{paper}.json")


def last_good(paper: str) -> Edition | None:
    return _edition(_root() / "lastgood" / f"{paper}.json")


def save_state(slot_id: str, state: dict) -> None:
    _write(_root() / "editions" / slot_id / "_state.json", state)


def load_state(slot_id: str) -> dict:
    return _read(_root() / "editions" / slot_id / "_state.json") or {}


def cache_get(kind: str, key: str) -> dict | None:
    return _read(_root() / "cache" / kind / f"{key}.json")


def cache_put(kind: str, key: str, data: dict) -> None:
    _write(_root() / "cache" / kind / f"{key}.json", data)
