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


# ===========================================================================
# Phase C2 — goals, key_results, learning_feed, talent_pool_status,
#            delegations, hr_requests, surveys_meta
# ===========================================================================

# Archetype → behaviour profile for goals.
# (n_goals_min, n_goals_max, p_done, p_in_progress, p_proposed, base_progress, p_overdue)
_ARCHETYPE_GOAL_PROFILE: dict[str, tuple[int, int, float, float, float, float, float]] = {
    "star_perfectionist":   (3, 5, 0.70, 0.25, 0.05, 0.85, 0.05),
    "newbie_enthusiast":    (3, 5, 0.45, 0.45, 0.10, 0.65, 0.10),
    "tired_midfielder":     (3, 6, 0.20, 0.55, 0.10, 0.40, 0.30),  # plan: overdue 20%+
    "quiet_rear_guard":     (3, 4, 0.50, 0.40, 0.05, 0.70, 0.08),
    "drifting_veteran":     (2, 4, 0.20, 0.40, 0.10, 0.35, 0.30),
    "toxic_high_performer": (3, 5, 0.55, 0.35, 0.05, 0.75, 0.10),
    "isolated_newbie":      (1, 2, 0.10, 0.20, 0.65, 0.20, 0.15),  # plan: «1-2 цели, статус proposed»
    "overwhelmed_manager":  (6, 8, 0.20, 0.55, 0.05, 0.40, 0.25),  # plan: «6+ целей, низкий progress»
}

# Periods covered: current (Q2-2026), and two trailing.
_GOAL_PERIODS: tuple[str, ...] = ("2026-Q2", "2026-Q1", "2025-Q4")

_GOAL_TITLES_BY_TYPE: dict[str, list[str]] = {
    "IT": [
        "Сократить время сборки на 30%",
        "Покрыть критичный модуль unit-тестами до 80%",
        "Закрыть 5 P1/P2 багов за квартал",
        "Запустить новую подсистему мониторинга",
        "Стабилизировать SLA сервиса до 99.9%",
        "Подготовить рефакторинг легаси-модуля",
    ],
    "sales": [
        "Выполнить план продаж на 105%",
        "Увеличить количество новых клиентов на 15%",
        "Провести 12 успешных встреч с key accounts",
        "Запустить кросс-продажу нового продукта",
        "Сократить churn по портфелю до 4%",
    ],
    "analytics": [
        "Подготовить квартальный отчёт по эффективности",
        "Внедрить новую витрину для маркетинга",
        "Автоматизировать 3 рутинных отчёта",
        "Провести ad-hoc исследование оттока",
        "Сделать сравнительный анализ продуктов",
    ],
    "ops": [
        "Сократить время обработки заявки на 20%",
        "Внедрить новый стандарт сверок",
        "Закрыть аудит без замечаний",
        "Снизить процент ошибок ввода до 0.5%",
    ],
    "support": [
        "Сократить время первого ответа до 15 мин",
        "Закрыть 95% обращений в SLA",
        "Подготовить новую базу знаний",
        "Провести 4 тренинга для команды",
    ],
}


def _normalize_weights(rng: np.random.Generator, n: int) -> list[float]:
    """Sum to ≈1.0, all >0. Uses a Dirichlet draw for diversity."""
    if n == 0:
        return []
    raw = rng.dirichlet(np.ones(n) * 1.5)
    return [float(round(w, 4)) for w in raw]


def _period_window(period: str, end_date: date) -> tuple[date, date]:
    """Return (start, end) for a period like '2026-Q2'."""
    year = int(period[:4])
    if "-Q" in period:
        q = int(period.split("Q")[-1])
        start_month = {1: 1, 2: 4, 3: 7, 4: 10}[q]
        start = date(year, start_month, 1)
        end_month = start_month + 2
        end = date(year + (1 if end_month > 12 else 0), ((end_month - 1) % 12) + 1, 28)
    else:
        start, end = date(year, 1, 1), date(year, 12, 31)
    return start, min(end, end_date)


