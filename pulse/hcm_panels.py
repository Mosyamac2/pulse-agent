"""Read-only aggregates powering the Pulse-HCM façade tabs (P14, v2.0.0+).

Style mirrors pulse/dashboard.py — pure functions over `data/sber_hr.db`,
optional `db` arg for test injection. No side effects, no writes anywhere.

Phase D1 covers four tabs from the presentation:
  * Подбор (recruit)        — slide 6
  * Цели (goals)            — slide 8
  * Обучение (learning)     — slide 9
  * Оценка (assess)         — slide 11

Phase D2 will add: profile, structure, career, docs (КЭДО), analytics.

Every function returns plain JSON-serializable dicts/lists so the
FastAPI layer can hand them straight to the client without further
transformation.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlite_utils import Database

from .config import PATHS


# ---------------------------------------------------------------------------
# DB helper (mirrors pulse/dashboard.py)
# ---------------------------------------------------------------------------

def _db(db: Database | None) -> Database:
    return db if db is not None else Database(PATHS.db)


def _today_iso() -> str:
    """End-of-window anchor. Uses max(activity_daily.date) when DB is present
    so the panels follow the same synthetic "now" as dashboard.py."""
    db = Database(PATHS.db) if PATHS.db.exists() else None
    if db is None:
        return date.today().isoformat()
    rows = list(db.query("SELECT MAX(date) m FROM activity_daily"))
    return rows[0]["m"] or date.today().isoformat()


def _days_between(d_iso_a: str, d_iso_b: str) -> int:
    return (date.fromisoformat(d_iso_b) - date.fromisoformat(d_iso_a)).days


# ===========================================================================
# Recruit — slide 6: Подбор и адаптация
# ===========================================================================

def get_recruit_summary(*, window: int = 30,
                          db: Database | None = None) -> dict[str, Any]:
    """KPI strip for the Подбор tab.

    Returns counts that match the screen tabs of the presentation
    («черновики / на согласовании / в работе / приостановленные / закрытые»)
    plus pipeline size and average time-to-close in days.
    """
    db = _db(db)
    today = _today_iso()
    cutoff = (date.fromisoformat(today) - timedelta(days=window)).isoformat()

    rows = list(db.query("SELECT status, COUNT(*) c FROM vacancies GROUP BY status"))
    by_status = {r["status"]: r["c"] for r in rows}

    pipeline = list(db.query("""
        SELECT COUNT(*) c FROM candidates c
        JOIN vacancies v USING(vacancy_id)
        WHERE v.status IN ('active', 'in_review')
          AND c.funnel_stage NOT IN ('hired', 'rejected')
    """))
    pipeline_count = pipeline[0]["c"] if pipeline else 0

    closed_recently = list(db.query("""
        SELECT COUNT(*) c FROM vacancies
        WHERE status='closed' AND closed_date >= :cutoff
    """, {"cutoff": cutoff}))
    closed_recent = closed_recently[0]["c"] if closed_recently else 0

    avg_ttc = list(db.query("""
        SELECT AVG(julianday(closed_date) - julianday(opened_date)) avg_days
        FROM vacancies WHERE status='closed' AND closed_date IS NOT NULL
    """))
    avg_days = float(avg_ttc[0]["avg_days"]) if avg_ttc and avg_ttc[0]["avg_days"] is not None else 0.0

    return {
        "draft_count":            by_status.get("draft", 0),
        "in_review_count":        by_status.get("in_review", 0),
        "active_count":           by_status.get("active", 0),
        "paused_count":           by_status.get("paused", 0),
        "closed_count":           by_status.get("closed", 0),
        "closed_recently_count":  closed_recent,
        "candidates_in_pipeline": pipeline_count,
        "avg_time_to_close_days": round(avg_days, 1),
        "window_days":            window,
    }


def list_active_vacancies(*, status: str | None = "active",
                            db: Database | None = None) -> list[dict[str, Any]]:
    """Table content for the Подбор tab. By default returns active; pass
    other status names to switch tabs. Each row is denormalized for
    rendering: hiring_manager_name, recruiter_name, candidates_count,
    days_open."""
    db = _db(db)
    where = ""
    params: dict[str, Any] = {}
    if status:
        where = "WHERE v.status = :status"
        params["status"] = status

    today = _today_iso()
    rows = list(db.query(f"""
        SELECT v.vacancy_id, v.title, v.type, v.status,
               v.opened_date, v.target_close_date, v.closed_date,
               v.is_internal_only, v.unit_id,
               hm.full_name AS hiring_manager_name,
               hm.emp_id    AS hiring_manager_id,
               rc.full_name AS recruiter_name,
               rc.emp_id    AS recruiter_id,
               u.name       AS unit_name,
               (SELECT COUNT(*) FROM candidates c
                  WHERE c.vacancy_id = v.vacancy_id) AS candidates_count
        FROM vacancies v
        LEFT JOIN employees hm ON hm.emp_id = v.hiring_manager_id
        LEFT JOIN employees rc ON rc.emp_id = v.recruiter_id
        LEFT JOIN units u      ON u.unit_id  = v.unit_id
        {where}
        ORDER BY v.opened_date DESC
    """, params))

    out: list[dict[str, Any]] = []
    for r in rows:
        days_open = _days_between(r["opened_date"], today) if r["opened_date"] else 0
        out.append({
            **r,
            "days_open": days_open,
        })
    return out


def get_vacancy_detail(vacancy_id: str, *,
                         db: Database | None = None) -> dict[str, Any] | None:
    """Vacancy row + funnel breakdown of its candidates (each stage with count
    and the candidate list). Used for the drill-down accordion."""
    db = _db(db)
    vrows = list(db.query("""
        SELECT v.*, hm.full_name AS hiring_manager_name,
               rc.full_name AS recruiter_name, u.name AS unit_name
        FROM vacancies v
        LEFT JOIN employees hm ON hm.emp_id = v.hiring_manager_id
        LEFT JOIN employees rc ON rc.emp_id = v.recruiter_id
        LEFT JOIN units u      ON u.unit_id  = v.unit_id
        WHERE v.vacancy_id = :v
    """, {"v": vacancy_id}))
    if not vrows:
        return None
    v = vrows[0]

    cands = list(db.query("""
        SELECT candidate_id, full_name, source, internal_emp_id,
               funnel_stage, applied_date, stage_updated_date, score
        FROM candidates WHERE vacancy_id = :v
        ORDER BY applied_date DESC
    """, {"v": vacancy_id}))

    funnel: dict[str, list[dict[str, Any]]] = {}
    for c in cands:
        funnel.setdefault(c["funnel_stage"], []).append(c)

    return {
        "vacancy": v,
        "funnel":  funnel,
        "candidates_count": len(cands),
    }


# ===========================================================================
# Goals — slide 8: Целеполагание
# ===========================================================================

_DEFAULT_PERIOD = "2026-Q2"


def get_goals_summary(*, emp_id: str | None = None,
                        period: str | None = None,
                        db: Database | None = None) -> dict[str, Any]:
    """KPI strip for the Цели tab. Without emp_id — company-wide for period.
    With emp_id — that employee's slice."""
    db = _db(db)
    period = period or _DEFAULT_PERIOD

    where = "WHERE g.period = :period"
    params: dict[str, Any] = {"period": period}
    if emp_id:
        where += " AND g.emp_id = :emp"
        params["emp"] = emp_id

    rows = list(db.query(f"""
        SELECT
            COUNT(*)                                                 AS goals_total,
            SUM(CASE WHEN status='accepted'    THEN 1 ELSE 0 END)    AS accepted,
            SUM(CASE WHEN status='proposed'    THEN 1 ELSE 0 END)    AS proposed,
            SUM(CASE WHEN status='in_progress' THEN 1 ELSE 0 END)    AS in_progress,
            SUM(CASE WHEN status='done'        THEN 1 ELSE 0 END)    AS done,
            SUM(CASE WHEN status='cancelled'   THEN 1 ELSE 0 END)    AS cancelled,
            AVG(progress_pct)                                        AS avg_progress
        FROM goals g
        {where}
    """, params))
    r = rows[0] if rows else {}

    today = _today_iso()
    overdue = list(db.query(f"""
        SELECT COUNT(*) c FROM goals g {where} AND status='in_progress' AND due_date < :today
    """, {**params, "today": today}))
    overdue_count = overdue[0]["c"] if overdue else 0

    return {
        "period":       period,
        "scope":        "employee" if emp_id else "company",
        "emp_id":       emp_id,
        "goals_total":  int(r.get("goals_total") or 0),
        "accepted":     int(r.get("accepted") or 0),
        "proposed":     int(r.get("proposed") or 0),
        "in_progress":  int(r.get("in_progress") or 0),
        "done":         int(r.get("done") or 0),
        "cancelled":    int(r.get("cancelled") or 0),
        "avg_progress": round(float(r.get("avg_progress") or 0.0), 3),
        "overdue":      overdue_count,
    }


