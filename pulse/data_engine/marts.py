"""Pre-aggregated views for typical HR questions.

Replaces the N+1 anti-pattern where the agent would call
`get_employee_metrics` once per employee in a 90-row team. Every function
here is a single SQL aggregation that returns ranked rows or distributions
ready for the MCP tool layer.

Date semantics: `last_days` is measured against MAX(date) of the relevant
table — the synthetic data clock advances on its own, not on wall-clock,
so we pin the window to the data's own horizon.
"""
from __future__ import annotations

import math
from typing import Any

from sqlite_utils import Database

from ..config import PATHS


# ---------------------------------------------------------------------------
# Metric registry — single source of truth for what we know how to aggregate
# ---------------------------------------------------------------------------

METRIC_REGISTRY: dict[str, dict[str, Any]] = {
    # activity_daily
    "tasks_done":         {"table": "activity_daily",         "col": "tasks_done",       "direction": "higher_is_better", "label": "выполненные задачи в день", "scale": "events/day"},
    "hours_logged":       {"table": "activity_daily",         "col": "hours_logged",     "direction": "near_norm",        "label": "часы работы в день",        "scale": "h/day"},
    "meetings_count":     {"table": "activity_daily",         "col": "meetings_count",   "direction": "lower_is_better",  "label": "встречи в день",            "scale": "events/day"},
    # digital_patterns_daily
    "focus_score":        {"table": "digital_patterns_daily", "col": "focus_score",      "direction": "higher_is_better", "label": "доля концентрированной работы", "scale": "0–1"},
    "switches_per_min":   {"table": "digital_patterns_daily", "col": "switches_per_min", "direction": "lower_is_better",  "label": "переключения контекста",    "scale": "/min"},
    "working_hours":      {"table": "digital_patterns_daily", "col": "working_hours",    "direction": "near_norm",        "label": "фактические часы активности", "scale": "h/day"},
    # wearables_daily
    "steps":              {"table": "wearables_daily",        "col": "steps",            "direction": "higher_is_better", "label": "шаги в день",               "scale": "steps/day"},
    "sleep_h":            {"table": "wearables_daily",        "col": "sleep_h",          "direction": "near_norm",        "label": "часы сна",                  "scale": "h/day"},
    "stress_index":       {"table": "wearables_daily",        "col": "stress_index",     "direction": "lower_is_better",  "label": "стресс-индекс",             "scale": "0–1"},
    # peer_feedback
    "peer_sentiment":     {"table": "peer_feedback",          "col": "sentiment_score",  "direction": "higher_is_better", "label": "средний sentiment коллег",  "scale": "−1…+1"},
}


def list_metric_names() -> list[str]:
    return sorted(METRIC_REGISTRY.keys())


def metric_meta(metric: str) -> dict[str, Any]:
    spec = METRIC_REGISTRY.get(metric)
    if spec is None:
        raise ValueError(
            f"Unknown metric '{metric}'. Available: {', '.join(list_metric_names())}"
        )
    return spec


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _db(db: Database | None) -> Database:
    return db if db is not None else Database(PATHS.db)


def _date_floor_clause(alias: str, table: str, last_days: int) -> str:
    """SQL fragment that selects rows from the last N days relative to that
    table's own latest date. `alias` is the alias used in the outer query
    (typically `t`); `table` is the real table name used in the subquery."""
    return (
        f"date({alias}.date) >= "
        f"date((SELECT MAX(date) FROM {table}), '-{int(last_days)} day')"
    )


# ---------------------------------------------------------------------------
# Marts
# ---------------------------------------------------------------------------

def top_employees_by_metric(metric: str, *, last_days: int = 30, n: int = 10,
                              ascending: bool = False,
                              db: Database | None = None) -> list[dict[str, Any]]:
    """Top-N (or bottom-N) employees by mean of `metric` over `last_days`.

    Single SQL query — replaces 90-emp N+1 loops. Returns list of:
      {emp_id, full_name, position_title, unit_name, value, n_days}
    """
    spec = metric_meta(metric)
    db = _db(db)
    direction = "ASC" if ascending else "DESC"
    table = spec["table"]
    col = spec["col"]
    where = _date_floor_clause("t", table, last_days) if table != "peer_feedback" else (
        # peer_feedback uses ts not date
        f"date(t.ts) >= date((SELECT MAX(ts) FROM peer_feedback), '-{int(last_days)} day')"
    )
    sql = f"""
      SELECT e.emp_id, e.full_name,
             p.title AS position_title,
             u.name AS unit_name,
             AVG(t.{col}) AS value,
             COUNT(*) AS n_days
      FROM employees e
      JOIN {table} t ON t.emp_id = e.emp_id
      LEFT JOIN positions p ON p.position_id = e.position_id
      LEFT JOIN units u ON u.unit_id = e.unit_id
      WHERE e.status = 'active' AND {where}
      GROUP BY e.emp_id
      HAVING COUNT(*) >= 5
      ORDER BY value {direction}
      LIMIT {int(n)}
    """
    rows = list(db.query(sql))
    for r in rows:
        if r["value"] is not None:
            r["value"] = round(float(r["value"]), 3)
    return rows