def gen_goals(rng: np.random.Generator,
                employees: list[dict[str, Any]],
                end_date: date) -> list[dict[str, Any]]:
    """Generate goals with archetype-driven counts/statuses/progress.

    Per active employee × per period: 2-8 goals (archetype-dependent).
    Weights normalized so sum per (emp, period) ≈ 1.0 (Plan §2 invariant).
    """
    actives = _active_employees(employees)
    if not actives:
        return []
    pos_by_id: dict[str, dict] = {}
    out: list[dict[str, Any]] = []
    next_id = 0

    for emp in actives:
        archetype = emp.get("archetype", "newbie_enthusiast")
        n_min, n_max, p_done, p_in_progress, p_proposed, base_prog, p_overdue = (
            _ARCHETYPE_GOAL_PROFILE.get(archetype, _ARCHETYPE_GOAL_PROFILE["newbie_enthusiast"])
        )

        # Title pool by role type (best-effort): use position_id slug as proxy.
        pos_id = emp.get("position_id", "")
        # Fallback: pick from broad pool. We pre-load the pool union once.
        all_titles: list[str] = []
        for tlist in _GOAL_TITLES_BY_TYPE.values():
            all_titles.extend(tlist)

        for period in _GOAL_PERIODS:
            n_goals = int(rng.integers(n_min, n_max + 1))
            weights = _normalize_weights(rng, n_goals)
            period_start, period_end = _period_window(period, end_date)
            # Status is meaningful only for current/recent period; older periods
            # mostly closed out.
            is_old = period != "2026-Q2"

            for i in range(n_goals):
                r = rng.random()
                if is_old:
                    status = "done" if r < (p_done + 0.30) else "cancelled" if r < 0.95 else "in_progress"
                elif r < p_done:
                    status = "done"
                elif r < p_done + p_in_progress:
                    status = "in_progress"
                elif r < p_done + p_in_progress + p_proposed:
                    status = "proposed"
                else:
                    status = "accepted"

                # Progress varies by status.
                if status == "done":
                    progress = 1.0
                elif status == "cancelled":
                    progress = float(rng.uniform(0.0, 0.5))
                elif status == "proposed":
                    progress = 0.0
                else:
                    progress = float(np.clip(base_prog + rng.normal(0, 0.15), 0.05, 0.95))

                # due_date — within period window
                due_offset = int(rng.integers(0, max(1, (period_end - period_start).days)))
                due_date = period_start + timedelta(days=due_offset)
                # Sometimes overdue (in current period only).
                if not is_old and status == "in_progress" and rng.random() < p_overdue:
                    due_date = end_date - timedelta(days=int(rng.integers(1, 21)))

                created_offset = int(rng.integers(0, 14))
                created = max(period_start, due_date - timedelta(days=int(rng.integers(20, 90))))
                created = min(created, period_start + timedelta(days=created_offset))

                completed = (None if status != "done" else
                              _iso(min(due_date, period_end - timedelta(days=int(rng.integers(0, 14))))))

                title = all_titles[int(rng.integers(0, len(all_titles)))]
                gid = f"goal_{1000 + next_id:06d}"
                next_id += 1
                out.append({
                    "goal_id": gid,
                    "emp_id": emp["emp_id"],
                    "title": title,
                    "description": f"{title} ({period}).",
                    "period": period,
                    "parent_goal_id": None,
                    "weight": weights[i],
                    "status": status,
                    "progress_pct": float(round(progress, 3)),
                    "created_date": _iso(created),
                    "due_date": _iso(due_date),
                    "completed_date": completed,
                })
    return out


