"""Aggregations powering /web/dashboard.html (CEO dashboard).

Read-only functions over data/sber_hr.db, data/logs/*.jsonl,
data/memory/knowledge/rejected_suggestions.md, and `git log`. No new
storage. Default window is 30 days — picked for the CEO rhythm
(monthly POK / budget cadence).

KPI strip (above-the-fold) is the contract: four numbers, each with a
delta vs the prior 30-day window. Definitions live at the top of this
file so README, UI tooltip, and SQL stay in sync:

  AT-RISK   — at-risk-of-leaving employees:
              ≥3 of {peer_sentiment<0.0, focus_score<0.4,
                     tasks_done<4.0, hours_logged<7.5}
              "disengaged + drifting"

  BURNOUT   — burnout-flag employees:
              ≥3 of {hours_logged>9.5, stress_index>0.65,
                     sleep_h<6.5, peer_sentiment<0.1}
              "overworked + stressed + tired"

  HOT DEPT  — unit with the lowest composite (peer_sentiment − stress_index).
              Lower = hotter.

  TRUST     — Pulse like-rate over the window: likes / (likes+dislikes).
              Computed from data/logs/feedback.jsonl.

For deltas the prior window is the immediately preceding `window` days.
If either side has insufficient data the delta is reported as None.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlite_utils import Database

from .config import PATHS

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Thresholds — single source of truth
# ---------------------------------------------------------------------------

AT_RISK_FLAGS: dict[str, tuple[str, float, str]] = {
    "low_sentiment": ("peer_sentiment", 0.0,  "lt"),
    "low_focus":     ("focus_score",    0.4,  "lt"),
    "low_tasks":     ("tasks_done",     4.0,  "lt"),
    "low_hours":     ("hours_logged",   7.5,  "lt"),
}
AT_RISK_MIN_FLAGS = 3

BURNOUT_FLAGS: dict[str, tuple[str, float, str]] = {
    "long_hours":    ("hours_logged",   9.5,  "gt"),
    "high_stress":   ("stress_index",   0.65, "gt"),
    "low_sleep":     ("sleep_h",        6.5,  "lt"),
    "low_sentiment": ("peer_sentiment", 0.1,  "lt"),
}
BURNOUT_MIN_FLAGS = 3


# ---------------------------------------------------------------------------
# DB helper — single shared connection per call site
# ---------------------------------------------------------------------------

def _db(db: Database | None) -> Database:
    return db if db is not None else Database(PATHS.db)


def _max_date(db: Database, table: str, col: str = "date") -> str | None:
    rows = list(db.query(f"SELECT MAX({col}) AS m FROM {table}"))
    return rows[0]["m"] if rows and rows[0]["m"] else None


# ---------------------------------------------------------------------------
# Per-employee composite metrics over a window
# ---------------------------------------------------------------------------

def _employee_composite(db: Database, *, days_back: int = 30,
                          offset_days: int = 0) -> list[dict[str, Any]]:
    """Per-active-employee window means for every metric used by KPIs.

    `offset_days` shifts the window backwards by N days — used to compute
    "previous window" for delta calculations. With offset_days=0 the
    window is the most-recent N days; with offset_days=N it's the N days
    before that.

    Returns one row per active employee, with NULL where data is missing
    (e.g. employees with zero peer_feedback in the window are kept but
    their peer_sentiment column is None).
    """
    end = days_back + offset_days
    start = offset_days
    sql = f"""
      WITH activity AS (
        SELECT a.emp_id,
               AVG(a.tasks_done)   AS tasks_done,
               AVG(a.hours_logged) AS hours_logged
        FROM activity_daily a
        WHERE date(a.date) >= date((SELECT MAX(date) FROM activity_daily), '-{end} day')
          AND date(a.date) <  date((SELECT MAX(date) FROM activity_daily), '-{start} day')
          AND a.is_weekend = 0
        GROUP BY a.emp_id
      ),
      digital AS (
        SELECT d.emp_id,
               AVG(d.focus_score)    AS focus_score,
               AVG(d.working_hours)  AS working_hours
        FROM digital_patterns_daily d
        WHERE date(d.date) >= date((SELECT MAX(date) FROM digital_patterns_daily), '-{end} day')
          AND date(d.date) <  date((SELECT MAX(date) FROM digital_patterns_daily), '-{start} day')
        GROUP BY d.emp_id
      ),
      wear AS (
        SELECT w.emp_id,
               AVG(w.stress_index) AS stress_index,
               AVG(w.sleep_h)      AS sleep_h
        FROM wearables_daily w
        WHERE date(w.date) >= date((SELECT MAX(date) FROM wearables_daily), '-{end} day')
          AND date(w.date) <  date((SELECT MAX(date) FROM wearables_daily), '-{start} day')
        GROUP BY w.emp_id
      ),
      peer AS (
        SELECT pf.emp_id,
               AVG(pf.sentiment_score) AS peer_sentiment
        FROM peer_feedback pf
        WHERE date(pf.ts) >= date((SELECT MAX(ts) FROM peer_feedback), '-{end} day')
          AND date(pf.ts) <  date((SELECT MAX(ts) FROM peer_feedback), '-{start} day')
        GROUP BY pf.emp_id
      )
      SELECT e.emp_id, e.full_name, e.archetype,
             p.title  AS position_title,
             u.unit_id, u.name AS unit_name,
             activity.tasks_done, activity.hours_logged,
             digital.focus_score, digital.working_hours,
             wear.stress_index, wear.sleep_h,
             peer.peer_sentiment
      FROM employees e
      LEFT JOIN positions p ON p.position_id = e.position_id
      LEFT JOIN units u     ON u.unit_id     = e.unit_id
      LEFT JOIN activity    ON activity.emp_id = e.emp_id
      LEFT JOIN digital     ON digital.emp_id  = e.emp_id
      LEFT JOIN wear        ON wear.emp_id     = e.emp_id
      LEFT JOIN peer        ON peer.emp_id     = e.emp_id
      WHERE e.status = 'active'
    """
    return list(db.query(sql))


def _flag_count(row: dict[str, Any], flags: dict[str, tuple[str, float, str]]) -> int:
    """Count how many flags trip for `row`. Missing values do NOT trip."""
    n = 0
    for col, threshold, op in flags.values():
        v = row.get(col)
        if v is None:
            continue
        if op == "lt" and v < threshold:
            n += 1
        elif op == "gt" and v > threshold:
            n += 1
    return n


def _flagged_reasons(row: dict[str, Any], flags: dict[str, tuple[str, float, str]]) -> list[str]:
    out: list[str] = []
    for label, (col, threshold, op) in flags.items():
        v = row.get(col)
        if v is None:
            continue
        if op == "lt" and v < threshold:
            out.append(label)
        elif op == "gt" and v > threshold:
            out.append(label)
    return out


# ---------------------------------------------------------------------------
# Hot department — composite (sentiment − stress) per unit
# ---------------------------------------------------------------------------

def _hot_department(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Worst (lowest) composite unit. Composite = mean_sentiment − mean_stress.

    Returns {unit_id, unit_name, score, sentiment, stress, n_employees,
             delta_vs_norm} or None if there are no scorable units.
    """
    by_unit: dict[str | None, dict[str, Any]] = {}
    for r in rows:
        u = r.get("unit_id")
        if u is None:
            continue
        b = by_unit.setdefault(u, {
            "unit_id": u,
            "unit_name": r.get("unit_name") or u,
            "stress_sum": 0.0, "stress_n": 0,
            "sentiment_sum": 0.0, "sentiment_n": 0,
            "n_employees": 0,
        })
        b["n_employees"] += 1
        if r.get("stress_index") is not None:
            b["stress_sum"] += r["stress_index"]; b["stress_n"] += 1
        if r.get("peer_sentiment") is not None:
            b["sentiment_sum"] += r["peer_sentiment"]; b["sentiment_n"] += 1

    scorable: list[dict[str, Any]] = []
    for b in by_unit.values():
        if b["stress_n"] == 0 or b["sentiment_n"] == 0:
            continue
        stress = b["stress_sum"] / b["stress_n"]
        sentiment = b["sentiment_sum"] / b["sentiment_n"]
        scorable.append({
            "unit_id": b["unit_id"],
            "name": b["unit_name"],
            "stress": round(stress, 3),
            "sentiment": round(sentiment, 3),
            "score": round(sentiment - stress, 3),
            "n_employees": b["n_employees"],
        })
    if not scorable:
        return None

    scorable.sort(key=lambda x: x["score"])
    worst = scorable[0]
    norm = sum(x["score"] for x in scorable) / len(scorable)
    worst["delta_vs_norm"] = round(worst["score"] - norm, 3)
    return worst