def metric_distribution(metric: str, *, last_days: int = 30,
                          db: Database | None = None) -> dict[str, Any]:
    """Distribution stats for `metric` across active employees.

    Returns: {metric, n_employees, mean, p25, p50, p75, min, max, label, scale, direction}.
    Pulled in two queries (per-employee mean, then aggregate) to avoid
    cross-employee bias from heavy users.
    """
    spec = metric_meta(metric)
    db = _db(db)
    table = spec["table"]
    col = spec["col"]
    where = _date_floor_clause("t", table, last_days) if table != "peer_feedback" else (
        f"date(t.ts) >= date((SELECT MAX(ts) FROM peer_feedback), '-{int(last_days)} day')"
    )
    sql = f"""
      SELECT e.emp_id, AVG(t.{col}) AS v
      FROM employees e
      JOIN {table} t ON t.emp_id = e.emp_id
      WHERE e.status='active' AND {where}
      GROUP BY e.emp_id
      HAVING COUNT(*) >= 5
    """
    values = sorted(float(r["v"]) for r in db.query(sql) if r["v"] is not None)
    if not values:
        return {"metric": metric, "n_employees": 0, **spec}

    def pct(p: float) -> float:
        if not values:
            return float("nan")
        k = (len(values) - 1) * p
        lo = math.floor(k)
        hi = math.ceil(k)
        if lo == hi:
            return values[int(k)]
        return values[lo] * (hi - k) + values[hi] * (k - lo)

    out = {
        "metric": metric,
        "label": spec["label"],
        "scale": spec["scale"],
        "direction": spec["direction"],
        "n_employees": len(values),
        "mean": round(sum(values) / len(values), 3),
        "p25": round(pct(0.25), 3),
        "p50": round(pct(0.50), 3),
        "p75": round(pct(0.75), 3),
        "min": round(values[0], 3),
        "max": round(values[-1], 3),
    }
    return out


_GROUP_COLS = {
    "unit": ("u.name AS group_label, e.unit_id AS group_id",
             "LEFT JOIN units u ON u.unit_id = e.unit_id",
             "e.unit_id"),
    "position": ("p.title AS group_label, e.position_id AS group_id",
                 "LEFT JOIN positions p ON p.position_id = e.position_id",
                 "e.position_id"),
    "archetype": ("e.archetype AS group_label, e.archetype AS group_id",
                  "",
                  "e.archetype"),
    "grade": ("CAST(e.grade_level AS TEXT) AS group_label, "
              "CAST(e.grade_level AS TEXT) AS group_id",
              "",
              "e.grade_level"),
}


def aggregate_metric_by(metric: str, *, group_by: str = "unit",
                          last_days: int = 30,
                          db: Database | None = None) -> list[dict[str, Any]]:
    """Mean of `metric` per group (unit / position / archetype / grade).

    Single query. Returns list of {group_id, group_label, value, n_employees}.
    """
    spec = metric_meta(metric)
    if group_by not in _GROUP_COLS:
        raise ValueError(
            f"Unknown group_by '{group_by}'. Use one of {list(_GROUP_COLS)}"
        )
    db = _db(db)
    select_cols, join, group_col = _GROUP_COLS[group_by]
    table = spec["table"]
    col = spec["col"]
    where = _date_floor_clause("t", table, last_days) if table != "peer_feedback" else (
        f"date(t.ts) >= date((SELECT MAX(ts) FROM peer_feedback), '-{int(last_days)} day')"
    )
    sql = f"""
      SELECT {select_cols},
             AVG(t.{col}) AS value,
             COUNT(DISTINCT e.emp_id) AS n_employees
      FROM employees e
      JOIN {table} t ON t.emp_id = e.emp_id
      {join}
      WHERE e.status='active' AND {where}
      GROUP BY {group_col}
      HAVING COUNT(DISTINCT e.emp_id) >= 1
      ORDER BY value DESC
    """
    rows = list(db.query(sql))
    for r in rows:
        if r["value"] is not None:
            r["value"] = round(float(r["value"]), 3)
    return rows


