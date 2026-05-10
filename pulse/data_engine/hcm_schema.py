"""Schema extension for HCM façade tables (P14, v2.0.0+).

These tables are NOT in the immune core. They can be added/altered through
the evolution loop because they hold no truth that the agent depends on for
self-modification — they are presentation-layer data for the Pulse-HCM tabs
(see web/app.html and pulse/hcm_panels.py).

Foreign keys reference tables in pulse.data_engine.schema (employees, units,
positions, courses). All tables idempotent via if_not_exists=True.

Tables added:
  - vacancies, candidates                     (Phase 1: Подбор и адаптация)
  - goals, key_results                        (Phase 2: Цели и задачи)
  - learning_feed                             (Phase 3: Обучение и развитие)
  - talent_pool_status                        (Phase 5: Карьерное продвижение)
  - delegations                               (Phase 5: Делегирования)
  - hr_requests                               (Phase 8: КЭДО)
  - surveys_meta                              (Phase 4: Оценочные кампании)

Phase 7 (HR-аналитика) reuses pulse/dashboard.py — no new tables.
Phase 6 (Корпоративные коммуникации) reuses corp_events / event_participation.
"""
from __future__ import annotations

from sqlite_utils import Database


def create_hcm_tables(db: Database) -> None:
    """Create all HCM façade tables. Idempotent."""

    # --- 1. Подбор и адаптация ---
    db["vacancies"].create({
        "vacancy_id": str,            # vac_NNNNNN
        "title": str,
        "position_id": str,           # FK -> positions
        "unit_id": str,               # FK -> units
        "hiring_manager_id": str,     # FK -> employees
        "recruiter_id": str,          # FK -> employees, nullable for draft/in_review
        "type": str,                  # technical / business
        "status": str,                # draft / in_review / active / paused / closed
        "is_internal_only": int,      # 0/1
        "opened_date": str,           # ISO date
        "target_close_date": str,
        "closed_date": str,           # nullable
        "description": str,
    }, pk="vacancy_id", if_not_exists=True)
    db["vacancies"].create_index(["status"], if_not_exists=True)
    db["vacancies"].create_index(["unit_id"], if_not_exists=True)

    db["candidates"].create({
        "candidate_id": str,          # cand_NNNNNN
        "vacancy_id": str,            # FK -> vacancies
        "full_name": str,
        "source": str,                # internal / hh / external_referral / job_board
        "internal_emp_id": str,       # FK -> employees, nullable
        "funnel_stage": str,          # applied / screening / tech / manager / offer / hired / rejected
        "applied_date": str,
        "stage_updated_date": str,
        "score": float,               # 0..100 — composite recruiter score
    }, pk="candidate_id", if_not_exists=True)
    db["candidates"].create_index(["vacancy_id"], if_not_exists=True)
    db["candidates"].create_index(["funnel_stage"], if_not_exists=True)

    # --- 2. Цели и задачи ---
    db["goals"].create({
        "goal_id": str,               # goal_NNNNNN
        "emp_id": str,                # FK -> employees
        "title": str,
        "description": str,
        "period": str,                # 2025-Q3 / 2026-Q1 / 2026-Y
        "parent_goal_id": str,        # nullable, FK -> goals (for cascade)
        "weight": float,              # 0..1; sum per (emp, period) ≈ 1.0
        "status": str,                # draft / proposed / accepted / in_progress / done / cancelled
        "progress_pct": float,        # 0..1
        "created_date": str,
        "due_date": str,
        "completed_date": str,        # nullable
    }, pk="goal_id", if_not_exists=True)
    db["goals"].create_index(["emp_id", "period"], if_not_exists=True)
    db["goals"].create_index(["status"], if_not_exists=True)

    db["key_results"].create({
        "kr_id": str,                 # kr_NNNNNN
        "goal_id": str,               # FK -> goals
        "title": str,
        "metric_unit": str,           # %, шт, ₽, дни
        "target_value": float,
        "current_value": float,
        "due_date": str,
        "is_completed": int,          # 0/1
    }, pk="kr_id", if_not_exists=True)
    db["key_results"].create_index(["goal_id"], if_not_exists=True)

    # --- 3. Обучение и развитие — лента рекомендаций ---
    db["learning_feed"].create({
        "feed_id": int,
        "emp_id": str,                # FK -> employees
        "content_type": str,          # course / article / video / audio
        "title": str,
        "source": str,                # Хабр / YouTube / internal / external
        "course_id": str,             # nullable, FK -> courses
        "recommended_reason": str,    # peer_completed / position_match / skill_gap / manager_assigned / similar_interests
        "recommended_date": str,
        "viewed": int,                # 0/1
        "bookmarked": int,            # 0/1
        "shared_with_count": int,     # 0..N
    }, pk="feed_id", if_not_exists=True)
    db["learning_feed"].create_index(["emp_id"], if_not_exists=True)
    db["learning_feed"].create_index(["recommended_date"], if_not_exists=True)

    # --- 4. Talent pool / карьерный статус ---
    db["talent_pool_status"].create({
        "emp_id": str,                       # PK + FK -> employees
        "open_to_offers": int,               # 0/1 — «открыт для предложений»
        "open_to_offers_date": str,          # nullable
        "recommended_by_count": int,         # сколько коллег пометили «рекомендую в свою команду»
        "last_recommended_date": str,        # nullable
        "career_track_preference": str,      # vertical / horizontal / hybrid / none
    }, pk="emp_id", if_not_exists=True)

    # --- 5. Делегирования ---
    db["delegations"].create({
        "delegation_id": str,         # del_NNNNNN
        "from_emp_id": str,           # FK -> employees
        "to_emp_id": str,             # FK -> employees
        "title": str,
        "scope_summary": str,         # «часть функций» / «полные полномочия» / «одно направление» / «проектные полномочия»
        "start_date": str,
        "end_date": str,              # nullable — бессрочное
        "status": str,                # active / completed / cancelled
    }, pk="delegation_id", if_not_exists=True)
    db["delegations"].create_index(["from_emp_id"], if_not_exists=True)
    db["delegations"].create_index(["to_emp_id"], if_not_exists=True)

    # --- 6. КЭДО — справки и обращения ---
    db["hr_requests"].create({
        "request_id": str,            # req_NNNNNN
        "emp_id": str,                # FK -> employees
        "type": str,                  # employer_request / 2-NDFL / work_book_copy / payroll / salary_certificate / other
        "submitted_date": str,
        "status": str,                # open / processing / done / cancelled
        "due_date": str,
        "completed_date": str,        # nullable
        "subject": str,
        "details": str,
    }, pk="request_id", if_not_exists=True)
    db["hr_requests"].create_index(["emp_id"], if_not_exists=True)
    db["hr_requests"].create_index(["status"], if_not_exists=True)

    # --- 7. Опросы / 360 кампании ---
    db["surveys_meta"].create({
        "survey_id": str,             # survey_NNNNNN
        "name": str,
        "kind": str,                  # оценка / опрос / 360
        "status": str,                # active / completed
        "launched_date": str,
        "ends_date": str,             # nullable
        "target_count": int,          # сколько участников приглашено
        "completed_count": int,       # сколько прошли
        "is_important": int,          # 0/1 — красная плашка «важно»
    }, pk="survey_id", if_not_exists=True)


__all__ = ["create_hcm_tables"]