def gen_key_results(rng: np.random.Generator,
                     goals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """KRs for ~60% of goals, 1-4 per goal. current/target correlates with progress_pct."""
    out: list[dict[str, Any]] = []
    next_id = 0
    units_pool = ["%", "шт", "дни", "₽", "клиентов"]
    for g in goals:
        if rng.random() > 0.60:
            continue
        n_kr = int(rng.integers(1, 5))
        for i in range(n_kr):
            target = float(rng.choice([10, 25, 50, 100, 200]) * float(rng.uniform(0.8, 1.5)))
            current = round(target * float(np.clip(g["progress_pct"] + rng.normal(0, 0.08), 0.0, 1.05)), 1)
            unit = str(rng.choice(units_pool))
            kr_id = f"kr_{1000 + next_id:06d}"
            next_id += 1
            # KR due — close to goal due.
            due = g["due_date"]
            out.append({
                "kr_id": kr_id,
                "goal_id": g["goal_id"],
                "title": f"KR {i+1}: достичь {round(target, 1)} {unit}",
                "metric_unit": unit,
                "target_value": float(round(target, 1)),
                "current_value": float(current),
                "due_date": due,
                "is_completed": int(current >= target),
            })
    return out


# ----- learning_feed -------------------------------------------------------

_REASONS = ("peer_completed", "position_match", "skill_gap",
              "manager_assigned", "similar_interests")
_SOURCES = ("Хабр", "YouTube", "internal", "external")
_CONTENT_TYPES = ("course", "article", "video", "audio")
_LEARNING_TITLES_TEMPLATE = [
    "Использование Provider/Riverpod в одном простом примере",
    "Технический пресейл: аналитики или Хагги Вагги?",
    "Complete .NET Developer's Guide to async/await",
    "Введение в LLM-агенты для бизнеса",
    "Soft skills для тимлида: 7 техник переговоров",
    "Coursera: Data Storytelling",
    "Финансовое моделирование для CFO",
    "AB-тесты в продуктовой аналитике",
    "Ритейл-банкинг: тренды 2026",
    "Управление проектами: PMBoK 7-е издание",
    "GenAI в HR-процессах: кейсы",
    "Психология продаж: эффективные диалоги",
    "Лидерство в кризис: чек-лист руководителя",
    "Основы платежных систем",
    "Аудит-методология для финансового сектора",
]

# Archetype → (n_min, n_max, p_viewed, p_bookmarked)
_ARCHETYPE_LEARNING: dict[str, tuple[int, int, float, float]] = {
    "star_perfectionist":   (15, 25, 0.80, 0.40),
    "newbie_enthusiast":    (12, 22, 0.70, 0.35),
    "tired_midfielder":     (8, 16, 0.40, 0.15),
    "quiet_rear_guard":     (10, 18, 0.55, 0.25),
    "drifting_veteran":     (6, 12, 0.25, 0.10),
    "toxic_high_performer": (10, 18, 0.60, 0.20),
    "isolated_newbie":      (8, 14, 0.45, 0.20),
    "overwhelmed_manager":  (10, 18, 0.50, 0.25),
}


def gen_learning_feed(rng: np.random.Generator,
                       employees: list[dict[str, Any]],
                       courses: list[dict[str, Any]],
                       end_date: date) -> list[dict[str, Any]]:
    """8-25 feed cards per active employee, last 90 days. Archetype-driven."""
    out: list[dict[str, Any]] = []
    next_id = 0
    course_ids = [c["course_id"] for c in courses]
    actives = _active_employees(employees)
    for emp in actives:
        archetype = emp.get("archetype", "newbie_enthusiast")
        n_min, n_max, p_view, p_book = _ARCHETYPE_LEARNING.get(archetype, _ARCHETYPE_LEARNING["newbie_enthusiast"])
        n = int(rng.integers(n_min, n_max + 1))
        for _ in range(n):
            title = _LEARNING_TITLES_TEMPLATE[int(rng.integers(0, len(_LEARNING_TITLES_TEMPLATE)))]
            ctype = str(rng.choice(_CONTENT_TYPES))
            source = str(rng.choice(_SOURCES))
            course_id = course_ids[int(rng.integers(0, len(course_ids)))] if course_ids and rng.random() < 0.4 else None
            reason = str(rng.choice(_REASONS))
            offset = int(rng.integers(0, 90))
            recd = end_date - timedelta(days=offset)
            viewed = int(rng.random() < p_view)
            bookmarked = int(viewed and rng.random() < p_book)
            shared = int(viewed * (rng.poisson(0.4) if hasattr(rng, "poisson") else int(np.random.default_rng(0).poisson(0.4))))
            out.append({
                "feed_id": next_id,
                "emp_id": emp["emp_id"],
                "content_type": ctype,
                "title": title,
                "source": source,
                "course_id": course_id,
                "recommended_reason": reason,
                "recommended_date": _iso(recd),
                "viewed": viewed,
                "bookmarked": bookmarked,
                "shared_with_count": int(shared),
            })
            next_id += 1
    return out


# ----- talent_pool_status --------------------------------------------------

# Archetype → (p_open_to_offers, recommended_lambda)
_ARCHETYPE_TALENT: dict[str, tuple[float, float]] = {
    "star_perfectionist":   (0.20, 3.5),
    "newbie_enthusiast":    (0.30, 1.0),
    "tired_midfielder":     (0.70, 0.6),  # plan: «70% для tired_midfielder»
    "quiet_rear_guard":     (0.20, 1.4),
    "drifting_veteran":     (0.70, 0.4),  # plan: «70% для drifting_veteran»
    "toxic_high_performer": (0.40, 0.5),
    "isolated_newbie":      (0.40, 0.3),
    "overwhelmed_manager":  (0.25, 2.0),
}


def gen_talent_pool_status(rng: np.random.Generator,
                             employees: list[dict[str, Any]],
                             end_date: date) -> list[dict[str, Any]]:
    """One row per employee. Non-active (terminated/maternity/long_sick)
    are kept in the table with open_to_offers=0 so the panel can still
    surface them in historical search; active rows follow archetype profile.
    """
    out: list[dict[str, Any]] = []
    for emp in employees:
        if emp.get("status") != "active":
            out.append({
                "emp_id": emp["emp_id"],
                "open_to_offers": 0,
                "open_to_offers_date": None,
                "recommended_by_count": 0,
                "last_recommended_date": None,
                "career_track_preference": "none",
            })
            continue
        archetype = emp.get("archetype", "newbie_enthusiast")
        p_open, lam = _ARCHETYPE_TALENT.get(archetype, _ARCHETYPE_TALENT["newbie_enthusiast"])
        open_to = int(rng.random() < p_open)
        open_date = (_iso(end_date - timedelta(days=int(rng.integers(0, 180))))
                      if open_to else None)
        rec_count = int(rng.poisson(lam))
        last_rec = (_iso(end_date - timedelta(days=int(rng.integers(0, 120))))
                     if rec_count > 0 else None)
        track = str(rng.choice(["vertical", "horizontal", "hybrid", "none"],
                                  p=[0.40, 0.25, 0.20, 0.15]))
        out.append({
            "emp_id": emp["emp_id"],
            "open_to_offers": open_to,
            "open_to_offers_date": open_date,
            "recommended_by_count": rec_count,
            "last_recommended_date": last_rec,
            "career_track_preference": track,
        })
    return out


# ----- delegations ---------------------------------------------------------

_SCOPE_SUMMARIES = ("часть функций", "полные полномочия",
                     "одно направление", "проектные полномочия")


def gen_delegations(rng: np.random.Generator,
                     employees: list[dict[str, Any]],
                     end_date: date) -> list[dict[str, Any]]:
    """6-10 active + 8-15 completed delegations.

    from_emp_id = manager (grade>=3); to_emp_id = any active non-self.
    """
    managers = _active_managers(employees, min_grade=3)
    actives = _active_employees(employees)
    if not managers:
        return []

    out: list[dict[str, Any]] = []
    n_active = int(rng.integers(6, 11))
    n_completed = int(rng.integers(8, 16))
    next_id = 0

    titles_pool = [
        "Координация команды разработки",
        "Согласование бюджетов",
        "Проведение еженедельных статусов",
        "Найм по открытым вакансиям",
        "Ведение проекта Х",
        "Контроль исполнения SLA",
        "Подготовка квартального отчёта",
        "Утверждение договоров до Y руб.",
    ]

    def _emit(status: str) -> dict:
        nonlocal next_id
        m = managers[int(rng.integers(0, len(managers)))]
        # to: any active not equal to m
        cand = [e for e in actives if e["emp_id"] != m["emp_id"]]
        t = cand[int(rng.integers(0, len(cand)))]
        title = titles_pool[int(rng.integers(0, len(titles_pool)))]
        scope = str(rng.choice(_SCOPE_SUMMARIES))
        if status == "active":
            start = end_date - timedelta(days=int(rng.integers(7, 365)))
            end = None if rng.random() < 0.30 else _iso(end_date + timedelta(days=int(rng.integers(30, 180))))
        else:
            start = end_date - timedelta(days=int(rng.integers(60, 540)))
            end = _iso(start + timedelta(days=int(rng.integers(15, 180))))
        d = {
            "delegation_id": f"del_{1000 + next_id:06d}",
            "from_emp_id": m["emp_id"],
            "to_emp_id": t["emp_id"],
            "title": title,
            "scope_summary": scope,
            "start_date": _iso(start),
            "end_date": end,
            "status": status,
        }
        next_id += 1
        return d

    for _ in range(n_active):
        out.append(_emit("active"))
    for _ in range(n_completed):
        out.append(_emit("completed"))

    return out


# ----- hr_requests ---------------------------------------------------------

_HR_REQUEST_TYPES = (
    ("employer_request", "обращение к работодателю"),
    ("salary_certificate", "справка с места работы"),
    ("2-NDFL", "справка 2-НДФЛ"),
    ("payroll", "заявление о счёте для перечисления зарплаты"),
    ("work_book_copy", "копия трудовой книжки"),
    ("other", "вопрос о выплатах"),
)


def gen_hr_requests(rng: np.random.Generator,
                     employees: list[dict[str, Any]],
                     end_date: date) -> list[dict[str, Any]]:
    """For ~40% of active employees, 1-4 requests in last 6 months.

    Most done; ~10% processing; ~3% open (recent).
    """
    out: list[dict[str, Any]] = []
    next_id = 0
    for emp in _active_employees(employees):
        if rng.random() > 0.40:
            continue
        n = int(rng.integers(1, 5))
        for _ in range(n):
            r = rng.random()
            if r < 0.03:
                status = "open"
            elif r < 0.13:
                status = "processing"
            else:
                status = "done"
            type_key, type_human = _HR_REQUEST_TYPES[int(rng.integers(0, len(_HR_REQUEST_TYPES)))]
            submit_offset = int(rng.integers(1, 180))
            submitted = end_date - timedelta(days=submit_offset)
            due = submitted + timedelta(days=int(rng.integers(3, 21)))
            if status == "done":
                completed = _iso(submitted + timedelta(days=int(rng.integers(1, max(2, submit_offset - 1)))))
            else:
                completed = None
            req_id = f"req_{1000 + next_id:06d}"
            next_id += 1
            out.append({
                "request_id": req_id,
                "emp_id": emp["emp_id"],
                "type": type_key,
                "submitted_date": _iso(submitted),
                "status": status,
                "due_date": _iso(due),
                "completed_date": completed,
                "subject": type_human,
                "details": f"Запрос: {type_human}. Сотрудник: {emp.get('full_name', '')}.",
            })
    return out


# ----- surveys_meta --------------------------------------------------------

def gen_surveys_meta(rng: np.random.Generator,
                      end_date: date) -> list[dict[str, Any]]:
    """6-8 campaigns: 4 завершённых (incl. 2 «оценка 360»), 2 активных.

    target_count ~100, completed_count = 60-95% for completed.
    """
    out: list[dict[str, Any]] = []
    completed_titles = [
        "Оценка компетенций методом 360 (от 23.09)",
        "Оценка 360 по произвольным объектам оценки",
        "369 обучение 5 марта",
        "Оценка компетенций методом 360 (копия) (копия)",
        "Основная форма",
        "Оценка компетенций сотрудников",
    ]
    active_titles = [
        "Опрос вовлечённости Q2 2026",
        "АОС по итогам обучения",
        "Оценка компетенций сотрудников",
    ]

    next_id = 0
    # 4 completed, 2 active baseline; can wiggle to 6-8 total via rng
    n_completed = int(rng.integers(4, 6))
    n_active = int(rng.integers(2, 3))

    for i in range(n_completed):
        title = completed_titles[i % len(completed_titles)]
        kind = "360" if "360" in title else ("оценка" if "оцен" in title.lower() else "опрос")
        launched_offset = int(rng.integers(120, 500))
        launched = end_date - timedelta(days=launched_offset)
        ends = launched + timedelta(days=int(rng.integers(15, 40)))
        target = 100
        completed = int(rng.integers(60, 96))
        out.append({
            "survey_id": f"survey_{1000 + next_id:06d}",
            "name": title,
            "kind": kind,
            "status": "completed",
            "launched_date": _iso(launched),
            "ends_date": _iso(ends),
            "target_count": target,
            "completed_count": completed,
            "is_important": 1,
        })
        next_id += 1

    for i in range(n_active):
        title = active_titles[i % len(active_titles)]
        kind = "360" if "360" in title else ("оценка" if "оцен" in title.lower() else "опрос")
        launched = end_date - timedelta(days=int(rng.integers(7, 30)))
        ends = end_date + timedelta(days=int(rng.integers(7, 30)))
        target = 100
        completed = int(rng.integers(0, 60))
        out.append({
            "survey_id": f"survey_{1000 + next_id:06d}",
            "name": title,
            "kind": kind,
            "status": "active",
            "launched_date": _iso(launched),
            "ends_date": _iso(ends),
            "target_count": target,
            "completed_count": completed,
            "is_important": 1,
        })
        next_id += 1

    return out


__all__ = [
    "gen_vacancies", "gen_candidates",
    "gen_goals", "gen_key_results",
    "gen_learning_feed", "gen_talent_pool_status",
    "gen_delegations", "gen_hr_requests", "gen_surveys_meta",
]