def top_collab_connectors(*, by: str = "weight_sum", n: int = 10,
                            db: Database | None = None) -> list[dict[str, Any]]:
    """Most-connected employees by collab_edges.

    by='degree'    — number of distinct partners (network breadth)
    by='weight_sum'— sum of edge weights (interaction intensity, default)

    Returns: {emp_id, full_name, position_title, unit_name, degree, weight_sum}.
    """
    if by not in ("degree", "weight_sum"):
        raise ValueError("by must be 'degree' or 'weight_sum'")
    db = _db(db)
    sql = f"""
      WITH edges AS (
        SELECT emp_a AS emp_id, emp_b AS partner, weight FROM collab_edges
        UNION ALL
        SELECT emp_b AS emp_id, emp_a AS partner, weight FROM collab_edges
      ),
      agg AS (
        SELECT emp_id,
               COUNT(DISTINCT partner) AS degree,
               SUM(weight) AS weight_sum
        FROM edges
        GROUP BY emp_id
      )
      SELECT e.emp_id, e.full_name,
             p.title AS position_title,
             u.name AS unit_name,
             agg.degree, ROUND(agg.weight_sum, 3) AS weight_sum
      FROM agg
      JOIN employees e ON e.emp_id = agg.emp_id
      LEFT JOIN positions p ON p.position_id = e.position_id
      LEFT JOIN units u ON u.unit_id = e.unit_id
      WHERE e.status='active'
      ORDER BY {by} DESC
      LIMIT {int(n)}
    """
    return list(db.query(sql))


def efficiency_ranking(*, last_days: int = 30, n: int = 10,
                         ascending: bool = False,
                         db: Database | None = None) -> list[dict[str, Any]]:
    """Composite efficiency ranking: tasks per logged hour, weighted by focus
    quality. Explainable: `(tasks_done / max(hours_logged, 4)) * (0.5 + focus_score)`.

    The constant 4 floors hours_logged so a single-task half-day doesn't
    dominate; the focus multiplier (0.5..1.5) gives a sanity penalty for
    chaotic days regardless of raw output.

    Returns ranked list of:
      {emp_id, full_name, position_title, unit_name, score,
       tasks_per_day, hours_per_day, focus_avg, n_days}
    """
    db = _db(db)
    direction = "ASC" if ascending else "DESC"
    sql = f"""
      WITH activity AS (
        SELECT e.emp_id,
               AVG(a.tasks_done) AS tasks_per_day,
               AVG(a.hours_logged) AS hours_per_day,
               COUNT(*) AS n_days
        FROM employees e
        JOIN activity_daily a ON a.emp_id = e.emp_id
        WHERE e.status='active'
          AND date(a.date) >= date((SELECT MAX(date) FROM activity_daily), '-{int(last_days)} day')
          AND a.is_weekend = 0
        GROUP BY e.emp_id
        HAVING COUNT(*) >= 5
      ),
      digital AS (
        SELECT e.emp_id, AVG(d.focus_score) AS focus_avg
        FROM employees e
        JOIN digital_patterns_daily d ON d.emp_id = e.emp_id
        WHERE e.status='active'
          AND date(d.date) >= date((SELECT MAX(date) FROM digital_patterns_daily), '-{int(last_days)} day')
        GROUP BY e.emp_id
      )
      SELECT e.emp_id, e.full_name,
             p.title AS position_title,
             u.name AS unit_name,
             ROUND(activity.tasks_per_day, 2) AS tasks_per_day,
             ROUND(activity.hours_per_day, 2) AS hours_per_day,
             ROUND(COALESCE(digital.focus_avg, 0.5), 3) AS focus_avg,
             activity.n_days AS n_days,
             ROUND(
               (activity.tasks_per_day / MAX(activity.hours_per_day, 4.0)) *
               (0.5 + COALESCE(digital.focus_avg, 0.5)),
               3
             ) AS score
      FROM activity
      LEFT JOIN digital ON digital.emp_id = activity.emp_id
      JOIN employees e ON e.emp_id = activity.emp_id
      LEFT JOIN positions p ON p.position_id = e.position_id
      LEFT JOIN units u ON u.unit_id = e.unit_id
      ORDER BY score {direction}
      LIMIT {int(n)}
    """
    return list(db.query(sql))


__all__ = [
    "METRIC_REGISTRY",
    "list_metric_names",
    "metric_meta",
    "top_employees_by_metric",
    "metric_distribution",
    "aggregate_metric_by",
    "top_collab_connectors",
    "efficiency_ranking",
]