def list_my_goals(emp_id: str, *, period: str | None = None,
                    db: Database | None = None) -> list[dict[str, Any]]:
    """Goals for one employee for one period, with attached KRs."""
    db = _db(db)
    period = period or _DEFAULT_PERIOD
    goals = list(db.query("""
        SELECT * FROM goals
        WHERE emp_id = :e AND period = :p
        ORDER BY weight DESC, due_date
    """, {"e": emp_id, "p": period}))
    if not goals:
        return []
    ids = [g["goal_id"] for g in goals]
    placeholders = ",".join(f":g{i}" for i in range(len(ids)))
    krs = list(db.query(
        f"SELECT * FROM key_results WHERE goal_id IN ({placeholders})",
        {f"g{i}": gid for i, gid in enumerate(ids)},
    ))
    by_goal: dict[str, list[dict[str, Any]]] = {}
    for kr in krs:
        by_goal.setdefault(kr["goal_id"], []).append(kr)

    today = _today_iso()
    out: list[dict[str, Any]] = []
    for g in goals:
        out.append({
            **g,
            "key_results": by_goal.get(g["goal_id"], []),
            "is_overdue": (g["status"] == "in_progress"
                            and g["due_date"] is not None
                            and g["due_date"] < today),
        })
    return out


def list_team_goals(manager_emp_id: str, *, period: str | None = None,
                      db: Database | None = None) -> list[dict[str, Any]]:
    """Aggregated goal status per direct subordinate.

    Heuristic: subordinates = active employees in the same unit_id whose
    grade_level is strictly less than the manager's. The synthetic schema
    has no manager_id pointer; this proxy is the closest faithful read.
    """
    db = _db(db)
    period = period or _DEFAULT_PERIOD
    mgrs = list(db.query("SELECT * FROM employees WHERE emp_id = :e", {"e": manager_emp_id}))
    if not mgrs:
        return []
    mgr = mgrs[0]
    rows = list(db.query("""
        SELECT e.emp_id, e.full_name, e.archetype,
               COUNT(g.goal_id)                                                         AS goals_total,
               SUM(CASE WHEN g.status='in_progress' THEN 1 ELSE 0 END)                 AS in_progress,
               SUM(CASE WHEN g.status='done'        THEN 1 ELSE 0 END)                 AS done,
               SUM(CASE WHEN g.status='proposed'    THEN 1 ELSE 0 END)                 AS proposed,
               AVG(g.progress_pct)                                                      AS avg_progress
        FROM employees e
        LEFT JOIN goals g ON g.emp_id = e.emp_id AND g.period = :p
        WHERE e.unit_id = :u AND e.grade_level < :gl AND e.status='active'
        GROUP BY e.emp_id
        ORDER BY avg_progress
    """, {"p": period, "u": mgr["unit_id"], "gl": mgr["grade_level"]}))
    return [
        {**r, "avg_progress": round(float(r["avg_progress"] or 0.0), 3)}
        for r in rows
    ]