# ---------------------------------------------------------------------------
# Trust like-rate from data/logs/feedback.jsonl
# ---------------------------------------------------------------------------

def _read_feedback(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or (PATHS.logs / "feedback.jsonl")
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def _ts_to_date(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _trust(records: list[dict[str, Any]], *, days_back: int, offset_days: int = 0,
            now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    end = now.timestamp() - offset_days * 86400
    start = now.timestamp() - (offset_days + days_back) * 86400
    likes = 0
    dislikes = 0
    for r in records:
        d = _ts_to_date(r.get("ts", ""))
        if d is None:
            continue
        t = d.timestamp()
        if not (start <= t < end):
            continue
        v = r.get("verdict")
        if v == "up":
            likes += 1
        elif v == "down":
            dislikes += 1
    total = likes + dislikes
    pct = round(100.0 * likes / total, 1) if total > 0 else None
    return {"likes": likes, "dislikes": dislikes, "total": total, "pct": pct}


# ---------------------------------------------------------------------------
# KPI strip — the contract above the fold
# ---------------------------------------------------------------------------

def get_kpi_strip(*, window: int = 30, db: Database | None = None,
                    now: datetime | None = None,
                    feedback_path: Path | None = None) -> dict[str, Any]:
    """The 4 hero numbers + their deltas vs the prior `window` days.

    Returns:
      {
        "window_days": int,
        "at_risk":  {value, delta, total, threshold_text},
        "burnout":  {value, delta, total, threshold_text},
        "hot_dept": {name, unit_id, score, delta_vs_norm, sentiment, stress, n_employees},
        "trust":    {pct, delta_pp, likes, dislikes, total},
      }
    `delta` is signed integer (current − prior). `delta_pp` for trust is
    in percentage points (rounded to 1 decimal). `None` when prior data
    is empty.
    """
    db = _db(db)
    now = now or datetime.now(timezone.utc)

    cur_rows = _employee_composite(db, days_back=window, offset_days=0)
    prv_rows = _employee_composite(db, days_back=window, offset_days=window)

    def count_flagged(rows: list[dict[str, Any]],
                       flags: dict[str, tuple[str, float, str]],
                       min_flags: int) -> int:
        return sum(1 for r in rows if _flag_count(r, flags) >= min_flags)

    cur_at_risk = count_flagged(cur_rows, AT_RISK_FLAGS, AT_RISK_MIN_FLAGS)
    prv_at_risk = count_flagged(prv_rows, AT_RISK_FLAGS, AT_RISK_MIN_FLAGS) if prv_rows else None
    cur_burnout = count_flagged(cur_rows, BURNOUT_FLAGS, BURNOUT_MIN_FLAGS)
    prv_burnout = count_flagged(prv_rows, BURNOUT_FLAGS, BURNOUT_MIN_FLAGS) if prv_rows else None

    hot = _hot_department(cur_rows)

    fb = _read_feedback(feedback_path)
    trust_cur = _trust(fb, days_back=window, offset_days=0, now=now)
    trust_prv = _trust(fb, days_back=window, offset_days=window, now=now)
    delta_pp = (round(trust_cur["pct"] - trust_prv["pct"], 1)
                if trust_cur["pct"] is not None and trust_prv["pct"] is not None else None)

    return {
        "window_days": window,
        "at_risk": {
            "value": cur_at_risk,
            "delta": (cur_at_risk - prv_at_risk) if prv_at_risk is not None else None,
            "total": len(cur_rows),
            "threshold_text": f"≥{AT_RISK_MIN_FLAGS}/4 флагов: low sentiment / low focus / low tasks / low hours",
        },
        "burnout": {
            "value": cur_burnout,
            "delta": (cur_burnout - prv_burnout) if prv_burnout is not None else None,
            "total": len(cur_rows),
            "threshold_text": f"≥{BURNOUT_MIN_FLAGS}/4 флагов: long hours / high stress / low sleep / low sentiment",
        },
        "hot_dept": hot or {"name": None, "unit_id": None, "score": None,
                              "delta_vs_norm": None, "sentiment": None,
                              "stress": None, "n_employees": 0},
        "trust": {
            "pct": trust_cur["pct"],
            "delta_pp": delta_pp,
            "likes": trust_cur["likes"],
            "dislikes": trust_cur["dislikes"],
            "total": trust_cur["total"],
        },
    }


# ---------------------------------------------------------------------------
# Workforce heatmap — units × metrics
# ---------------------------------------------------------------------------

HEATMAP_METRICS: list[dict[str, Any]] = [
    {"key": "stress_index",   "label": "стресс",     "direction": "lower_is_better"},
    {"key": "sleep_h",        "label": "сон",        "direction": "higher_is_better"},
    {"key": "hours_logged",   "label": "часы",       "direction": "near_norm", "norm": 8.0},
    {"key": "focus_score",    "label": "фокус",      "direction": "higher_is_better"},
    {"key": "peer_sentiment", "label": "sentiment",  "direction": "higher_is_better"},
]


def get_workforce_heatmap(*, window: int = 30,
                            db: Database | None = None) -> dict[str, Any]:
    """Heatmap data: rows=units, cols=metrics, values=mean over window.

    Severity is normalized: 0 ≈ at population mean, +1/+2/+3 = better,
    -1/-2/-3 = worse. Direction respected. UI uses severity to pick a
    cell tint without re-implementing the math.
    """
    db = _db(db)
    rows = _employee_composite(db, days_back=window)

    # collect per-unit means + global means
    units: dict[str, dict[str, Any]] = {}
    for r in rows:
        u = r.get("unit_id")
        if u is None:
            continue
        units.setdefault(u, {"unit_id": u, "unit_name": r.get("unit_name") or u,
                              "n_employees": 0, "metrics": {}})
        units[u]["n_employees"] += 1
        for m in HEATMAP_METRICS:
            v = r.get(m["key"])
            if v is None:
                continue
            bucket = units[u]["metrics"].setdefault(m["key"], {"sum": 0.0, "n": 0})
            bucket["sum"] += v; bucket["n"] += 1

    # global mean + std for severity
    global_stats: dict[str, dict[str, float]] = {}
    for m in HEATMAP_METRICS:
        vals = [r[m["key"]] for r in rows if r.get(m["key"]) is not None]
        if not vals:
            global_stats[m["key"]] = {"mean": 0.0, "std": 1.0}
            continue
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / max(1, len(vals) - 1)
        std = var ** 0.5 if var > 0 else 1.0
        global_stats[m["key"]] = {"mean": mean, "std": std}

    cells: list[dict[str, Any]] = []
    for u in units.values():
        for m in HEATMAP_METRICS:
            b = u["metrics"].get(m["key"])
            if b is None or b["n"] == 0:
                cells.append({"unit_id": u["unit_id"], "unit_name": u["unit_name"],
                              "metric": m["key"], "metric_label": m["label"],
                              "value": None, "severity": 0.0, "n": 0})
                continue
            value = b["sum"] / b["n"]
            mean = global_stats[m["key"]]["mean"]
            std = global_stats[m["key"]]["std"] or 1.0
            z = (value - mean) / std
            if m["direction"] == "lower_is_better":
                severity = -z
            elif m["direction"] == "higher_is_better":
                severity = z
            else:  # near_norm
                norm = m.get("norm", mean)
                severity = -abs(value - norm) / std
            severity = max(-3.0, min(3.0, severity))
            cells.append({
                "unit_id": u["unit_id"], "unit_name": u["unit_name"],
                "metric": m["key"], "metric_label": m["label"],
                "value": round(value, 3),
                "severity": round(severity, 2),
                "n": b["n"],
            })

    return {
        "window_days": window,
        "metrics": HEATMAP_METRICS,
        "units": [{"unit_id": u["unit_id"], "unit_name": u["unit_name"],
                    "n_employees": u["n_employees"]}
                   for u in sorted(units.values(), key=lambda x: x["unit_name"])],
        "cells": cells,
    }


# ---------------------------------------------------------------------------
# At-risk Top-N
# ---------------------------------------------------------------------------

def get_at_risk_top(*, n: int = 7, window: int = 30,
                      db: Database | None = None) -> list[dict[str, Any]]:
    """Top-N at-risk active employees by flag count, then by composite badness.

    Returns: [{emp_id, full_name, position, unit_name, archetype, flags,
               flag_count, top_metrics: [{key, value}]}]
    """
    db = _db(db)
    rows = _employee_composite(db, days_back=window)
    scored: list[dict[str, Any]] = []
    for r in rows:
        n_flags = _flag_count(r, AT_RISK_FLAGS)
        if n_flags == 0:
            continue
        # Tie-breaker: low_sentiment dominates, then focus, then hours, then tasks
        sentiment = r.get("peer_sentiment")
        focus = r.get("focus_score")
        hours = r.get("hours_logged")
        tasks = r.get("tasks_done")
        # Lower badness rank = worse. We want descending by flag_count.
        scored.append({
            "emp_id": r["emp_id"],
            "full_name": r.get("full_name") or r["emp_id"],
            "position": r.get("position_title"),
            "unit_id": r.get("unit_id"),
            "unit_name": r.get("unit_name"),
            "archetype": r.get("archetype"),
            "flag_count": n_flags,
            "flags": _flagged_reasons(r, AT_RISK_FLAGS),
            "metrics": {
                "peer_sentiment": round(sentiment, 3) if sentiment is not None else None,
                "focus_score":    round(focus, 3) if focus is not None else None,
                "hours_logged":   round(hours, 2) if hours is not None else None,
                "tasks_done":     round(tasks, 2) if tasks is not None else None,
            },
        })
    scored.sort(key=lambda x: (
        -x["flag_count"],
        x["metrics"]["peer_sentiment"] if x["metrics"]["peer_sentiment"] is not None else 1.0,
        x["metrics"]["focus_score"] if x["metrics"]["focus_score"] is not None else 1.0,
    ))
    return scored[:n]


# ---------------------------------------------------------------------------
# Archetype scatter
# ---------------------------------------------------------------------------

def get_archetype_scatter(*, window: int = 30,
                            db: Database | None = None) -> dict[str, Any]:
    """Scatter: x = stress_index, y = focus_score, color = archetype.

    Returns {points: [{emp_id, full_name, archetype, x, y, unit_name,
    position}], archetypes: [name, ...] in stable order}.
    """
    db = _db(db)
    rows = _employee_composite(db, days_back=window)
    points: list[dict[str, Any]] = []
    arch_set: list[str] = []
    for r in rows:
        x = r.get("stress_index")
        y = r.get("focus_score")
        if x is None or y is None:
            continue
        a = r.get("archetype") or "—"
        if a not in arch_set:
            arch_set.append(a)
        points.append({
            "emp_id": r["emp_id"],
            "full_name": r.get("full_name") or r["emp_id"],
            "archetype": a,
            "x": round(x, 3),
            "y": round(y, 3),
            "unit_name": r.get("unit_name"),
            "position": r.get("position_title"),
        })
    return {"window_days": window, "points": points,
            "archetypes": sorted(arch_set)}


# ---------------------------------------------------------------------------
# Trust timeline — daily likes/dislikes + release markers
# ---------------------------------------------------------------------------

def _git_log_releases(repo: Path | None = None,
                       since_days: int = 90) -> list[dict[str, Any]]:
    """List release commits (those that touched VERSION).

    Returns [{hash, date (YYYY-MM-DD), version, subject, self_evolved}].
    """
    repo = repo or PATHS.repo
    try:
        # `--name-only` listing every commit's touched files lets us flag the
        # ones that bumped VERSION — i.e. releases per the constitution P9.
        out = subprocess.run(
            ["git", "-C", str(repo), "log",
             f"--since={since_days} days ago",
             "--pretty=format:%H|%ai|%s",
             "--name-only"],
            check=True, capture_output=True, text=True, timeout=10,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return []

    out_list: list[dict[str, Any]] = []
    blocks = out.split("\n\n")
    for block in blocks:
        lines = [l for l in block.split("\n") if l]
        if not lines:
            continue
        head = lines[0]
        files = lines[1:]
        if "VERSION" not in files:
            continue
        try:
            h, ai, subj = head.split("|", 2)
        except ValueError:
            continue
        date = ai[:10]
        vmatch = re.search(r"v(\d+\.\d+\.\d+)", subj)
        version = vmatch.group(1) if vmatch else None
        # detect self-evolved trailer
        try:
            body = subprocess.run(
                ["git", "-C", str(repo), "show", "-s", "--format=%B", h],
                check=True, capture_output=True, text=True, timeout=5,
            ).stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            body = ""
        self_evolved = "Self-Evolved-By:" in body
        out_list.append({
            "hash": h[:7],
            "date": date,
            "version": version,
            "subject": subj,
            "self_evolved": self_evolved,
        })
    return out_list


def get_trust_timeline(*, window: int = 30,
                         now: datetime | None = None,
                         feedback_path: Path | None = None) -> dict[str, Any]:
    """Daily likes / dislikes for the last `window` days + release markers.

    Returns {window_days, days: [{date, likes, dislikes}], releases: [...]}
    """
    now = now or datetime.now(timezone.utc)
    fb = _read_feedback(feedback_path)
    by_day: dict[str, dict[str, int]] = {}
    cutoff = now.timestamp() - window * 86400
    for r in fb:
        d = _ts_to_date(r.get("ts", ""))
        if d is None or d.timestamp() < cutoff:
            continue
        key = d.date().isoformat()
        bucket = by_day.setdefault(key, {"likes": 0, "dislikes": 0})
        if r.get("verdict") == "up":
            bucket["likes"] += 1
        elif r.get("verdict") == "down":
            bucket["dislikes"] += 1
    days = [{"date": k, "likes": v["likes"], "dislikes": v["dislikes"]}
            for k, v in sorted(by_day.items())]
    releases = _git_log_releases(since_days=window)
    return {"window_days": window, "days": days, "releases": releases}


# ---------------------------------------------------------------------------
# Evolution log — last N commits, flag self-evolved
# ---------------------------------------------------------------------------

def get_evolution_log(*, n: int = 10,
                       repo: Path | None = None) -> list[dict[str, Any]]:
    """Last N commits with version, subject, self_evolved flag, body excerpt."""
    repo = repo or PATHS.repo
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "log", f"-{int(n)}",
             "--pretty=format:%H%x1f%ai%x1f%s%x1f%b%x1e"],
            check=True, capture_output=True, text=True, timeout=10,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return []
    items: list[dict[str, Any]] = []
    for entry in out.split("\x1e"):
        entry = entry.strip("\n")
        if not entry:
            continue
        parts = entry.split("\x1f")
        if len(parts) < 3:
            continue
        h, ai, subj = parts[0], parts[1], parts[2]
        body = parts[3] if len(parts) > 3 else ""
        vmatch = re.search(r"v(\d+\.\d+\.\d+)", subj)
        items.append({
            "hash": h[:7],
            "date": ai[:10],
            "datetime": ai,
            "version": vmatch.group(1) if vmatch else None,
            "subject": subj,
            "self_evolved": "Self-Evolved-By:" in body,
            "body_excerpt": body.strip().split("\n\n")[0][:300] if body.strip() else None,
        })
    return items


# ---------------------------------------------------------------------------
# Rejected suggestions — parsed from data/memory/knowledge/rejected_suggestions.md
# ---------------------------------------------------------------------------

_REJECTED_HEADER = re.compile(r"^##\s+(?P<id>\S+)\s+—\s+(?P<ts>\S+)\s*$", re.MULTILINE)


def get_rejected_suggestions(*, n: int = 5,
                               path: Path | None = None) -> list[dict[str, Any]]:
    """Parse rejected_suggestions.md into structured records.

    Each entry begins with `## <id> — <ts>` and is followed by labelled
    sections. We extract verdict / reasoning / principle / hint when
    present. The most recent entries come last in the file (append-only),
    so we reverse to put them first.
    """
    path = path or (PATHS.knowledge / "rejected_suggestions.md")
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    matches = list(_REJECTED_HEADER.finditer(text))
    items: list[dict[str, Any]] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end]
        suggestion = _extract_quoted(block, "Предложение пользователя:")
        verdict = _extract_inline(block, "Вердикт:")
        reasoning = _extract_inline(block, "Обоснование:")
        principle = _extract_inline(block, "Конфликт с принципом:")
        hint = _extract_inline(block, "Подсказка для переформулирования:")
        items.append({
            "id": m.group("id"),
            "ts": m.group("ts"),
            "suggestion": suggestion,
            "verdict": verdict,
            "reasoning": reasoning,
            "principle": principle,
            "hint": hint,
        })
    items.reverse()
    return items[:n]


def _extract_quoted(block: str, label: str) -> str | None:
    m = re.search(rf"\*\*{re.escape(label)}\*\*\s*\n+>(.+?)(?=\n\n|\n\*\*|\Z)", block, re.DOTALL)
    if not m:
        return None
    return m.group(1).strip()


def _extract_inline(block: str, label: str) -> str | None:
    m = re.search(rf"\*\*{re.escape(label)}\*\*\s*([^\n]+)", block)
    if not m:
        return None
    val = m.group(1).strip().strip("`")
    return val or None


# ---------------------------------------------------------------------------
# Cost — daily token spend, Opus / Sonnet split, run-rate
# ---------------------------------------------------------------------------

def _classify_model(model: str | None) -> str:
    if not model:
        return "other"
    m = model.lower()
    if "opus" in m:
        return "opus"
    if "sonnet" in m:
        return "sonnet"
    if "haiku" in m:
        return "haiku"
    return "other"


def get_cost_breakdown(*, window: int = 30,
                        path: Path | None = None,
                        now: datetime | None = None) -> dict[str, Any]:
    """Daily token spend split by model class. From data/logs/budget.jsonl."""
    p = path or (PATHS.logs / "budget.jsonl")
    now = now or datetime.now(timezone.utc)
    if not p.exists():
        return {"window_days": window, "days": [], "total_window_usd": 0.0,
                 "by_model_usd": {"opus": 0.0, "sonnet": 0.0, "haiku": 0.0, "other": 0.0},
                 "run_rate_usd_30d": 0.0}
    cutoff = now.timestamp() - window * 86400
    by_day: dict[str, dict[str, float]] = {}
    by_model: dict[str, float] = {"opus": 0.0, "sonnet": 0.0, "haiku": 0.0, "other": 0.0}
    total = 0.0
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            continue
        d = _ts_to_date(r.get("ts", ""))
        if d is None or d.timestamp() < cutoff:
            continue
        usd = float(r.get("usd", 0.0) or 0.0)
        cls = _classify_model(r.get("model"))
        key = d.date().isoformat()
        bucket = by_day.setdefault(key, {"opus": 0.0, "sonnet": 0.0,
                                            "haiku": 0.0, "other": 0.0})
        bucket[cls] += usd
        by_model[cls] += usd
        total += usd
    days = [{"date": k, **{m: round(v, 4) for m, v in d.items()}}
            for k, d in sorted(by_day.items())]
    # Project the partial window to 30 days
    days_observed = max(1, len(days))
    run_rate = total / days_observed * 30
    return {
        "window_days": window,
        "days": days,
        "total_window_usd": round(total, 4),
        "by_model_usd": {k: round(v, 4) for k, v in by_model.items()},
        "run_rate_usd_30d": round(run_rate, 4),
    }


__all__ = [
    "AT_RISK_FLAGS", "AT_RISK_MIN_FLAGS",
    "BURNOUT_FLAGS", "BURNOUT_MIN_FLAGS",
    "HEATMAP_METRICS",
    "get_kpi_strip",
    "get_workforce_heatmap",
    "get_at_risk_top",
    "get_archetype_scatter",
    "get_trust_timeline",
    "get_evolution_log",
    "get_rejected_suggestions",
    "get_cost_breakdown",
]
