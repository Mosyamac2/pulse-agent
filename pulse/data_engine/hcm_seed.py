"""Synthetic data generators for the HCM façade tables (P14, v2.0.0+).

These are the empirical fillers behind the Pulse-HCM tabs:
vacancies/candidates (recruit), goals/key_results, learning_feed,
talent_pool_status, delegations, hr_requests, surveys_meta.

Design rules (see HROBOROS_HCM_FACADE_PLAN §2):
1. Single RNG seed — accept the same `rng` already in use in seed.py(42).
2. Foreign keys ONLY to existing rows (employees, units, positions, courses).
3. Archetype-driven distributions, so ML / dashboard signals stay consistent.
4. Time window — all dates fit `[start, end]` from seed.py.

Phase split:
* Phase C1 — gen_vacancies + gen_candidates (recruit module).
* Phase C2 — goals, key_results, learning_feed, talent_pool_status,
              delegations, hr_requests, surveys_meta.

Public surface (added incrementally per phase):
* `gen_vacancies(rng, employees, positions, units, end_date) -> list[dict]`
* `gen_candidates(rng, vacancies, employees) -> list[dict]`
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable

import numpy as np
from faker import Faker


# ---------------------------------------------------------------------------
# helpers (keep this module standalone — don't import from seed.py to avoid
# circular imports; small bits of date logic are duplicated on purpose).
# ---------------------------------------------------------------------------

def _iso(d: date) -> str:
    return d.isoformat()


def _active_managers(employees: list[dict[str, Any]], min_grade: int = 3) -> list[dict[str, Any]]:
    return [e for e in employees if e.get("status") == "active" and e.get("grade_level", 0) >= min_grade]


def _active_employees(employees: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in employees if e.get("status") == "active"]


def _recruiter_pool(rng: np.random.Generator, employees: list[dict[str, Any]],
                     positions: list[dict[str, Any]], k: int = 4) -> list[dict[str, Any]]:
    """Pick a small deterministic recruiter pool.

    Synthetic schema has no dedicated HR unit — instead we lift `k` active
    employees of position type=support as our standing recruiter bench.
    Chosen by sorted emp_id so the pool is reproducible across seed runs.
    """
    support_pos_ids = {p["position_id"] for p in positions if p.get("type") == "support"}
    pool = [e for e in employees
            if e.get("status") == "active" and e.get("position_id") in support_pos_ids]
    pool.sort(key=lambda e: e["emp_id"])
    if len(pool) >= k:
        return pool[:k]
    # fallback: any active grade>=2
    extra = [e for e in employees
             if e.get("status") == "active" and e.get("grade_level", 0) >= 2 and e not in pool]
    extra.sort(key=lambda e: e["emp_id"])
    return (pool + extra)[:k]


# ---------------------------------------------------------------------------
# Phase C1: gen_vacancies
# ---------------------------------------------------------------------------

# Status distribution targets — sums to ~22 typical vacancies. Drawn so the
# Подбор panel feels lived-in: a handful of in-flight, plenty of historical
# closed, a few dormant.
_STATUS_TARGETS: tuple[tuple[str, int], ...] = (
    ("active", 5),
    ("in_review", 3),
    ("paused", 2),
    ("closed", 10),
    ("draft", 3),
)


def _vacancy_titles_pool() -> list[tuple[str, str, int]]:
    """(title, type, target_grade_min) — drawn without replacement up to vacancy count."""
    return [
        ("Старший аналитик", "business", 3),
        ("Руководитель департамента", "business", 4),
        ("Секретарь", "business", 1),
        ("Бэкенд-разработчик Java", "technical", 2),
        ("Senior Python/Data engineer", "technical", 3),
        ("DevOps-инженер", "technical", 2),
        ("Тимлид группы разработки", "technical", 4),
        ("Менеджер по корпоративным продажам", "business", 2),
        ("Кредитный аналитик", "business", 2),
        ("Специалист клиринга", "business", 1),
        ("Специалист информационной безопасности", "technical", 3),
        ("Архитектор данных", "technical", 4),
        ("Финансовый контролёр", "business", 3),
        ("Бизнес-аналитик BI", "business", 2),
        ("Специалист по работе с клиентами розницы", "business", 1),
        ("Руководитель направления операций", "business", 4),
        ("Junior разработчик", "technical", 1),
        ("Скрам-мастер", "business", 2),
        ("Старший специалист", "business", 2),
        ("Ведущий разработчик платформы данных", "technical", 4),
        ("Менеджер по продажам", "business", 2),
        ("Разработчик мобильных приложений", "technical", 2),
        ("Аналитик кредитных рисков", "business", 3),
        ("Специалист по тестированию", "technical", 2),
        ("Секретарь руководителя", "business", 1),
        ("Project manager", "business", 3),
    ]


def gen_vacancies(rng: np.random.Generator,
                   employees: list[dict[str, Any]],
                   positions: list[dict[str, Any]],
                   units: list[dict[str, Any]],
                   end_date: date) -> list[dict[str, Any]]:
    """Generate ~22 vacancies with realistic status distribution and dates.

    Hiring manager: random active employee with grade_level>=3.
    Recruiter: from a deterministic 4-person support-type pool. For
    draft and in_review vacancies we leave recruiter NULL ~50% of the time
    to mirror the «без рекрутера» tab on the Подбор screen.
    """
    managers = _active_managers(employees, min_grade=3)
    recruiters = _recruiter_pool(rng, employees, positions, k=4)
    if not managers or not recruiters or not positions or not units:
        return []

    pos_by_id = {p["position_id"]: p for p in positions}
    unit_by_id = {u["unit_id"]: u for u in units}
    pos_grade_index: dict[int, list[dict[str, Any]]] = {}
    for p in positions:
        pos_grade_index.setdefault(int(p.get("grade_level", 1)), []).append(p)

    title_pool = _vacancy_titles_pool()
    rng.shuffle(title_pool)

    out: list[dict[str, Any]] = []
    n = 0
    for status, count in _STATUS_TARGETS:
        for _ in range(count):
            title, vtype, min_grade = title_pool[n % len(title_pool)]
            n += 1

            # Pick a position whose grade_level matches min_grade, or fall back.
            grade_keys = sorted(pos_grade_index.keys())
            chosen_grade = min_grade if min_grade in pos_grade_index else min(grade_keys, key=lambda g: abs(g - min_grade))
            pos = pos_grade_index[chosen_grade][int(rng.integers(0, len(pos_grade_index[chosen_grade])))]
            unit = units[int(rng.integers(0, len(units)))]

            # Manager: prefer one whose grade >= min_grade; else any manager.
            mgr_pool = [m for m in managers if int(m.get("grade_level", 0)) >= min_grade] or managers
            mgr = mgr_pool[int(rng.integers(0, len(mgr_pool)))]

            # Recruiter assignment: draft/in_review can be unassigned (NULL).
            if status in ("draft", "in_review") and rng.random() < 0.5:
                recruiter_id: str | None = None
            else:
                recruiter_id = recruiters[int(rng.integers(0, len(recruiters)))]["emp_id"]

            is_internal_only = 1 if rng.random() < 0.30 else 0

            # Dates by status.
            if status == "closed":
                # closed in the last 90 days; opened 60-150 days before close
                close_offset = int(rng.integers(0, 90))
                closed = end_date - timedelta(days=close_offset)
                opened = closed - timedelta(days=int(rng.integers(60, 150)))
                target_close = opened + timedelta(days=int(rng.integers(45, 90)))
                closed_date: str | None = _iso(closed)
            elif status == "paused":
                opened = end_date - timedelta(days=int(rng.integers(120, 240)))
                target_close = opened + timedelta(days=int(rng.integers(60, 90)))
                closed_date = None
            elif status == "active":
                opened = end_date - timedelta(days=int(rng.integers(7, 90)))
                target_close = opened + timedelta(days=int(rng.integers(45, 90)))
                closed_date = None
            elif status == "in_review":
                opened = end_date - timedelta(days=int(rng.integers(2, 30)))
                target_close = opened + timedelta(days=int(rng.integers(45, 75)))
                closed_date = None
            else:  # draft
                opened = end_date - timedelta(days=int(rng.integers(0, 14)))
                target_close = opened + timedelta(days=int(rng.integers(60, 90)))
                closed_date = None

            vid = f"vac_{1000 + len(out):06d}"
            out.append({
                "vacancy_id": vid,
                "title": title,
                "position_id": pos["position_id"],
                "unit_id": unit["unit_id"],
                "hiring_manager_id": mgr["emp_id"],
                "recruiter_id": recruiter_id,
                "type": vtype,
                "status": status,
                "is_internal_only": is_internal_only,
                "opened_date": _iso(opened),
                "target_close_date": _iso(target_close),
                "closed_date": closed_date,
                "description": f"Поиск {title.lower()} в подразделении «{unit_by_id[unit['unit_id']]['name']}»; грейд {chosen_grade}.",
            })
    return out


# ---------------------------------------------------------------------------
# Phase C1: gen_candidates
# ---------------------------------------------------------------------------

# Funnel-stage profile per vacancy status. Sum need not be 1; we use these as
# weights for a multinomial draw of candidates per vacancy.
_FUNNEL_BY_STATUS: dict[str, dict[str, float]] = {
    # active vacancies: pipeline distributed across stages, no hires yet
    "active":    {"applied": 0.32, "screening": 0.22, "tech": 0.18, "manager": 0.14,
                  "offer": 0.08, "rejected": 0.06, "hired": 0.0},
    # in_review: mostly applied/screening, no later stages yet
    "in_review": {"applied": 0.55, "screening": 0.30, "tech": 0.10, "manager": 0.05,
                  "offer": 0.0, "rejected": 0.0, "hired": 0.0},
    # paused: frozen pipeline
    "paused":    {"applied": 0.40, "screening": 0.25, "tech": 0.20, "manager": 0.10,
                  "offer": 0.05, "rejected": 0.0, "hired": 0.0},
    # closed: clear winner + rejected losers (most common); occasionally
    # closed-without-hire (handled inline for ~15% of closed vacancies)
    "closed":    {"applied": 0.0,  "screening": 0.0,  "tech": 0.05, "manager": 0.05,
                  "offer": 0.0,    "rejected": 0.85, "hired": 0.05},
}


def _candidates_count_for(status: str, rng: np.random.Generator) -> int:
    if status == "draft":
        return 0
    if status == "active":
        return int(rng.integers(2, 9))   # 2..8
    if status == "in_review":
        return int(rng.integers(2, 6))   # 2..5
    if status == "paused":
        return int(rng.integers(1, 4))   # 1..3
    if status == "closed":
        return int(rng.integers(4, 13))  # 4..12
    return 0


def gen_candidates(rng: np.random.Generator,
                    vacancies: list[dict[str, Any]],
                    employees: list[dict[str, Any]],
                    locale: str = "ru_RU") -> list[dict[str, Any]]:
    """Generate candidates with funnel-stage distribution shaped by vacancy status.

    For closed vacancies we either emit exactly one `hired` (≈85% of cases)
    or zero `hired` with all `rejected` (≈15%, real "closed-without-hire").
    `internal_emp_id` is only set for source=internal candidates and points
    to a real active employee not currently the hiring manager of the same
    vacancy.
    """
    fake = Faker(locale)
    Faker.seed(int(rng.integers(0, 2**31 - 1)))

    actives = _active_employees(employees)
    actives_by_id = {e["emp_id"]: e for e in actives}

    out: list[dict[str, Any]] = []
    next_id = 0

    for v in vacancies:
        status = v["status"]
        n_candidates = _candidates_count_for(status, rng)
        if n_candidates == 0:
            continue

        # Decide whether closed vacancy ended with a hire or without one.
        closed_without_hire = (status == "closed" and rng.random() < 0.15)

        # Pre-pick stages for this vacancy.
        weights = _FUNNEL_BY_STATUS[status]
        stages = list(weights.keys())
        probs = np.array([weights[s] for s in stages], dtype=float)
        probs = probs / probs.sum()

        opened = date.fromisoformat(v["opened_date"])
        anchor_end = (date.fromisoformat(v["closed_date"])
                       if v.get("closed_date") else None)

        forced_hired_emitted = False
        for i in range(n_candidates):
            stage = str(rng.choice(stages, p=probs))

            if status == "closed":
                # First slot becomes "hired" unless this is closed-without-hire.
                if i == 0 and not closed_without_hire and not forced_hired_emitted:
                    stage = "hired"
                    forced_hired_emitted = True
                else:
                    stage = "rejected"

            # Source mix.
            r = rng.random()
            if r < 0.30:
                source = "internal"
            elif r < 0.65:
                source = "hh"
            elif r < 0.85:
                source = "external_referral"
            else:
                source = "job_board"

            internal_emp_id: str | None = None
            full_name: str
            if source == "internal":
                # pick an active emp not the hiring manager of this vacancy
                pool_ids = [eid for eid in actives_by_id
                             if eid != v["hiring_manager_id"]]
                if pool_ids:
                    internal_emp_id = pool_ids[int(rng.integers(0, len(pool_ids)))]
                    full_name = actives_by_id[internal_emp_id]["full_name"]
                else:
                    source = "hh"  # degrade gracefully
                    full_name = fake.name()
            else:
                full_name = fake.name()

            # Application date — within [opened, end] for open vacancies, or
            # within [opened, closed - small_gap] for closed ones.
            window_end = anchor_end if anchor_end else opened + timedelta(days=90)
            if window_end <= opened:
                window_end = opened + timedelta(days=1)
            applied_offset = int(rng.integers(0, max(1, (window_end - opened).days)))
            applied = opened + timedelta(days=applied_offset)
            stage_offset = int(rng.integers(1, 30)) if stage not in ("applied",) else 0
            stage_updated = min(applied + timedelta(days=stage_offset),
                                 anchor_end if anchor_end else (window_end))

            # Score: hired gets the top tail, rejected gets the bottom tail.
            if stage == "hired":
                score = 75.0 + rng.beta(2.0, 1.0) * 25.0
            elif stage == "rejected":
                score = rng.beta(1.5, 4.0) * 60.0
            else:
                score = 35.0 + rng.beta(2.0, 2.0) * 50.0

            cid = f"cand_{500 + next_id:06d}"
            next_id += 1
            out.append({
                "candidate_id": cid,
                "vacancy_id": v["vacancy_id"],
                "full_name": full_name,
                "source": source,
                "internal_emp_id": internal_emp_id,
                "funnel_stage": stage,
                "applied_date": _iso(applied),
                "stage_updated_date": _iso(stage_updated),
                "score": float(round(score, 1)),
            })

    return out


__all__ = ["gen_vacancies", "gen_candidates"]