# ===========================================================================
# Learning — slide 9: Развитие и обучение
# ===========================================================================

def get_learning_feed(emp_id: str, *, limit: int = 20,
                        db: Database | None = None) -> list[dict[str, Any]]:
    """Most recent feed cards for one employee, decorated with course title
    when course_id is set."""
    db = _db(db)
    rows = list(db.query("""
        SELECT lf.*, c.title AS course_title, c.topic AS course_topic
        FROM learning_feed lf
        LEFT JOIN courses c USING(course_id)
        WHERE lf.emp_id = :e
        ORDER BY lf.recommended_date DESC
        LIMIT :n
    """, {"e": emp_id, "n": max(1, min(200, limit))}))
    return rows


def get_my_courses(emp_id: str, *, status: str | None = None,
                     db: Database | None = None) -> list[dict[str, Any]]:
    """Course enrollments for one employee. Filter by status if given."""
    db = _db(db)
    where = "WHERE ce.emp_id = :e"
    params: dict[str, Any] = {"e": emp_id}
    if status:
        where += " AND ce.status = :s"
        params["s"] = status
    rows = list(db.query(f"""
        SELECT ce.*, c.title, c.topic, c.duration_h, c.level
        FROM course_enrollments ce
        JOIN courses c USING(course_id)
        {where}
        ORDER BY ce.start_date DESC
    """, params))
    return rows


