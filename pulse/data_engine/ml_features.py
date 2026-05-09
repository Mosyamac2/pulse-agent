"""Feature builders for the three ML models.

Pure SQL → pandas; no ML imports. Re-used by both `ml_train` (build a full
training set) and `ml_predict` (build a single-row inference vector).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlite_utils import Database

from . import archetypes as A


# Order matters — used as the column index for both training and inference.
FEATURE_COLS: list[str] = [
    "tenure_days",
    "grade_level",
    "tasks_30d_mean",
    "tasks_60d_mean",
    "tasks_90d_mean",
    "tasks_30v60_ratio",
    "hours_30d_mean",
    "meetings_30d_mean",
    "focus_30d_mean",
    "switches_30d_mean",
    "working_hours_30d_mean",
    "stress_30d_mean",
    "sleep_30d_mean",
    "steps_30d_mean",
    "peer_sentiment_90d_mean",
    "peer_count_90d",
    "last_perf_score",
    "perf_trend_3rev",
    "days_since_vacation",
    "course_complete_rate",
    "strong_edges_count",
    "n_promotions",
    "n_assessments",
    "active_jira_count_30d",
    "is_burnout_archetype",
    "is_isolated_archetype",
    "is_overworked_archetype",
    "is_toxic_archetype",
    "n_security_flags_180d",
]
N_FEATURES = len(FEATURE_COLS)


def _avg(rows: list[dict], key: str) -> float:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return float(np.mean(vals)) if vals else 0.0


def _date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def emp_features(db: Database, emp_id: str, ref_date: date) -> dict[str, float]:
    """Build a single feature row for `emp_id` as of `ref_date`.

    `ref_date` is the snapshot day; everything is computed using only data with
    `date <= ref_date` to avoid look-ahead leakage.
    """
    emp_rows = list(db.query("SELECT * FROM employees WHERE emp_id = :e", {"e": emp_id}))
    if not emp_rows:
        return {c: 0.0 for c in FEATURE_COLS}
    emp = emp_rows[0]
    arc = A.by_name(emp["archetype"])

    hire = _date(emp["hire_date"]) or ref_date
    tenure_days = max(0, (ref_date - hire).days)

    def _window(table: str, days: int, key: str) -> float:
        lo = (ref_date - timedelta(days=days)).isoformat()
        hi = ref_date.isoformat()
        rows = list(db.query(
            f"SELECT AVG({key}) AS m FROM {table} WHERE emp_id=:e AND date>=:lo AND date<=:hi",
            {"e": emp_id, "lo": lo, "hi": hi}))
        return float(rows[0]["m"]) if rows and rows[0]["m"] is not None else 0.0

    tasks_30 = _window("activity_daily", 30, "tasks_done")
    tasks_60 = _window("activity_daily", 60, "tasks_done")
    tasks_90 = _window("activity_daily", 90, "tasks_done")

    # peer feedback
    lo90 = (ref_date - timedelta(days=90)).isoformat()
    hi = ref_date.isoformat() + "T23:59:59"
    pf_rows = list(db.query(
        "SELECT AVG(sentiment_score) AS m, COUNT(*) AS n FROM peer_feedback "
        "WHERE emp_id=:e AND ts>=:lo AND ts<=:hi",
        {"e": emp_id, "lo": lo90, "hi": hi}))
    peer_sent = float(pf_rows[0]["m"]) if pf_rows and pf_rows[0]["m"] is not None else 0.0
    peer_n = int(pf_rows[0]["n"]) if pf_rows else 0

    # last performance score & trend
    perf_rows = list(db.query(
        "SELECT score FROM performance_reviews WHERE emp_id=:e ORDER BY period DESC LIMIT 3",
        {"e": emp_id}))
    last_perf = float(perf_rows[0]["score"]) if perf_rows else 0.0
    if len(perf_rows) >= 2:
        perf_trend = float(perf_rows[0]["score"]) - float(perf_rows[-1]["score"])
    else:
        perf_trend = 0.0

    # days since vacation
    last_vac_rows = list(db.query(
        "SELECT MAX(end_date) AS d FROM vacations WHERE emp_id=:e AND kind='annual' AND end_date<=:hi",
        {"e": emp_id, "hi": hi}))
    last_vac = _date(last_vac_rows[0]["d"]) if last_vac_rows and last_vac_rows[0]["d"] else None
    days_since_vac = max(0, (ref_date - last_vac).days) if last_vac else 365

    # course completion rate
    cr_rows = list(db.query(
        "SELECT SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS c, COUNT(*) AS n "
        "FROM course_enrollments WHERE emp_id=:e", {"e": emp_id}))
    if cr_rows and cr_rows[0]["n"]:
        course_rate = float(cr_rows[0]["c"]) / float(cr_rows[0]["n"])
    else:
        course_rate = 0.0

    # strong edges
    edge_rows = list(db.query(
        "SELECT COUNT(*) AS n FROM collab_edges WHERE (emp_a=:e OR emp_b=:e) AND weight>0.5",
        {"e": emp_id}))
    strong_edges = int(edge_rows[0]["n"]) if edge_rows else 0

    # promotions count
    prom_rows = list(db.query("SELECT COUNT(*) AS n FROM promotions WHERE emp_id=:e", {"e": emp_id}))
    n_prom = int(prom_rows[0]["n"]) if prom_rows else 0

    # assessments count
    ass_rows = list(db.query("SELECT COUNT(*) AS n FROM assessments WHERE emp_id=:e", {"e": emp_id}))
    n_ass = int(ass_rows[0]["n"]) if ass_rows else 0

    # active jira in last 30d
    lo30 = (ref_date - timedelta(days=30)).isoformat()
    jira_rows = list(db.query(
        "SELECT COUNT(*) AS n FROM jira_issues WHERE emp_id=:e AND ts_created>=:lo AND ts_created<=:hi",
        {"e": emp_id, "lo": lo30, "hi": hi}))
    n_jira = int(jira_rows[0]["n"]) if jira_rows else 0

    # security flags last 180d
    lo180 = (ref_date - timedelta(days=180)).isoformat()
    sec_rows = list(db.query(
        "SELECT COUNT(*) AS n FROM security_flags WHERE emp_id=:e AND ts>=:lo AND ts<=:hi",
        {"e": emp_id, "lo": lo180, "hi": hi}))
    n_sec = int(sec_rows[0]["n"]) if sec_rows else 0

    feats = {
        "tenure_days": float(tenure_days),
        "grade_level": float(emp["grade_level"]),
        "tasks_30d_mean": tasks_30,
        "tasks_60d_mean": tasks_60,
        "tasks_90d_mean": tasks_90,
        "tasks_30v60_ratio": tasks_30 / tasks_60 if tasks_60 > 0 else 1.0,
        "hours_30d_mean": _window("activity_daily", 30, "hours_logged"),
        "meetings_30d_mean": _window("activity_daily", 30, "meetings_count"),
        "focus_30d_mean": _window("digital_patterns_daily", 30, "focus_score"),
        "switches_30d_mean": _window("digital_patterns_daily", 30, "switches_per_min"),
        "working_hours_30d_mean": _window("digital_patterns_daily", 30, "working_hours"),
        "stress_30d_mean": _window("wearables_daily", 30, "stress_index"),
        "sleep_30d_mean": _window("wearables_daily", 30, "sleep_h"),
        "steps_30d_mean": _window("wearables_daily", 30, "steps"),
        "peer_sentiment_90d_mean": peer_sent,
        "peer_count_90d": float(peer_n),
        "last_perf_score": last_perf,
        "perf_trend_3rev": perf_trend,
        "days_since_vacation": float(days_since_vac),
        "course_complete_rate": course_rate,
        "strong_edges_count": float(strong_edges),
        "n_promotions": float(n_prom),
        "n_assessments": float(n_ass),
        "active_jira_count_30d": float(n_jira),
        "is_burnout_archetype": float(arc.burnout_prone),
        "is_isolated_archetype": float(arc.isolated),
        "is_overworked_archetype": float(arc.overworked),
        "is_toxic_archetype": float(arc.toxic),
        "n_security_flags_180d": float(n_sec),
    }
    return feats


def feature_vector(feats: dict[str, float]) -> np.ndarray:
    return np.array([feats[c] for c in FEATURE_COLS], dtype=np.float64)


def factor_explanation(model, feats: dict[str, float], top_k: int = 3) -> list[tuple[str, float]]:
    """Cheap SHAP-substitute: report `feature_importances_ * value`."""
    fi = getattr(model, "feature_importances_", None)
    if fi is None:
        return []
    contrib = [(FEATURE_COLS[i], float(fi[i] * feats[FEATURE_COLS[i]])) for i in range(len(FEATURE_COLS))]
    contrib.sort(key=lambda x: abs(x[1]), reverse=True)
    return contrib[:top_k]


__all__ = ["FEATURE_COLS", "N_FEATURES", "emp_features", "feature_vector", "factor_explanation"]
