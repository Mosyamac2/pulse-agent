"""One-day tick: append a fresh row to every daily metric table and roll
stochastic events. Roughly 5 seconds CPU, 0 LLM tokens.

Logic mirrors §8 of the TZ:

1. Pick `D_next` = MAX(activity_daily.date) + 1 day. Skip weekends for
   activity & digital metrics; wearables flow on weekends too.
2. For each active employee, draw the daily row from an exponentially-smoothed
   blend of the last 7 days plus archetype mean.
3. Stochastic events with low per-day probabilities — promotion, sick leave,
   termination, new hire, peer feedback, assessment, JIRA, course transitions.
4. Apply archetype-specific effects (burnout-prone employees more likely to
   take sick leave; toxic_high_performer more peer_feedback with negative
   sentiment).
5. Log every stochastic event to `data/logs/events.jsonl`.
6. Flip `state.json::ml.needs_refresh = True` so the next prediction call can
   trigger a quick re-train if needed.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sqlite_utils import Database

from . import archetypes as A
from ..config import PATHS
from ..state import load_state, save_state
from .seed import _iso

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today_for_db(db: Database) -> date:
    """Latest date covered by ANY daily table — wearables run on weekends so they
    will be ahead of activity_daily on Saturdays/Sundays. Picking the max across
    all three tables prevents a tick from re-writing a weekend wearables row."""
    candidates: list[date] = []
    for table in ("activity_daily", "digital_patterns_daily", "wearables_daily"):
        rows = list(db.query(f"SELECT MAX(date) AS d FROM {table}"))
        if rows and rows[0]["d"]:
            candidates.append(date.fromisoformat(rows[0]["d"]))
    if not candidates:
        raise RuntimeError("DB has no daily-metric rows; run seed first.")
    return max(candidates)


def _ema(values: list[float], alpha: float = 0.4) -> float:
    if not values:
        return 0.0
    cur = values[0]
    for v in values[1:]:
        cur = alpha * v + (1 - alpha) * cur
    return cur


def _last_n_days(db: Database, table: str, emp_id: str, key: str,
                  upto: date, n: int) -> list[float]:
    lo = (upto - timedelta(days=n)).isoformat()
    hi = upto.isoformat()
    rows = list(db.query(
        f"SELECT {key} FROM {table} WHERE emp_id=:e AND date>=:lo AND date<=:hi ORDER BY date ASC",
        {"e": emp_id, "lo": lo, "hi": hi}))
    return [float(r[key]) for r in rows if r[key] is not None]


def _next_id(db: Database, table: str) -> int:
    """Auto-increment for tables with manual integer pk."""
    rows = list(db.query(f"SELECT MAX(id) AS m FROM {table}"))
    return int(rows[0]["m"] + 1) if rows and rows[0]["m"] else 1


def _log_event(kind: str, **payload) -> None:
    PATHS.ensure()
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, **payload}
    with (PATHS.logs / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Per-day metric synthesizer
# ---------------------------------------------------------------------------

def _generate_daily_row(rng: np.random.Generator, emp: dict, d: date,
                         last_tasks: list[float], last_focus: list[float],
                         last_stress: list[float], decline: float = 0.0) -> tuple[dict, dict, dict]:
    arc = A.by_name(emp["archetype"])
    is_weekend = d.weekday() >= 5

    # tasks
    base_tasks = arc.tasks_done_mean * (1.0 - 0.55 * decline)
    smooth_tasks = _ema(last_tasks)
    tasks = int(max(0, rng.normal(0.6 * smooth_tasks + 0.4 * base_tasks if smooth_tasks else base_tasks,
                                    arc.tasks_done_std)))
    hours = float(np.clip(rng.normal(arc.hours_logged_mean - 1.0 * decline, 0.8), 0, 14))
    meetings = int(max(0, rng.normal(arc.meetings_mean, 1.5)))

    smooth_focus = _ema(last_focus)
    base_focus = arc.focus_mean * (1.0 - 0.5 * decline)
    focus = float(np.clip(rng.normal(0.6 * smooth_focus + 0.4 * base_focus if smooth_focus else base_focus, 0.08), 0.0, 1.0))
    switches = float(np.clip(rng.normal(arc.switches_mean * (1 + 0.7 * decline), 1.0), 0.5, 12.0))
    working = float(np.clip(rng.normal(arc.working_hours_mean, 0.7), 0, 14))

    smooth_stress = _ema(last_stress)
    base_stress = arc.stress_mean * (1.0 + 0.3 * decline)
    stress = float(np.clip(rng.normal(0.6 * smooth_stress + 0.4 * base_stress if smooth_stress else base_stress, 0.10), 0, 1))
    sleep = float(np.clip(rng.normal(arc.sleep_h_mean, 0.6), 3.0, 10.0))
    steps = int(max(0, rng.normal(arc.steps_mean * (0.7 if is_weekend else 1.0), 1500)))
    hr = float(np.clip(rng.normal(72, 7), 50, 110))

    activity = {
        "emp_id": emp["emp_id"], "date": _iso(d),
        "tasks_done": tasks, "hours_logged": round(hours, 2),
        "meetings_count": meetings, "is_weekend": int(is_weekend),
    }
    digital = {
        "emp_id": emp["emp_id"], "date": _iso(d),
        "focus_score": round(focus, 3),
        "switches_per_min": round(switches, 2),
        "working_hours": round(working, 2),
    }
    wearable = {
        "emp_id": emp["emp_id"], "date": _iso(d),
        "steps": steps, "sleep_h": round(sleep, 2),
        "stress_index": round(stress, 3),
        "hr_avg": round(hr, 1),
    }
    return activity, digital, wearable


# ---------------------------------------------------------------------------
# Stochastic events
# ---------------------------------------------------------------------------

P_PROMOTION = 0.0007
P_TERMINATION = 0.0012
P_NEW_HIRE = 0.005          # bank-wide per day
P_NEW_ASSESSMENT = 0.005    # per emp per day (~ once / 200 days)
P_COURSE_START = 0.003
P_COURSE_COMPLETE = 0.004   # for in_progress only
P_BURNOUT_SICK = 0.012      # for burnout_prone only


def _maybe_promote(rng: np.random.Generator, db: Database, emp: dict, d: date) -> bool:
    if rng.random() >= P_PROMOTION:
        return False
    if emp["grade_level"] >= 5:
        return False
    new_grade = emp["grade_level"] + 1
    db["promotions"].insert({
        "id": _next_id(db, "promotions"),
        "emp_id": emp["emp_id"], "date": _iso(d),
        "from_grade": emp["grade_level"], "to_grade": new_grade,
        "from_position_id": emp["position_id"],
        "to_position_id": emp["position_id"],
    })
    db["employees"].update(emp["emp_id"], {"grade_level": new_grade})
    _log_event("daily_tick_event", subkind="promotion",
               emp_id=emp["emp_id"], from_grade=emp["grade_level"], to_grade=new_grade)
    return True


def _maybe_terminate(rng: np.random.Generator, db: Database, emp: dict, d: date) -> bool:
    arc = A.by_name(emp["archetype"])
    p = P_TERMINATION * (2.5 if arc.burnout_prone else 1.0) * (1.4 if arc.toxic else 1.0)
    if rng.random() >= p:
        return False
    db["employees"].update(emp["emp_id"], {"term_date": _iso(d), "status": "terminated"})
    _log_event("daily_tick_event", subkind="termination", emp_id=emp["emp_id"], date=_iso(d))
    return True


def _maybe_hire(rng: np.random.Generator, db: Database, d: date) -> bool:
    if rng.random() >= P_NEW_HIRE:
        return False
    state = load_state()
    next_idx = state.get("next_emp_idx", 100) + 1
    emp_id = f"emp_{next_idx:03d}"
    state["next_emp_idx"] = next_idx
    save_state(state)

    leaf_units = [u["unit_id"] for u in db["units"].rows if u["level"] == 2]
    positions = list(db["positions"].rows)
    pos = positions[int(rng.integers(0, len(positions)))]
    unit = leaf_units[int(rng.integers(0, len(leaf_units)))]
    db["employees"].insert({
        "emp_id": emp_id,
        "full_name": f"Новичок {emp_id}",
        "gender": "F" if rng.random() < 0.5 else "M",
        "birth_date": _iso(d - timedelta(days=int(rng.integers(8000, 14000)))),
        "city": "Москва",
        "education": "ВШЭ",
        "language_skills_json": json.dumps(["ru", "en"]),
        "hire_date": _iso(d),
        "term_date": "",
        "status": "active",
        "grade_level": 1,
        "position_id": pos["position_id"],
        "unit_id": unit,
        "archetype": "newbie_enthusiast",
    })
    _log_event("daily_tick_event", subkind="new_hire", emp_id=emp_id)
    return True


def _maybe_peer_feedback(rng: np.random.Generator, db: Database, emps: list[dict], d: date,
                          k: int = 5) -> int:
    """Generate roughly k peer feedback rows bank-wide."""
    n = int(rng.poisson(k))
    if n == 0 or len(emps) < 2:
        return 0
    inserted = 0
    for _ in range(n):
        i, j = rng.choice(len(emps), size=2, replace=False)
        target = emps[int(i)]
        author = emps[int(j)]
        if target["status"] != "active" or author["status"] != "active":
            continue
        arc = A.by_name(target["archetype"])
        sentiment = float(np.clip(rng.normal(arc.peer_sentiment_mean, 0.30), -1.0, 1.0))
        db["peer_feedback"].insert({
            "id": _next_id(db, "peer_feedback"),
            "emp_id": target["emp_id"], "from_id": author["emp_id"],
            "ts": _iso(d) + "T12:00:00",
            "sentiment_score": round(sentiment, 3),
            "category": str(rng.choice(["cooperation", "quality", "reliability", "leadership"])),
            "text_summary": "tick-generated",
        })
        inserted += 1
    return inserted


def _maybe_jira(rng: np.random.Generator, db: Database, emps: list[dict], d: date) -> int:
    if d.weekday() >= 5:
        return 0
    pos_type = {p["position_id"]: p["type"] for p in db["positions"].rows}
    it_emps = [e for e in emps if pos_type.get(e["position_id"]) == "IT" and e["status"] == "active"]
    if not it_emps:
        return 0
    n = int(rng.poisson(len(it_emps) * 0.8))
    rows = list(db.query("SELECT MAX(CAST(SUBSTR(issue_key, 7) AS INTEGER)) AS m FROM jira_issues"))
    next_num = (rows[0]["m"] or 0) + 1
    inserts: list[dict] = []
    for k in range(n):
        e = it_emps[int(rng.integers(0, len(it_emps)))]
        prio = str(rng.choice(["low", "med", "high", "critical"], p=[0.45, 0.40, 0.12, 0.03]))
        ttype = str(rng.choice(["bug", "feature", "task"], p=[0.30, 0.30, 0.40]))
        inserts.append({
            "issue_key": f"PULSE-{next_num + k:06d}",
            "emp_id": e["emp_id"], "status": "in_progress",
            "ts_created": _iso(d) + "T10:00:00",
            "ts_resolved": "",
            "type": ttype, "priority": prio,
            "summary": f"tick {ttype} {prio}",
        })
    if inserts:
        db["jira_issues"].insert_all(inserts)
    return len(inserts)


def _maybe_assessment(rng: np.random.Generator, db: Database, emp: dict, d: date) -> bool:
    if rng.random() >= P_NEW_ASSESSMENT:
        return False
    arc = A.by_name(emp["archetype"])
    base = 0.55 + 0.10 * (arc.perf_score_mean - 3.0)
    score = float(np.clip(rng.normal(base, 0.10), 0.05, 0.99))
    db["assessments"].insert({
        "id": _next_id(db, "assessments"),
        "emp_id": emp["emp_id"], "type": str(rng.choice(["sberq", "360", "systemic"])),
        "date": _iso(d), "score": round(score, 3),
        "details_json": json.dumps({"source": "tick"}),
    })
    return True


def _maybe_burnout_sick(rng: np.random.Generator, db: Database, emp: dict, d: date) -> bool:
    arc = A.by_name(emp["archetype"])
    if not arc.burnout_prone:
        return False
    if rng.random() >= P_BURNOUT_SICK:
        return False
    dur = int(rng.choice([2, 3, 5, 7]))
    db["vacations"].insert({
        "id": _next_id(db, "vacations"),
        "emp_id": emp["emp_id"], "kind": "sick",
        "start_date": _iso(d), "end_date": _iso(d + timedelta(days=dur)),
    })
    _log_event("daily_tick_event", subkind="sick_leave", emp_id=emp["emp_id"], days=dur)
    return True


# ---------------------------------------------------------------------------
# Top-level tick
# ---------------------------------------------------------------------------

def tick(db_path: Path | None = None, *, target_date: date | None = None,
         force: bool = False) -> dict[str, Any]:
    """Append one day to the DB. Returns a summary dict.

    Idempotency: if `target_date` already exists in `activity_daily` (or
    `wearables_daily` for weekends), refuses unless `force=True`.
    """
    db_path = db_path or PATHS.db
    db = Database(db_path)

    last = _today_for_db(db)
    d = target_date or (last + timedelta(days=1))

    # Idempotency check — wearables fills every day, activity only weekdays.
    table = "activity_daily" if d.weekday() < 5 else "wearables_daily"
    rows = list(db.query(
        f"SELECT COUNT(*) AS n FROM {table} WHERE date = :d",
        {"d": d.isoformat()}))
    if rows[0]["n"] > 0 and not force:
        return {"date": d.isoformat(), "skipped": "already exists", "rows_added": 0}

    rng = np.random.default_rng(42 + d.toordinal())

    employees = list(db["employees"].rows)
    actives = [e for e in employees if e["status"] == "active"]

    new_activity: list[dict] = []
    new_digital: list[dict] = []
    new_wearables: list[dict] = []
    is_weekend = d.weekday() >= 5

    next_aid = _next_id(db, "activity_daily")
    next_did = _next_id(db, "digital_patterns_daily")
    next_wid = _next_id(db, "wearables_daily")

    for e in actives:
        last_tasks = _last_n_days(db, "activity_daily", e["emp_id"], "tasks_done", d - timedelta(days=1), 7)
        last_focus = _last_n_days(db, "digital_patterns_daily", e["emp_id"], "focus_score", d - timedelta(days=1), 7)
        last_stress = _last_n_days(db, "wearables_daily", e["emp_id"], "stress_index", d - timedelta(days=1), 7)
        a, dg, w = _generate_daily_row(rng, e, d, last_tasks, last_focus, last_stress)
        if not is_weekend:
            a["id"] = next_aid; next_aid += 1
            new_activity.append(a)
            dg["id"] = next_did; next_did += 1
            new_digital.append(dg)
        w["id"] = next_wid; next_wid += 1
        new_wearables.append(w)

    if new_activity:
        db["activity_daily"].insert_all(new_activity)
    if new_digital:
        db["digital_patterns_daily"].insert_all(new_digital)
    if new_wearables:
        db["wearables_daily"].insert_all(new_wearables)

    # --- Stochastic events ---
    n_promo = sum(_maybe_promote(rng, db, e, d) for e in actives)
    n_term = sum(_maybe_terminate(rng, db, e, d) for e in actives)
    n_hire = int(_maybe_hire(rng, db, d))
    n_pf = _maybe_peer_feedback(rng, db, actives, d, k=5)
    n_jira = _maybe_jira(rng, db, actives, d)
    n_assess = sum(_maybe_assessment(rng, db, e, d) for e in actives)
    n_sick = sum(_maybe_burnout_sick(rng, db, e, d) for e in actives)

    # Mark ml refresh
    state = load_state()
    state["ml"]["needs_refresh"] = True
    state["tick"] = {"last_tick_date": d.isoformat(), "last_tick_ts": datetime.now(timezone.utc).isoformat()}
    save_state(state)

    summary = {
        "date": d.isoformat(),
        "rows_activity": len(new_activity),
        "rows_digital": len(new_digital),
        "rows_wearables": len(new_wearables),
        "events": {
            "promotions": n_promo, "terminations": n_term, "new_hires": n_hire,
            "peer_feedback": n_pf, "jira": n_jira,
            "assessments": n_assess, "burnout_sicks": n_sick,
        },
    }
    _log_event("daily_tick", **summary)
    log.info("tick %s: %s", d, summary)
    return summary


__all__ = ["tick"]