# ===========================================================================
# Assess — slide 10/11: Опросы / Оценка эффективности
# ===========================================================================

def get_assessment_campaigns(*, db: Database | None = None) -> dict[str, Any]:
    """Active and completed campaigns. Mirrors slide 10 split."""
    db = _db(db)
    rows = list(db.query("SELECT * FROM surveys_meta ORDER BY launched_date DESC"))
    active = [r for r in rows if r["status"] == "active"]
    completed = [r for r in rows if r["status"] == "completed"]
    return {
        "active":    active,
        "completed": completed,
        "total":     len(rows),
    }


def get_my_assessment(emp_id: str, *, period: str | None = None,
                        db: Database | None = None) -> dict[str, Any]:
    """Самооценка / 360 / итоговая оценка из таблиц assessments + performance_reviews.

    Returns the latest period's slice if `period` is None. Result mirrors
    slide 11: three blocks (samocenka / mneniya kolleg / ocenka sotrudnikov).
    """
    db = _db(db)
    # Latest performance_reviews row defines "period" if not given.
    if period is None:
        prs = list(db.query(
            "SELECT period FROM performance_reviews WHERE emp_id = :e "
            "ORDER BY period DESC LIMIT 1", {"e": emp_id},
        ))
        period = prs[0]["period"] if prs else None

    pr_row = None
    if period:
        rows = list(db.query(
            "SELECT * FROM performance_reviews WHERE emp_id = :e AND period = :p",
            {"e": emp_id, "p": period},
        ))
        if rows:
            pr_row = rows[0]

    # 360 / psych / sberq / systemic — last of each kind for this employee.
    aggs = list(db.query("""
        SELECT type, score, date, details_json FROM assessments
        WHERE emp_id = :e
        ORDER BY date DESC
    """, {"e": emp_id}))
    by_type: dict[str, dict[str, Any]] = {}
    for r in aggs:
        if r["type"] not in by_type:  # most recent for each kind
            by_type[r["type"]] = r

    # Peer feedback summary (sentiment over last year)
    peer = list(db.query("""
        SELECT AVG(sentiment_score) avg_sentiment, COUNT(*) n
        FROM peer_feedback WHERE emp_id = :e
    """, {"e": emp_id}))

    return {
        "emp_id":             emp_id,
        "period":             period,
        "performance_review": pr_row,
        "assessments":        by_type,
        "peer_summary": {
            "avg_sentiment": round(float(peer[0]["avg_sentiment"] or 0.0), 3) if peer else None,
            "n":             int(peer[0]["n"] or 0) if peer else 0,
        },
    }


# ===========================================================================
# Career — slide 7 (Поиск талантов и делегирования) + slide 12 (Моя карьера)
# ===========================================================================

