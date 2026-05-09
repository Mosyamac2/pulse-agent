"""Background consciousness — a single thread that runs one maintenance step
every `wakeup_interval_s`.

Steps come from prompts/CONSCIOUSNESS.md (numbered list). We rotate through
them; that's the entire scheduling logic. State persistence:

  state.json::consciousness = {
    "last_wake_ts": <iso>,
    "rotation_idx": <int>,
    "wakeups_total": <int>,
    "last_step": <name>,
  }

The thread is daemon so it dies with the process. `start()` is idempotent —
calling it twice is a no-op.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .config import PATHS, SETTINGS
from .improvement_backlog import list_entries, update_status
from .memory import (
    append_dated_section,
    bootstrap_starter_files,
    identity_path,
    read_text,
    scratchpad_path,
)
from .state import load_state, save_state

log = logging.getLogger(__name__)

DEFAULT_WAKEUP_INTERVAL_S = 600


# ---------------------------------------------------------------------------
# Maintenance steps
# ---------------------------------------------------------------------------

def _step_identity_freshness() -> str:
    """If identity.md hasn't been touched in >24h of activity, append a stub note."""
    p = identity_path()
    if not p.exists():
        bootstrap_starter_files()
    last_mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
    age_h = (datetime.now(timezone.utc) - last_mtime).total_seconds() / 3600
    if age_h < 24:
        return "skipped (identity fresh)"
    append_dated_section(p, "Просыпаюсь и осознаю: на сутки активного диалога я не "
                            "обновлял этот файл. Я тот же — но мой опыт за этот день "
                            "стоит зафиксировать в следующих коммитах.")
    return "identity stub appended"


def _step_scratchpad_trim() -> str:
    """Trim scratchpad.md if it's grown past 100KB — keep the tail."""
    p = scratchpad_path()
    if not p.exists():
        return "skipped (no scratchpad)"
    size = p.stat().st_size
    if size < 100_000:
        return f"skipped (size={size}B < 100KB)"
    txt = p.read_text(encoding="utf-8")
    keep = txt[-50_000:]
    p.write_text("# scratchpad.md (trimmed)\n\n" + keep, encoding="utf-8")
    return f"trimmed: {size}B → {len(keep)}B"


def _step_backlog_triage() -> str:
    """Mark entries older than 90 days as 'abandoned' if still 'open' — gentle decay."""
    items = list_entries()
    if not items:
        return "skipped (backlog empty)"
    now = datetime.now(timezone.utc)
    n = 0
    for e in items:
        if e.status != "open":
            continue
        try:
            created = datetime.fromisoformat(e.created)
        except ValueError:
            continue
        if (now - created).days >= 90:
            update_status(e.id, "abandoned")
            n += 1
    return f"abandoned {n} stale entries"


def _step_daily_tick() -> str:
    """Run one tick if the last one was > 24h ago. No-op without DB."""
    if not PATHS.db.exists():
        return "skipped (no DB)"
    state = load_state()
    last = state.get("tick", {}).get("last_tick_ts", "")
    try:
        ts = datetime.fromisoformat(last) if last else None
    except ValueError:
        ts = None
    if ts is not None and (datetime.now(timezone.utc) - ts) < timedelta(hours=SETTINGS.daily_tick_interval_h):
        return f"skipped (last tick {last})"
    from .data_engine.tick import tick
    summary = tick(PATHS.db)
    return f"tick {summary['date']}: {summary.get('rows_activity', 0)} activity rows"


def _step_feedback_scan() -> str:
    """Cheap: just count last 10 feedback entries."""
    p = PATHS.logs / "feedback.jsonl"
    if not p.exists():
        return "skipped (no feedback)"
    lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()][-10:]
    n_down = sum(1 for ln in lines if '"verdict":"down"' in ln or '"verdict": "down"' in ln)
    return f"scanned: {len(lines)} recent, {n_down} 👎"


STEPS: list[tuple[str, Callable[[], str]]] = [
    ("identity_freshness", _step_identity_freshness),
    ("scratchpad_trim", _step_scratchpad_trim),
    ("backlog_triage", _step_backlog_triage),
    ("daily_tick", _step_daily_tick),
    ("feedback_scan", _step_feedback_scan),
]


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run_one_maintenance_step() -> dict[str, object]:
    """Pick one step (rotated), run it, persist state. Returns a result dict."""
    state = load_state()
    cs = state.setdefault("consciousness", {})
    idx = int(cs.get("rotation_idx", 0)) % len(STEPS)
    name, fn = STEPS[idx]
    try:
        outcome = fn()
        ok = True
    except Exception as ex:
        outcome = f"error: {ex}"
        ok = False
        log.exception("consciousness step %s failed", name)

    cs["last_wake_ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cs["rotation_idx"] = (idx + 1) % len(STEPS)
    cs["wakeups_total"] = int(cs.get("wakeups_total", 0)) + 1
    cs["last_step"] = name
    cs["last_outcome"] = outcome
    save_state(state)
    return {"step": name, "ok": ok, "outcome": outcome,
            "wakeups_total": cs["wakeups_total"]}


# ---------------------------------------------------------------------------
# Thread
# ---------------------------------------------------------------------------

_THREAD: threading.Thread | None = None
_STOP = threading.Event()


def _loop(interval_s: int) -> None:
    log.info("consciousness loop started (interval=%ds)", interval_s)
    while not _STOP.is_set():
        try:
            res = run_one_maintenance_step()
            log.info("consciousness wake: %s", res)
        except Exception:
            log.exception("consciousness wake failed")
        if _STOP.wait(interval_s):
            break
    log.info("consciousness loop stopped")


def start(interval_s: int = DEFAULT_WAKEUP_INTERVAL_S) -> None:
    global _THREAD
    if _THREAD is not None and _THREAD.is_alive():
        return
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, args=(interval_s,), name="pulse-consciousness", daemon=True)
    _THREAD.start()


def stop() -> None:
    _STOP.set()


def is_alive() -> bool:
    return _THREAD is not None and _THREAD.is_alive()


__all__ = ["STEPS", "run_one_maintenance_step", "start", "stop", "is_alive"]
