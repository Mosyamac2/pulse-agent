"""Smoke tests for `pulse.dashboard` — aggregations powering /dashboard.html.

We seed a real DB once per module (via the same `seed.seed` fixture used in
test_marts.py) and exercise each function. Logs that don't exist on a fresh
machine (feedback.jsonl, budget.jsonl, rejected_suggestions.md) are written
ad-hoc inside the test for the path-injectable functions.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pulse.data_engine.seed import seed


@pytest.fixture(scope="module")
def seeded_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db_path = tmp_path_factory.mktemp("dash") / "sber_hr.db"
    seed(db_path, force=True)
    return db_path


@pytest.fixture
def db(seeded_db: Path):
    from sqlite_utils import Database
    return Database(seeded_db)


# --- KPI strip --------------------------------------------------------------

def test_kpi_strip_shape(db):
    from pulse.dashboard import get_kpi_strip
    out = get_kpi_strip(window=30, db=db)
    assert out["window_days"] == 30
    for k in ("at_risk", "burnout", "hot_dept", "trust"):
        assert k in out
    assert isinstance(out["at_risk"]["value"], int)
    assert out["at_risk"]["total"] >= 1
    # delta may be int or None depending on prior-window data presence
    assert out["at_risk"]["delta"] is None or isinstance(out["at_risk"]["delta"], int)
    # burnout same
    assert isinstance(out["burnout"]["value"], int)


def test_kpi_strip_hot_dept_is_real_unit(db):
    from pulse.dashboard import get_kpi_strip
    out = get_kpi_strip(window=30, db=db)
    hot = out["hot_dept"]
    if hot["unit_id"] is not None:
        assert hot["score"] is not None
        assert hot["sentiment"] is not None
        assert hot["stress"] is not None
        assert hot["n_employees"] >= 1


def test_kpi_strip_trust_uses_injected_log(tmp_path, db):
    """Write a fake feedback.jsonl and inject it via the path parameter."""
    fb = tmp_path / "feedback.jsonl"
    now = datetime.now(timezone.utc)
    rows = []
    # 8 likes / 2 dislikes in the recent window → 80%
    for i in range(8):
        rows.append({"ts": (now - timedelta(days=2)).isoformat(timespec="seconds"),
                       "verdict": "up", "message_id": f"m{i}"})
    for i in range(2):
        rows.append({"ts": (now - timedelta(days=2)).isoformat(timespec="seconds"),
                       "verdict": "down", "message_id": f"d{i}"})
    fb.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    from pulse.dashboard import get_kpi_strip
    out = get_kpi_strip(window=30, db=db, feedback_path=fb)
    assert out["trust"]["pct"] == 80.0
    assert out["trust"]["likes"] == 8
    assert out["trust"]["dislikes"] == 2


# --- Heatmap ----------------------------------------------------------------

def test_heatmap_shape(db):
    from pulse.dashboard import get_workforce_heatmap, HEATMAP_METRICS
    out = get_workforce_heatmap(window=30, db=db)
    assert out["window_days"] == 30
    assert len(out["units"]) >= 1
    assert len(out["metrics"]) == len(HEATMAP_METRICS)
    # cells = units × metrics
    assert len(out["cells"]) == len(out["units"]) * len(HEATMAP_METRICS)
    for c in out["cells"]:
        assert -3.0 <= c["severity"] <= 3.0
        assert c["metric"] in {m["key"] for m in HEATMAP_METRICS}


# --- At-risk Top-N ----------------------------------------------------------

def test_at_risk_top_returns_flagged(db):
    from pulse.dashboard import get_at_risk_top, AT_RISK_MIN_FLAGS
    rows = get_at_risk_top(n=7, window=30, db=db)
    assert isinstance(rows, list)
    assert len(rows) <= 7
    for r in rows:
        # Each row is at minimum 1-flagged; sorted so flag_count is non-increasing
        assert r["flag_count"] >= 1
        assert r["emp_id"]
        assert isinstance(r["flags"], list)
    if len(rows) >= 2:
        assert rows[0]["flag_count"] >= rows[-1]["flag_count"]


# --- Archetype scatter ------------------------------------------------------

def test_archetype_scatter_points_are_in_range(db):
    from pulse.dashboard import get_archetype_scatter
    out = get_archetype_scatter(window=30, db=db)
    assert len(out["points"]) >= 1
    assert len(out["archetypes"]) >= 1
    for p in out["points"]:
        # x = stress (0..1), y = focus (0..1)
        assert 0.0 <= p["x"] <= 1.0
        assert 0.0 <= p["y"] <= 1.0
        assert p["archetype"]


# --- Trust timeline ---------------------------------------------------------

def test_trust_timeline_shape(tmp_path):
    from pulse.dashboard import get_trust_timeline
    fb = tmp_path / "feedback.jsonl"
    now = datetime.now(timezone.utc)
    rows = [
        {"ts": (now - timedelta(days=1)).isoformat(timespec="seconds"),
         "verdict": "up", "message_id": "m1"},
        {"ts": (now - timedelta(days=1)).isoformat(timespec="seconds"),
         "verdict": "down", "message_id": "m2"},
        {"ts": (now - timedelta(days=2)).isoformat(timespec="seconds"),
         "verdict": "up", "message_id": "m3"},
    ]
    fb.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = get_trust_timeline(window=30, feedback_path=fb)
    assert out["window_days"] == 30
    assert len(out["days"]) >= 1
    total_likes = sum(d["likes"] for d in out["days"])
    total_dislikes = sum(d["dislikes"] for d in out["days"])
    assert total_likes == 2 and total_dislikes == 1


# --- Evolution log ----------------------------------------------------------

def test_evolution_log_returns_real_commits():
    """Run against the live repo — should always have at least 1 commit."""
    from pulse.dashboard import get_evolution_log
    items = get_evolution_log(n=3)
    assert len(items) >= 1
    for it in items:
        assert it["hash"] and len(it["hash"]) == 7
        assert it["subject"]
        assert isinstance(it["self_evolved"], bool)


# --- Rejected suggestions ---------------------------------------------------

def test_rejected_suggestions_parses_known_format(tmp_path):
    from pulse.dashboard import get_rejected_suggestions
    p = tmp_path / "rejected_suggestions.md"
    p.write_text(
        "# rejected_suggestions.md — отклонённые\n\n"
        "## gen_aaa1 — 2026-05-01T10:00:00\n\n"
        "**Предложение пользователя:**\n\n"
        "> добавить функцию X\n\n"
        "**Вердикт:** `reject`\n\n"
        "**Обоснование:** конфликт с принципом P5\n\n"
        "**Конфликт с принципом:** P5\n\n"
        "## gen_bbb2 — 2026-05-02T11:00:00\n\n"
        "**Предложение пользователя:**\n\n"
        "> сделать Y\n\n"
        "**Вердикт:** `modify`\n\n"
        "**Подсказка для переформулирования:** уточнить scope\n",
        encoding="utf-8",
    )
    items = get_rejected_suggestions(n=5, path=p)
    assert len(items) == 2
    # Most recent first (file is append-only, we reverse)
    assert items[0]["id"] == "gen_bbb2"
    assert items[0]["verdict"] == "modify"
    assert items[0]["hint"] == "уточнить scope"
    assert items[1]["id"] == "gen_aaa1"
    assert items[1]["principle"] == "P5"
    assert "X" in (items[1]["suggestion"] or "")


def test_rejected_suggestions_missing_file_returns_empty(tmp_path):
    from pulse.dashboard import get_rejected_suggestions
    out = get_rejected_suggestions(n=5, path=tmp_path / "does-not-exist.md")
    assert out == []


# --- Cost -------------------------------------------------------------------

def test_cost_breakdown_aggregates(tmp_path):
    from pulse.dashboard import get_cost_breakdown
    p = tmp_path / "budget.jsonl"
    now = datetime.now(timezone.utc)
    lines = [
        {"ts": (now - timedelta(days=1)).isoformat(),
         "model": "claude-sonnet-4-6", "usd": 0.10},
        {"ts": (now - timedelta(days=1)).isoformat(),
         "model": "claude-opus-4-7",   "usd": 0.40},
        {"ts": (now - timedelta(days=2)).isoformat(),
         "model": "claude-sonnet-4-6", "usd": 0.05},
    ]
    p.write_text("\n".join(json.dumps(r) for r in lines), encoding="utf-8")
    out = get_cost_breakdown(window=30, path=p)
    assert out["window_days"] == 30
    assert abs(out["total_window_usd"] - 0.55) < 1e-6
    assert abs(out["by_model_usd"]["opus"] - 0.40) < 1e-6
    assert abs(out["by_model_usd"]["sonnet"] - 0.15) < 1e-6
    assert len(out["days"]) == 2
    assert out["run_rate_usd_30d"] > 0


def test_cost_breakdown_missing_file_is_zero(tmp_path):
    from pulse.dashboard import get_cost_breakdown
    out = get_cost_breakdown(window=30, path=tmp_path / "missing.jsonl")
    assert out["total_window_usd"] == 0.0
    assert out["days"] == []