def get_my_career(emp_id: str, *,
                    db: Database | None = None) -> dict[str, Any]:
    """Profile + talent_pool_status + recommendations stub.

    Used to render slide 12: «карьерный статус», список рекомендованных
    внутренних вакансий рассчитывается отдельно через `list_internal_vacancies`.
    """
    db = _db(db)
    emp = list(db.query(
        "SELECT * FROM employees WHERE emp_id = :e", {"e": emp_id}
    ))
    if not emp:
        return {}
    tps = list(db.query(
        "SELECT * FROM talent_pool_status WHERE emp_id = :e", {"e": emp_id}
    ))
    pos = list(db.query(
        "SELECT * FROM positions WHERE position_id = :p",
        {"p": emp[0]["position_id"]},
    ))
    unit = list(db.query(
        "SELECT * FROM units WHERE unit_id = :u",
        {"u": emp[0]["unit_id"]},
    ))
    return {
        "employee":           emp[0],
        "position":           pos[0] if pos else None,
        "unit":               unit[0] if unit else None,
        "talent_pool_status": tps[0] if tps else None,
    }


def list_internal_vacancies(emp_id: str, *,
                              db: Database | None = None) -> list[dict[str, Any]]:
    """Vacancies an employee could realistically apply to.

    Heuristic: status='active' AND (is_internal_only=1 OR position grade
    is within ±1 of the employee's grade_level). Skip vacancies the
    employee already manages.
    """
    db = _db(db)
    emp = list(db.query(
        "SELECT grade_level FROM employees WHERE emp_id = :e", {"e": emp_id}
    ))
    if not emp:
        return []
    g = int(emp[0]["grade_level"])

    rows = list(db.query("""
        SELECT v.*, u.name AS unit_name, p.title AS position_title,
               p.grade_level AS position_grade,
               (SELECT COUNT(*) FROM candidates c WHERE c.vacancy_id=v.vacancy_id) AS candidates_count
        FROM vacancies v
        LEFT JOIN positions p ON p.position_id = v.position_id
        LEFT JOIN units u     ON u.unit_id     = v.unit_id
        WHERE v.status='active' AND v.hiring_manager_id != :e
        ORDER BY v.opened_date DESC
    """, {"e": emp_id}))
    out = []
    for r in rows:
        pg = int(r.get("position_grade") or 0)
        if r["is_internal_only"] == 1 or abs(pg - g) <= 1:
            out.append(r)
    return out


def list_talent_search_results(query: dict[str, Any], *,
                                 db: Database | None = None) -> list[dict[str, Any]]:
    """Slide 7 «Поиск талантов» — extended search with filters.

    Supported filters in `query`:
      position_title (substring), grade_min, grade_max, unit_id,
      open_to_offers (1/0), min_recommended_by_count.
    """
    db = _db(db)
    where_parts = ["e.status='active'"]
    params: dict[str, Any] = {}

    if (g_min := query.get("grade_min")) is not None:
        where_parts.append("e.grade_level >= :gmin")
        params["gmin"] = int(g_min)
    if (g_max := query.get("grade_max")) is not None:
        where_parts.append("e.grade_level <= :gmax")
        params["gmax"] = int(g_max)
    if (u := query.get("unit_id")):
        where_parts.append("e.unit_id = :u")
        params["u"] = u
    if (pt := query.get("position_title")):
        where_parts.append("LOWER(p.title) LIKE :pt")
        params["pt"] = f"%{pt.lower()}%"
    if (oto := query.get("open_to_offers")) is not None:
        where_parts.append("t.open_to_offers = :oto")
        params["oto"] = int(bool(oto))
    if (mrc := query.get("min_recommended_by_count")) is not None:
        where_parts.append("t.recommended_by_count >= :mrc")
        params["mrc"] = int(mrc)

    where = " AND ".join(where_parts)
    rows = list(db.query(f"""
        SELECT e.emp_id, e.full_name, e.archetype, e.grade_level,
               p.title  AS position_title,
               u.name   AS unit_name,
               t.open_to_offers, t.recommended_by_count, t.career_track_preference
        FROM employees e
        LEFT JOIN positions p ON p.position_id = e.position_id
        LEFT JOIN units u     ON u.unit_id     = e.unit_id
        LEFT JOIN talent_pool_status t ON t.emp_id = e.emp_id
        WHERE {where}
        ORDER BY t.recommended_by_count DESC, e.full_name
        LIMIT 50
    """, params))
    return rows


