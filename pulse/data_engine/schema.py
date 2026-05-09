"""SQLite schema for the synthetic HR sandbox.

Single source of truth for all tables. Created via sqlite-utils for brevity:
column types are passed as Python types; PKs and indexes set explicitly.

Reference dates inside the DB are stored as ISO strings (YYYY-MM-DD) — not as
Date objects — so reads from sqlite stay trivial. Datetimes stored as ISO
seconds (YYYY-MM-DDTHH:MM:SS).

Protected file: do not modify in evolution mode (v0.1).
"""
from __future__ import annotations

from sqlite_utils import Database


def create_tables(db: Database) -> None:
    """Create all tables. Idempotent."""

    # --- Org structure ---
    db["units"].create(
        {
            "unit_id": str,
            "name": str,
            "parent_unit_id": str,
            "level": int,
            "processes_json": str,
        },
        pk="unit_id",
        if_not_exists=True,
    )

    db["positions"].create(
        {
            "position_id": str,
            "title": str,
            "type": str,        # IT / sales / analytics / ops / support
            "grade_level": int, # 1..5
            "core_skills_json": str,
        },
        pk="position_id",
        if_not_exists=True,
    )

    # --- Core employee tables ---
    db["employees"].create(
        {
            "emp_id": str,
            "full_name": str,
            "gender": str,        # M / F
            "birth_date": str,
            "city": str,
            "education": str,
            "language_skills_json": str,
            "hire_date": str,
            "term_date": str,     # ISO date or NULL
            "status": str,        # active / terminated / maternity / long_sick
            "grade_level": int,
            "position_id": str,
            "unit_id": str,
            "archetype": str,
        },
        pk="emp_id",
        if_not_exists=True,
    )
    db["employees"].create_index(["unit_id"], if_not_exists=True)
    db["employees"].create_index(["status"], if_not_exists=True)
    db["employees"].create_index(["archetype"], if_not_exists=True)

    db["family"].create(
        {
            "emp_id": str,
            "marital_status": str,
            "kids_count": int,
            "spouse_works_in_company": int,
        },
        pk="emp_id",
        if_not_exists=True,
    )

    db["career_history"].create(
        {
            "id": int,
            "emp_id": str,
            "position_id": str,
            "unit_id": str,
            "start_date": str,
            "end_date": str,  # NULL if current
            "company": str,   # 'Сбер' or external
        },
        pk="id",
        if_not_exists=True,
    )
    db["career_history"].create_index(["emp_id"], if_not_exists=True)

    db["performance_reviews"].create(
        {
            "id": int,
            "emp_id": str,
            "period": str,    # e.g. 2025H1
            "score": float,   # 1..5
            "reviewer_id": str,
            "comment_summary": str,
        },
        pk="id",
        if_not_exists=True,
    )
    db["performance_reviews"].create_index(["emp_id"], if_not_exists=True)
    db["performance_reviews"].create_index(["period"], if_not_exists=True)

    db["promotions"].create(
        {
            "id": int,
            "emp_id": str,
            "date": str,
            "from_grade": int,
            "to_grade": int,
            "from_position_id": str,
            "to_position_id": str,
        },
        pk="id",
        if_not_exists=True,
    )
    db["promotions"].create_index(["emp_id"], if_not_exists=True)

    db["peer_feedback"].create(
        {
            "id": int,
            "emp_id": str,           # subject
            "from_id": str,          # author
            "ts": str,
            "sentiment_score": float,  # -1..+1
            "category": str,           # cooperation / quality / reliability / leadership
            "text_summary": str,
        },
        pk="id",
        if_not_exists=True,
    )
    db["peer_feedback"].create_index(["emp_id"], if_not_exists=True)

    # --- Daily metrics ---
    db["activity_daily"].create(
        {
            "id": int,
            "emp_id": str,
            "date": str,
            "tasks_done": int,
            "hours_logged": float,
            "meetings_count": int,
            "is_weekend": int,
        },
        pk="id",
        if_not_exists=True,
    )
    db["activity_daily"].create_index(["emp_id", "date"], unique=True, if_not_exists=True)
    db["activity_daily"].create_index(["date"], if_not_exists=True)

    db["digital_patterns_daily"].create(
        {
            "id": int,
            "emp_id": str,
            "date": str,
            "focus_score": float,         # 0..1
            "switches_per_min": float,    # 0..N
            "working_hours": float,       # actual hours active
        },
        pk="id",
        if_not_exists=True,
    )
    db["digital_patterns_daily"].create_index(["emp_id", "date"], unique=True, if_not_exists=True)

    db["wearables_daily"].create(
        {
            "id": int,
            "emp_id": str,
            "date": str,
            "steps": int,
            "sleep_h": float,
            "stress_index": float,        # 0..1
            "hr_avg": float,
        },
        pk="id",
        if_not_exists=True,
    )
    db["wearables_daily"].create_index(["emp_id", "date"], unique=True, if_not_exists=True)

    # --- Graph & similarity ---
    db["collab_edges"].create(
        {
            "id": int,
            "emp_a": str,
            "emp_b": str,
            "weight": float,
            "last_interact_ts": str,
        },
        pk="id",
        if_not_exists=True,
    )
    db["collab_edges"].create_index(["emp_a"], if_not_exists=True)
    db["collab_edges"].create_index(["emp_b"], if_not_exists=True)

    db["similarity_to_unit"].create(
        {
            "id": int,
            "emp_id": str,
            "unit_id": str,
            "cosine": float,
            "attr_diff_json": str,
        },
        pk="id",
        if_not_exists=True,
    )
    db["similarity_to_unit"].create_index(["emp_id"], if_not_exists=True)

    # --- Courses & enrollments ---
    db["courses"].create(
        {
            "course_id": str,
            "title": str,
            "topic": str,         # leadership / hard_skill / soft_skill / compliance / banking
            "duration_h": float,
            "level": int,
        },
        pk="course_id",
        if_not_exists=True,
    )

    db["course_enrollments"].create(
        {
            "id": int,
            "emp_id": str,
            "course_id": str,
            "status": str,         # completed / in_progress / dropped
            "start_date": str,
            "end_date": str,
            "score": float,
        },
        pk="id",
        if_not_exists=True,
    )
    db["course_enrollments"].create_index(["emp_id"], if_not_exists=True)

    # --- Assessments (psych, SberQ, 360, computed) ---
    db["assessments"].create(
        {
            "id": int,
            "emp_id": str,
            "type": str,           # psych / sberq / 360 / systemic
            "date": str,
            "score": float,
            "details_json": str,
        },
        pk="id",
        if_not_exists=True,
    )
    db["assessments"].create_index(["emp_id"], if_not_exists=True)

    # --- Corp events ---
    db["corp_events"].create(
        {
            "event_id": str,
            "name": str,
            "date": str,
            "kind": str,
        },
        pk="event_id",
        if_not_exists=True,
    )
    db["event_participation"].create(
        {
            "id": int,
            "event_id": str,
            "emp_id": str,
            "role": str,           # invited / attended / declined / speaker
        },
        pk="id",
        if_not_exists=True,
    )
    db["event_participation"].create_index(["emp_id"], if_not_exists=True)

    # --- Vacations ---
    db["vacations"].create(
        {
            "id": int,
            "emp_id": str,
            "kind": str,           # annual / sick / maternity / sick_long
            "start_date": str,
            "end_date": str,
        },
        pk="id",
        if_not_exists=True,
    )
    db["vacations"].create_index(["emp_id"], if_not_exists=True)

    # --- Tools metadata (JIRA, Confluence, Bitbucket) ---
    db["jira_issues"].create(
        {
            "issue_key": str,
            "emp_id": str,
            "status": str,         # open / in_progress / resolved / closed
            "ts_created": str,
            "ts_resolved": str,
            "type": str,           # bug / feature / task
            "priority": str,       # low / med / high / critical
            "summary": str,
        },
        pk="issue_key",
        if_not_exists=True,
    )
    db["jira_issues"].create_index(["emp_id"], if_not_exists=True)

    db["confluence_pages"].create(
        {
            "page_id": str,
            "emp_id": str,
            "ts_created": str,
            "length_chars": int,
            "title": str,
        },
        pk="page_id",
        if_not_exists=True,
    )
    db["confluence_pages"].create_index(["emp_id"], if_not_exists=True)

    db["bitbucket_commits"].create(
        {
            "commit_id": str,
            "emp_id": str,
            "ts": str,
            "lines_changed": int,
            "repo": str,
        },
        pk="commit_id",
        if_not_exists=True,
    )
    db["bitbucket_commits"].create_index(["emp_id"], if_not_exists=True)

    db["branch_tasks"].create(
        {
            "id": int,
            "emp_id": str,
            "ts": str,
            "kind": str,           # client_consult / cash_op / loan_application
        },
        pk="id",
        if_not_exists=True,
    )
    db["branch_tasks"].create_index(["emp_id"], if_not_exists=True)

    # --- Communication style + SLA ---
    db["comm_style"].create(
        {
            "emp_id": str,
            "avg_length_chars": float,
            "formality_score": float,    # 0..1
            "response_speed_h": float,
            "sla_compliance_pct": float, # 0..1
        },
        pk="emp_id",
        if_not_exists=True,
    )

    # --- Meetings & video summaries ---
    db["meeting_artifacts"].create(
        {
            "id": int,
            "emp_id": str,
            "meeting_id": str,
            "ts": str,
            "summary": str,
            "sentiment": float,
        },
        pk="id",
        if_not_exists=True,
    )
    db["meeting_artifacts"].create_index(["emp_id"], if_not_exists=True)

    db["vc_transcripts_summary"].create(
        {
            "id": int,
            "emp_id": str,
            "meeting_id": str,
            "agg_topics_json": str,
            "sentiment": float,
        },
        pk="id",
        if_not_exists=True,
    )

    # --- Finance, investments, lifestyle, mobility, wearables, security ---
    db["finance_health"].create(
        {
            "id": int,
            "emp_id": str,
            "year_month": str,
            "income_band": str,            # low / mid / high (band only — privacy)
            "spending_band": str,
            "savings_ratio": float,
        },
        pk="id",
        if_not_exists=True,
    )
    db["finance_health"].create_index(["emp_id"], if_not_exists=True)

    db["investment_profile"].create(
        {
            "emp_id": str,
            "risk_score": str,             # low / med / high
            "portfolio_size_band": str,
            "horizon_years": int,
        },
        pk="emp_id",
        if_not_exists=True,
    )

    db["lifestyle_signals"].create(
        {
            "id": int,
            "emp_id": str,
            "year_month": str,
            "okko_hours_band": str,
            "samokat_orders_band": str,
            "megamarket_band": str,
        },
        pk="id",
        if_not_exists=True,
    )

    db["security_flags"].create(
        {
            "id": int,
            "emp_id": str,
            "ts": str,
            "kind": str,                  # badge_anomaly / login_anomaly / access_denied
            "severity": str,              # info / warning / critical
        },
        pk="id",
        if_not_exists=True,
    )
    db["security_flags"].create_index(["emp_id"], if_not_exists=True)

    db["mobility"].create(
        {
            "id": int,
            "emp_id": str,
            "year_month": str,
            "trips_count": int,
            "countries_json": str,
        },
        pk="id",
        if_not_exists=True,
    )

    # --- Snapshot of seed config (for diagnostics) ---
    db["seed_meta"].create(
        {
            "key": str,
            "value": str,
        },
        pk="key",
        if_not_exists=True,
    )


__all__ = ["create_tables"]
