"""Tiny JSON-on-disk state store at `data/state/state.json`.

Used by tick, evolution, consciousness, and reflection. Atomic write:
write to a temp file in the same dir, fsync, rename.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .config import PATHS

_DEFAULT_STATE: dict[str, Any] = {
    "evolution": {
        "last_offset": 0,                # bytes already consumed in feedback.jsonl
        "last_version": "",
        "last_run_ts": "",
        "downvotes_since_last": 0,
        "history": [],                   # list of {intent, class_addressed, version, ts}
        "cooldown": {},                  # class_id -> until_iso_ts
    },
    "ml": {"needs_refresh": False, "last_train_ts": ""},
    "tick": {"last_tick_date": "", "last_tick_ts": ""},
    "process": {"restart_pending": False},
    "next_emp_idx": 100,                  # next emp_id sequence pointer
}


def _state_path() -> Path:
    PATHS.ensure()
    return PATHS.state / "state.json"


def load_state() -> dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return json.loads(json.dumps(_DEFAULT_STATE))
    raw = p.read_text(encoding="utf-8")
    if not raw.strip():
        return json.loads(json.dumps(_DEFAULT_STATE))
    out = json.loads(raw)
    # back-fill any missing top-level keys.
    for k, v in _DEFAULT_STATE.items():
        if k not in out:
            out[k] = json.loads(json.dumps(v))
    return out


def save_state(state: dict[str, Any]) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".state.", suffix=".json.tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


__all__ = ["load_state", "save_state"]