def list_delegations(emp_id: str, *,
                       db: Database | None = None) -> dict[str, list[dict[str, Any]]]:
    """Slide 7 right side: «вы делегируете» / «вам делегировали» split."""
    db = _db(db)
    out_from = list(db.query("""
        SELECT d.*, e.full_name AS counterparty_name
        FROM delegations d JOIN employees e ON e.emp_id = d.to_emp_id
        WHERE d.from_emp_id = :e
        ORDER BY d.status, d.start_date DESC
    """, {"e": emp_id}))
    out_to = list(db.query("""
        SELECT d.*, e.full_name AS counterparty_name
        FROM delegations d JOIN employees e ON e.emp_id = d.from_emp_id
        WHERE d.to_emp_id = :e
        ORDER BY d.status, d.start_date DESC
    """, {"e": emp_id}))
    return {"i_delegate": out_from, "delegated_to_me": out_to}


# ===========================================================================
# Profile — slide 5: Профиль сотрудника + Структура компании
# ===========================================================================

def get_profile_full(emp_id: str, *,
                       db: Database | None = None) -> dict[str, Any]:
    """Full profile card backing slide 5 left panel.

    Joins employee + family + position + unit + last 3 career_history
    rows + last 3 performance_reviews + course_enrollments summary +
    peer_feedback summary.
    """
    db = _db(db)
    emp = list(db.query(
        "SELECT * FROM employees WHERE emp_id = :e", {"e": emp_id}
    ))
    if not emp:
        return {}
    pos = list(db.query(
        "SELECT * FROM positions WHERE position_id = :p",
        {"p": emp[0]["position_id"]},
    ))
    unit = list(db.query(
        "SELECT * FROM units WHERE unit_id = :u",
        {"u": emp[0]["unit_id"]},
    ))
    family = list(db.query(
        "SELECT * FROM family WHERE emp_id = :e", {"e": emp_id}
    ))
    career = list(db.query(
        "SELECT * FROM career_history WHERE emp_id = :e "
        "ORDER BY start_date DESC LIMIT 5", {"e": emp_id}
    ))
    perf = list(db.query(
        "SELECT * FROM performance_reviews WHERE emp_id = :e "
        "ORDER BY period DESC LIMIT 3", {"e": emp_id}
    ))
    enroll = list(db.query("""
        SELECT status, COUNT(*) c FROM course_enrollments
        WHERE emp_id = :e GROUP BY status
    """, {"e": emp_id}))
    enroll_dict = {r["status"]: r["c"] for r in enroll}
    peer = list(db.query("""
        SELECT AVG(sentiment_score) avg_sentiment, COUNT(*) n
        FROM peer_feedback WHERE emp_id = :e
    """, {"e": emp_id}))

    return {
        "employee":            emp[0],
        "position":            pos[0] if pos else None,
        "unit":                unit[0] if unit else None,
        "family":              family[0] if family else None,
        "career_history":      career,
        "performance_reviews": perf,
        "course_summary": {
            "completed":   enroll_dict.get("completed", 0),
            "in_progress": enroll_dict.get("in_progress", 0),
            "dropped":     enroll_dict.get("dropped", 0),
        },
        "peer_summary": {
            "avg_sentiment": round(float(peer[0]["avg_sentiment"] or 0.0), 3) if peer else None,
            "n":             int(peer[0]["n"] or 0) if peer else 0,
        },
    }


