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
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_edition(slot_id: str, ed: Edition) -> None:
    _write(_root() / "editions" / slot_id / f"{ed.paper}.json", ed.to_dict())
    if not ed.stale:
        _write(_root() / "lastgood" / f"{ed.paper}.json", ed.to_dict())


def load_edition(slot_id: str, paper: str) -> Edition | None:
    d = _read(_root() / "editions" / slot_id / f"{paper}.json")
    return Edition.from_dict(d) if d else None


def last_good(paper: str) -> Edition | None:
    d = _read(_root() / "lastgood" / f"{paper}.json")
    return Edition.from_dict(d) if d else None


def save_state(slot_id: str, state: dict) -> None:
    _write(_root() / "editions" / slot_id / "_state.json", state)


def load_state(slot_id: str) -> dict:
    return _read(_root() / "editions" / slot_id / "_state.json") or {}


def cache_get(kind: str, key: str) -> dict | None:
    return _read(_root() / "cache" / kind / f"{key}.json")


def cache_put(kind: str, key: str, data: dict) -> None:
    _write(_root() / "cache" / kind / f"{key}.json", data)
