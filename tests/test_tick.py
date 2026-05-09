"""Daily-tick contract tests."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlite_utils import Database

from pulse.data_engine.seed import seed, END_DATE
from pulse.data_engine.tick import tick


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "sber_hr.db"
    seed(db_path, force=True)
    # rebind PATHS so state.json lands in tmp_path
    from pulse.config import PATHS
    object.__setattr__(PATHS, "db", db_path)
    object.__setattr__(PATHS, "data", tmp_path)
    object.__setattr__(PATHS, "state", tmp_path / "state")
    object.__setattr__(PATHS, "logs", tmp_path / "logs")
    return db_path


def test_tick_appends_one_day(fresh_db: Path):
    db = Database(fresh_db)
    # Tick targets max(activity, wearables) + 1.
    max_act = date.fromisoformat(list(db.query("SELECT MAX(date) AS d FROM activity_daily"))[0]["d"])
    max_wear = date.fromisoformat(list(db.query("SELECT MAX(date) AS d FROM wearables_daily"))[0]["d"])
    target = max(max_act, max_wear) + timedelta(days=1)
    summary = tick(fresh_db)
    assert summary["date"] == target.isoformat()
    if target.weekday() < 5:
        assert summary["rows_activity"] > 0
    assert summary["rows_wearables"] > 0
    last_w = date.fromisoformat(list(db.query("SELECT MAX(date) AS d FROM wearables_daily"))[0]["d"])
    assert last_w == target


def test_tick_idempotent(fresh_db: Path):
    summary1 = tick(fresh_db)
    summary2 = tick(fresh_db, target_date=date.fromisoformat(summary1["date"]))
    assert summary2.get("skipped") == "already exists"


def test_tick_writes_state(fresh_db: Path):
    summary = tick(fresh_db)
    from pulse.state import load_state
    state = load_state()
    assert state["tick"]["last_tick_date"] == summary["date"]
    assert state["ml"]["needs_refresh"] is True


def test_tick_logs_event(fresh_db: Path):
    summary = tick(fresh_db)
    from pulse.config import PATHS
    log_path = PATHS.logs / "events.jsonl"
    assert log_path.exists()
    last_line = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
    import json
    rec = json.loads(last_line)
    assert rec["kind"] == "daily_tick"
    assert rec["date"] == summary["date"]


def test_multiple_ticks(fresh_db: Path):
    """Run 5 ticks in a row. None should be skipped."""
    n_active_before = list(Database(fresh_db).query("SELECT COUNT(*) AS n FROM employees WHERE status='active'"))[0]["n"]
    for _ in range(5):
        s = tick(fresh_db)
        assert "skipped" not in s
    db = Database(fresh_db)
    n_active_after = list(db.query("SELECT COUNT(*) AS n FROM employees WHERE status='active'"))[0]["n"]
    # active count can move slightly via stochastic hire/term — bound
    assert abs(n_active_after - n_active_before) <= 5