def get_org_structure(unit_id: str | None = None, *,
                        db: Database | None = None) -> dict[str, Any]:
    """Org tree for slide 5 right panel.

    Without unit_id: returns the root + first-level units, each annotated
    with headcount / leader / vacancies count. With unit_id: returns that
    unit and its direct children.
    """
    db = _db(db)

    def _annotate(u: dict) -> dict:
        # leader = highest-grade active employee in this unit
        leaders = list(db.query("""
            SELECT emp_id, full_name, position_id, grade_level
            FROM employees WHERE unit_id = :u AND status='active'
            ORDER BY grade_level DESC, full_name LIMIT 1
        """, {"u": u["unit_id"]}))
        head = list(db.query("""
            SELECT COUNT(*) c FROM employees
            WHERE unit_id = :u AND status='active'
        """, {"u": u["unit_id"]}))
        vacs = list(db.query("""
            SELECT COUNT(*) c FROM vacancies
            WHERE unit_id = :u AND status IN ('active', 'in_review')
        """, {"u": u["unit_id"]}))
        return {
            **u,
            "headcount":      head[0]["c"] if head else 0,
            "open_vacancies": vacs[0]["c"] if vacs else 0,
            "leader":         leaders[0] if leaders else None,
        }

    if unit_id is None:
        roots = list(db.query("SELECT * FROM units WHERE level=0"))
        if not roots:
            return {"root": None, "children": []}
        root = roots[0]
        children = list(db.query(
            "SELECT * FROM units WHERE parent_unit_id = :p ORDER BY name",
            {"p": root["unit_id"]},
        ))
        return {
            "root":     _annotate(root),
            "children": [_annotate(c) for c in children],
        }
    rows = list(db.query("SELECT * FROM units WHERE unit_id = :u", {"u": unit_id}))
    if not rows:
        return {"root": None, "children": []}
    children = list(db.query(
        "SELECT * FROM units WHERE parent_unit_id = :p ORDER BY name",
        {"p": unit_id},
    ))
    return {
        "root":     _annotate(rows[0]),
        "children": [_annotate(c) for c in children],
    }


# ===========================================================================
# Docs (КЭДО) — slide 13: Работа и отдых, документооборот
# ===========================================================================

def list_my_hr_requests(emp_id: str, *,
                          db: Database | None = None) -> list[dict[str, Any]]:
    """All HR requests for one employee, newest first."""
    db = _db(db)
    return list(db.query("""
        SELECT * FROM hr_requests WHERE emp_id = :e
        ORDER BY submitted_date DESC
    """, {"e": emp_id}))


def get_team_calendar(manager_emp_id: str, year: int, month: int, *,
                        db: Database | None = None) -> dict[str, Any]:
    """Vacation/sick grid for the manager's direct subordinates over one
    month. Each subordinate gets their `vacations` rows that overlap the
    month window.
    """
    db = _db(db)
    mgrs = list(db.query("SELECT * FROM employees WHERE emp_id = :e", {"e": manager_emp_id}))
    if not mgrs:
        return {"month_start": None, "month_end": None, "team": []}
    mgr = mgrs[0]
    month_start = date(year, month, 1)
    month_end = (date(year + (1 if month == 12 else 0),
                      1 if month == 12 else month + 1,
                      1) - timedelta(days=1))

    subs = list(db.query("""
        SELECT emp_id, full_name FROM employees
        WHERE unit_id = :u AND grade_level < :gl AND status='active'
        ORDER BY full_name
    """, {"u": mgr["unit_id"], "gl": mgr["grade_level"]}))

    team: list[dict[str, Any]] = []
    for s in subs:
        vacs = list(db.query("""
            SELECT kind, start_date, end_date FROM vacations
            WHERE emp_id = :e
              AND (start_date <= :me AND end_date >= :ms)
            ORDER BY start_date
        """, {"e": s["emp_id"], "me": month_end.isoformat(), "ms": month_start.isoformat()}))
        team.append({**s, "vacations": vacs})

    return {
        "month_start": month_start.isoformat(),
        "month_end":   month_end.isoformat(),
        "manager":     mgr,
        "team":        team,
    }


def get_request_catalog() -> list[dict[str, Any]]:
    """Static catalog mirroring slide 13 right panel («каталог обращений»).

    Pure data; no DB read. The UI shows these as clickable items that
    open `Спросите Пульса` overlay with a pre-filled prompt.
    """
    return [
        {"key": "employer_request",
          "title": "обращение к работодателю",
          "subtitle": "по любому вопросу"},
        {"key": "salary_certificate",
          "title": "справки с места работы",
          "subtitle": "для визы, для суда, реквизиты, должность"},
        {"key": "payroll",
          "title": "заявление о счёте для перечисления зарплаты и прочих выплат",
          "subtitle": "зарплата, прочие выплаты"},
        {"key": "2-NDFL",
          "title": "справка 2-НДФЛ",
          "subtitle": "кредит, доход, вычет"},
        {"key": "work_book_copy",
          "title": "копия трудовой книжки",
          "subtitle": "стаж"},
        {"key": "other",
          "title": "вопрос о выплатах",
          "subtitle": "зарплата, прочие выплаты"},
    ]


# ===========================================================================
# Analytics overview — slide 10 right + slide 14 (Сквозная HR-аналитика)
# ===========================================================================

def get_hr_analytics_overview(*, window: int = 30,
                                 db: Database | None = None) -> dict[str, Any]:
    """Light-weight overview to back the analytics tab landing card.

    The full CEO dashboard lives in pulse/dashboard.py and is reused via
    iframe in Phase H. Here we only return a few extra HR-specific
    counters that complement the dashboard ones.
    """
    db = _db(db)
    today = _today_iso()
    cutoff = (date.fromisoformat(today) - timedelta(days=window)).isoformat()

    headcount = list(db.query(
        "SELECT COUNT(*) c FROM employees WHERE status='active'"
    ))
    terminated = list(db.query(
        "SELECT COUNT(*) c FROM employees WHERE status='terminated' AND term_date >= :c",
        {"c": cutoff},
    ))
    vacancies_open = list(db.query(
        "SELECT COUNT(*) c FROM vacancies WHERE status IN ('active', 'in_review')"
    ))
    completed_courses = list(db.query("""
        SELECT COUNT(*) c FROM course_enrollments
        WHERE status='completed' AND end_date >= :c
    """, {"c": cutoff}))
    surveys_active = list(db.query(
        "SELECT COUNT(*) c FROM surveys_meta WHERE status='active'"
    ))
    pending_requests = list(db.query(
        "SELECT COUNT(*) c FROM hr_requests WHERE status IN ('open', 'processing')"
    ))

    return {
        "window_days":       window,
        "headcount_active":  headcount[0]["c"] if headcount else 0,
        "terminations":      terminated[0]["c"] if terminated else 0,
        "vacancies_open":    vacancies_open[0]["c"] if vacancies_open else 0,
        "courses_completed": completed_courses[0]["c"] if completed_courses else 0,
        "surveys_active":    surveys_active[0]["c"] if surveys_active else 0,
        "pending_requests":  pending_requests[0]["c"] if pending_requests else 0,
    }


# ===========================================================================
# Comms — slide 2 module 6: Корпоративные коммуникации (light stub)
# ===========================================================================

def get_upcoming_events(*, n: int = 8,
                         db: Database | None = None) -> list[dict[str, Any]]:
    """Last {n} corp events (past + upcoming) with participation count.

    Reuses existing corp_events / event_participation tables — no new
    schema. The Phase H3 panel uses this as a simple feed.
    """
    db = _db(db)
    rows = list(db.query("""
        SELECT ce.*, COUNT(ep.id) AS participants
        FROM corp_events ce
        LEFT JOIN event_participation ep ON ep.event_id = ce.event_id
        GROUP BY ce.event_id
        ORDER BY ce.date DESC
        LIMIT :n
    """, {"n": max(1, min(50, n))}))
    return rows


__all__ = [
    # D1
    "get_recruit_summary", "list_active_vacancies", "get_vacancy_detail",
    "get_goals_summary", "list_my_goals", "list_team_goals",
    "get_learning_feed", "get_my_courses",
    "get_assessment_campaigns", "get_my_assessment",
    # D2
    "get_my_career", "list_internal_vacancies",
    "list_talent_search_results", "list_delegations",
    "get_profile_full", "get_org_structure",
    "list_my_hr_requests", "get_team_calendar", "get_request_catalog",
    "get_hr_analytics_overview",
    # H3
    "get_upcoming_events",
]
