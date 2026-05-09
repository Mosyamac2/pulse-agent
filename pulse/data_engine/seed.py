"""Generate the synthetic Sber HR sandbox in `data/sber_hr.db`.

Deterministic (`seed=42`). Designed so that:

* the 8 terminated employees show a 60-day declining trend before `term_date`
  (tasks↓, switches↑, negative peer feedback) — gives the attrition model
  honest signal;
* the colleague graph is connected (single component) via Barabási–Albert with m=3;
* archetype-specific distributions are visible in aggregates per `unit_id`.

Public entry: `seed(db_path: Path, force: bool=False) -> dict[str, int]`
returning a row-count summary.
"""
from __future__ import annotations

import json
import logging
import math
import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import networkx as nx
import numpy as np
from faker import Faker
from sqlite_utils import Database

from . import archetypes as A
from .schema import create_tables

log = logging.getLogger(__name__)

END_DATE = date(2026, 5, 9)
START_DATE = END_DATE - timedelta(days=730)  # ~24 months

UNITS_COUNT = 12
POSITIONS_COUNT = 20
EMPLOYEES_TOTAL = 100
TERMINATIONS_TARGET = 8

POSITION_TYPES = ["IT", "sales", "analytics", "ops", "support"]
COURSE_TOPICS = ["leadership", "hard_skill", "soft_skill", "compliance", "banking"]
CITY_POOL = ["Москва", "Санкт-Петербург", "Казань", "Новосибирск", "Екатеринбург", "Нижний Новгород"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso(d: date | datetime) -> str:
    return d.isoformat()


def _weekdays(start: date, end: date) -> list[date]:
    out: list[date] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            out.append(cur)
        cur += timedelta(days=1)
    return out


def _all_days(start: date, end: date) -> list[date]:
    out: list[date] = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def _band(value: float) -> str:
    if value < 0.33:
        return "low"
    if value < 0.66:
        return "mid"
    return "high"


def _archetype_assignments() -> list[A.Archetype]:
    """Return a list of 100 archetypes following the share table (deterministic order)."""
    out: list[A.Archetype] = []
    for arc in A.ARCHETYPES:
        out.extend([arc] * arc.share)
    assert len(out) == EMPLOYEES_TOTAL
    return out


# ---------------------------------------------------------------------------
# Generators — small first
# ---------------------------------------------------------------------------

def gen_units(rng: np.random.Generator, fake: Faker) -> list[dict[str, Any]]:
    """3-level hierarchy: 1 root → 4 mid → 7 leaf (12 total)."""
    units = [
        {"unit_id": "unit_root", "name": "Сбер: блок «Розница и обслуживание»",
         "parent_unit_id": "", "level": 0,
         "processes_json": json.dumps(["стратегия", "управление"])},
    ]
    mids = [
        ("unit_it",        "Технологический блок",        ["разработка", "инфраструктура", "поддержка"]),
        ("unit_sales",     "Продажи и розничная сеть",   ["продажи", "консультации", "акции"]),
        ("unit_analytics", "Аналитика и BI",              ["BI", "DS", "репортинг"]),
        ("unit_ops",       "Операционный блок",           ["платежи", "сверки", "контроль"]),
    ]
    for uid, name, procs in mids:
        units.append({"unit_id": uid, "name": name, "parent_unit_id": "unit_root",
                      "level": 1, "processes_json": json.dumps(procs)})
    leaves = [
        ("unit_it_back",   "Бэкенд-разработка",          "unit_it",        ["python", "go", "k8s"]),
        ("unit_it_data",   "Платформа данных",            "unit_it",        ["airflow", "spark", "kafka"]),
        ("unit_it_sec",    "Безопасность",                 "unit_it",        ["AppSec", "IAM"]),
        ("unit_sales_corp", "Корпоративные продажи",       "unit_sales",     ["B2B", "тендеры"]),
        ("unit_sales_retail", "Розничная сеть",            "unit_sales",     ["консультации", "обслуживание"]),
        ("unit_an_credit", "Кредитные риски",              "unit_analytics", ["скоринг", "отчётность"]),
        ("unit_ops_clear", "Клиринг и сверки",             "unit_ops",       ["сверки", "контроль"]),
    ]
    for uid, name, parent, procs in leaves:
        units.append({"unit_id": uid, "name": name, "parent_unit_id": parent,
                      "level": 2, "processes_json": json.dumps(procs)})
    assert len(units) == UNITS_COUNT
    return units


def gen_positions(rng: np.random.Generator) -> list[dict[str, Any]]:
    """20 positions across 5 grades and 5 type buckets."""
    titles_by_type = {
        "IT":         ["Junior Backend", "Backend Engineer", "Senior Engineer", "Tech Lead"],
        "sales":      ["Менеджер по продажам", "Старший менеджер", "Руководитель отдела продаж", "Директор по продажам"],
        "analytics":  ["Аналитик данных", "Старший аналитик", "Lead Analyst", "Head of Analytics"],
        "ops":        ["Операционист", "Старший операционист", "Руководитель группы", "Начальник отдела операций"],
        "support":    ["Специалист поддержки", "Старший специалист поддержки", "Руководитель поддержки", "Директор сервиса"],
    }
    skills_by_type = {
        "IT":         ["python", "sql", "git", "linux"],
        "sales":      ["переговоры", "CRM", "продукт"],
        "analytics":  ["sql", "python", "BI", "статистика"],
        "ops":        ["1С", "сверки", "compliance"],
        "support":    ["communication", "ITSM", "клиент-сервис"],
    }
    positions: list[dict[str, Any]] = []
    for ptype, titles in titles_by_type.items():
        for grade, title in enumerate(titles, start=1):
            pid = f"pos_{ptype}_{grade}"
            positions.append({
                "position_id": pid,
                "title": title,
                "type": ptype,
                "grade_level": grade,
                "core_skills_json": json.dumps(skills_by_type[ptype]),
            })
    assert len(positions) == POSITIONS_COUNT
    return positions


def gen_courses(rng: np.random.Generator) -> list[dict[str, Any]]:
    courses: list[dict[str, Any]] = []
    catalog = [
        ("c_lead_intro",  "Основы лидерства",       "leadership", 16, 1),
        ("c_lead_adv",    "Лидерство в команде",     "leadership", 24, 2),
        ("c_python",      "Python для аналитика",    "hard_skill", 32, 1),
        ("c_sql",         "SQL для роста",            "hard_skill", 24, 1),
        ("c_data_prod",   "ML в продуктовой задаче", "hard_skill", 40, 3),
        ("c_negotiate",   "Переговоры в продажах",   "soft_skill", 16, 1),
        ("c_feedback",    "Культура обратной связи", "soft_skill", 8, 1),
        ("c_compliance",  "Compliance: must-know",   "compliance", 4, 1),
        ("c_aml",         "AML и KYC",                "compliance", 8, 2),
        ("c_banking_101", "Банковские продукты 101", "banking", 12, 1),
        ("c_banking_credit", "Кредитные продукты",   "banking", 16, 2),
        ("c_burnout",     "Профилактика выгорания",  "soft_skill", 6, 1),
    ]
    for cid, title, topic, dur, lvl in catalog:
        courses.append({
            "course_id": cid, "title": title, "topic": topic,
            "duration_h": float(dur), "level": lvl,
        })
    return courses


def gen_corp_events(rng: np.random.Generator, fake: Faker,
                    start: date, end: date) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cur = start.replace(day=1)
    idx = 0
    while cur <= end:
        # one event per month
        ev_date = cur + timedelta(days=int(rng.integers(2, 26)))
        kind = rng.choice(["meetup", "team_building", "training_offsite", "town_hall"])
        out.append({
            "event_id": f"ev_{idx:04d}",
            "name": f"{kind.replace('_', ' ').title()} {ev_date.year}-{ev_date.month:02d}",
            "date": _iso(ev_date),
            "kind": str(kind),
        })
        idx += 1
        # advance month
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)
    return out


# ---------------------------------------------------------------------------
# Employees
# ---------------------------------------------------------------------------

def gen_employees(rng: np.random.Generator, fake: Faker,
                  units: list[dict], positions: list[dict],
                  start: date, end: date) -> list[dict[str, Any]]:
    """Assign archetype, unit, position, hire date. Pick 8 terminations."""
    arc_list = _archetype_assignments()
    rng_perm = rng.permutation(len(arc_list))
    arc_list = [arc_list[i] for i in rng_perm]  # shuffled but deterministic

    leaf_units = [u["unit_id"] for u in units if u["level"] == 2]
    pos_by_type: dict[str, list[dict]] = defaultdict(list)
    for p in positions:
        pos_by_type[p["type"]].append(p)

    # mapping unit→typical position type
    unit_type = {
        "unit_it_back": "IT", "unit_it_data": "IT", "unit_it_sec": "IT",
        "unit_sales_corp": "sales", "unit_sales_retail": "sales",
        "unit_an_credit": "analytics",
        "unit_ops_clear": "ops",
    }

    employees: list[dict[str, Any]] = []
    for i, arc in enumerate(arc_list):
        emp_id = f"emp_{i+1:03d}"
        gender = "F" if rng.random() < 0.48 else "M"
        first = fake.first_name_female() if gender == "F" else fake.first_name_male()
        last = fake.last_name_female() if gender == "F" else fake.last_name_male()
        full_name = f"{last} {first}"
        age = int(rng.integers(23, 56))
        if arc.name in ("newbie_enthusiast", "isolated_newbie"):
            age = int(rng.integers(22, 32))
        elif arc.name == "drifting_veteran":
            age = int(rng.integers(42, 60))
        birth_date = end.replace(year=end.year - age) - timedelta(days=int(rng.integers(0, 365)))

        # unit assignment — round-robin with bias for IT-leaning archetypes
        if arc.name in ("star_perfectionist", "newbie_enthusiast"):
            unit_id = rng.choice(["unit_it_back", "unit_it_data", "unit_an_credit",
                                   "unit_sales_corp", "unit_ops_clear"])
        elif arc.name == "overwhelmed_manager":
            unit_id = rng.choice(leaf_units)
        else:
            unit_id = rng.choice(leaf_units)
        ptype = unit_type.get(unit_id, rng.choice(POSITION_TYPES))

        # grade — manager → 3-4, newbie → 1-2, others → 2-3
        if arc.leader:
            grade = int(rng.choice([3, 4]))
        elif arc.name in ("newbie_enthusiast", "isolated_newbie"):
            grade = 1
        elif arc.name == "drifting_veteran":
            grade = int(rng.choice([2, 3]))
        else:
            grade = int(rng.choice([2, 3]))
        # find a matching position
        candidates = [p for p in pos_by_type[ptype] if p["grade_level"] == grade]
        if not candidates:
            candidates = pos_by_type[ptype]
        position_id = rng.choice([p["position_id"] for p in candidates])

        # hire date
        if arc.name in ("newbie_enthusiast", "isolated_newbie"):
            hire = end - timedelta(days=int(rng.integers(60, 540)))
        elif arc.name == "drifting_veteran":
            hire = end - timedelta(days=int(rng.integers(2400, 5500)))
        else:
            hire = end - timedelta(days=int(rng.integers(540, 2400)))
        if hire < date(2010, 1, 1):
            hire = date(2010, 1, 1)

        employees.append({
            "emp_id": emp_id,
            "full_name": full_name,
            "gender": gender,
            "birth_date": _iso(birth_date),
            "city": str(rng.choice(CITY_POOL)),
            "education": str(rng.choice(["МГУ", "СПбГУ", "ВШЭ", "МФТИ", "МИФИ", "Финуниверситет"])),
            "language_skills_json": json.dumps(["ru"] + (["en"] if rng.random() < 0.7 else [])),
            "hire_date": _iso(hire),
            "term_date": "",
            "status": "active",
            "grade_level": grade,
            "position_id": str(position_id),
            "unit_id": str(unit_id),
            "archetype": arc.name,
        })

    # Pick 8 terminations from tired/drifting/overworked archetypes only —
    # and only from employees with ≥ 18 months of tenure so the 60-day decline
    # window fits cleanly into their history.
    min_tenure_days = 540
    candidates = [
        i for i, e in enumerate(employees)
        if e["archetype"] in ("tired_midfielder", "drifting_veteran", "overwhelmed_manager")
        and (end - date.fromisoformat(e["hire_date"])).days >= min_tenure_days
    ]
    chosen = list(rng.choice(candidates, size=TERMINATIONS_TARGET, replace=False))
    for idx in chosen:
        hire_d = date.fromisoformat(employees[idx]["hire_date"])
        # term offset between 7 and 360 days from END, but never before hire+180.
        max_offset = min(360, (end - hire_d).days - 180)
        offset = int(rng.integers(7, max(8, max_offset)))
        td = end - timedelta(days=offset)
        employees[idx]["term_date"] = _iso(td)
        employees[idx]["status"] = "terminated"

    # 2 employees on maternity (active women, age 25-35) — flagged for skill demo
    fem_active = [i for i, e in enumerate(employees)
                  if e["gender"] == "F" and e["status"] == "active"]
    if len(fem_active) >= 2:
        for idx in list(rng.choice(fem_active, size=2, replace=False)):
            employees[idx]["status"] = "maternity"

    return employees


def gen_family(rng: np.random.Generator, employees: list[dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in employees:
        marital = str(rng.choice(["single", "married", "divorced", "domestic_partner"],
                                  p=[0.35, 0.50, 0.10, 0.05]))
        kids = int(rng.choice([0, 1, 2, 3], p=[0.45, 0.30, 0.20, 0.05]))
        out.append({
            "emp_id": e["emp_id"],
            "marital_status": marital,
            "kids_count": kids,
            "spouse_works_in_company": int(rng.random() < 0.04),
        })
    return out


def gen_career_history(rng: np.random.Generator, employees: list[dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    rid = 1
    for e in employees:
        # 1-3 prior records before current, then current
        n_prior = int(rng.choice([0, 1, 2], p=[0.4, 0.4, 0.2]))
        cur_start = e["hire_date"]
        for _ in range(n_prior):
            past_company = str(rng.choice(["ВТБ", "Альфа", "Тинькофф", "Газпромбанк",
                                            "Яндекс", "X5", "Ростелеком"]))
            dur = int(rng.integers(180, 1500))
            past_end = date.fromisoformat(cur_start) - timedelta(days=int(rng.integers(0, 30)))
            past_start = past_end - timedelta(days=dur)
            out.append({
                "id": rid, "emp_id": e["emp_id"], "position_id": "ext",
                "unit_id": "external", "start_date": _iso(past_start),
                "end_date": _iso(past_end), "company": past_company,
            })
            rid += 1
            cur_start = _iso(past_start)
        out.append({
            "id": rid, "emp_id": e["emp_id"], "position_id": e["position_id"],
            "unit_id": e["unit_id"], "start_date": e["hire_date"],
            "end_date": e["term_date"] or "", "company": "Сбер",
        })
        rid += 1
    return out


def gen_promotions(rng: np.random.Generator, employees: list[dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    rid = 1
    for e in employees:
        arc = A.by_name(e["archetype"])
        years_in = max(0.5, (END_DATE - date.fromisoformat(e["hire_date"])).days / 365.0)
        n_prom = int(np.random.default_rng(int(e["emp_id"].split("_")[1]))
                     .binomial(int(years_in), arc.promotion_probability_per_year))
        cur_grade = max(1, e["grade_level"] - n_prom)
        for k in range(n_prom):
            d = date.fromisoformat(e["hire_date"]) + timedelta(days=int((k + 1) * 365 * (years_in / max(1, n_prom + 1))))
            new_grade = min(5, cur_grade + 1)
            out.append({
                "id": rid, "emp_id": e["emp_id"], "date": _iso(d),
                "from_grade": int(cur_grade), "to_grade": int(new_grade),
                "from_position_id": e["position_id"],  # simplification
                "to_position_id": e["position_id"],
            })
            rid += 1
            cur_grade = new_grade
    return out


def gen_performance_reviews(rng: np.random.Generator, employees: list[dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    rid = 1
    # H1 / H2 each year of tenure within the 24m window
    for e in employees:
        arc = A.by_name(e["archetype"])
        hire = date.fromisoformat(e["hire_date"])
        end = date.fromisoformat(e["term_date"]) if e["term_date"] else END_DATE
        for y in range(START_DATE.year, end.year + 1):
            for half_label, ref_date in [("H1", date(y, 6, 30)), ("H2", date(y, 12, 31))]:
                if ref_date < hire or ref_date > end:
                    continue
                if ref_date > END_DATE:
                    continue
                trend = 0.0
                if e["status"] == "terminated" and (date.fromisoformat(e["term_date"]) - ref_date).days < 270:
                    trend = -0.4  # declining before term
                score = float(np.clip(rng.normal(arc.perf_score_mean + trend, arc.perf_score_std), 1.0, 5.0))
                out.append({
                    "id": rid, "emp_id": e["emp_id"], "period": f"{y}{half_label}",
                    "score": round(score, 2),
                    "reviewer_id": "mgr_anon",
                    "comment_summary": "автогенерированная сводка ревью",
                })
                rid += 1
    return out


# ---------------------------------------------------------------------------
# Daily metrics — biggest data volume
# ---------------------------------------------------------------------------

def gen_daily_metrics(rng: np.random.Generator,
                      employees: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Activity, digital_patterns, wearables for each emp on each day in [hire, end_or_term]."""
    activity: list[dict] = []
    digital: list[dict] = []
    wearables: list[dict] = []
    aid = did = wid = 1

    weekdays_all = _weekdays(START_DATE, END_DATE)
    alldays_all = _all_days(START_DATE, END_DATE)
    weekday_set = set(weekdays_all)

    for e in employees:
        arc = A.by_name(e["archetype"])
        hire = date.fromisoformat(e["hire_date"])
        last = date.fromisoformat(e["term_date"]) if e["term_date"] else END_DATE
        last = min(last, END_DATE)
        emp_id = e["emp_id"]
        is_terminated = e["status"] == "terminated"
        emp_seed = int(emp_id.split("_")[1])
        emp_rng = np.random.default_rng(42 + emp_seed)

        for d in alldays_all:
            if d < hire or d > last:
                continue
            is_weekend = d.weekday() >= 5

            # decline factor in last 60 days for terminated employees
            decline = 0.0
            if is_terminated:
                lag = (last - d).days
                if 0 <= lag < 60:
                    decline = (60 - lag) / 60.0  # 0 .. ~1 as we approach term

            # 1) activity (only weekdays for tasks)
            if not is_weekend:
                base_tasks = arc.tasks_done_mean * (1.0 - 0.55 * decline)
                tasks = int(max(0, emp_rng.normal(base_tasks, arc.tasks_done_std)))
                hours = float(np.clip(emp_rng.normal(arc.hours_logged_mean - 1.0 * decline, 0.8), 0, 14))
                meetings = int(max(0, emp_rng.normal(arc.meetings_mean, 1.5)))
                activity.append({
                    "id": aid, "emp_id": emp_id, "date": _iso(d),
                    "tasks_done": tasks, "hours_logged": round(hours, 2),
                    "meetings_count": meetings, "is_weekend": 0,
                })
                aid += 1

                base_focus = arc.focus_mean * (1.0 - 0.5 * decline)
                base_switches = arc.switches_mean * (1.0 + 0.7 * decline)
                digital.append({
                    "id": did, "emp_id": emp_id, "date": _iso(d),
                    "focus_score": round(float(np.clip(emp_rng.normal(base_focus, 0.08), 0.0, 1.0)), 3),
                    "switches_per_min": round(float(np.clip(emp_rng.normal(base_switches, 1.0), 0.5, 12.0)), 2),
                    "working_hours": round(float(np.clip(emp_rng.normal(arc.working_hours_mean, 0.7), 0, 14)), 2),
                })
                did += 1

            # 2) wearables — every day
            steps_mean = arc.steps_mean * (0.7 if is_weekend else 1.0)
            sleep_mean = arc.sleep_h_mean
            stress_mean = arc.stress_mean * (1.0 + 0.3 * decline)
            wearables.append({
                "id": wid, "emp_id": emp_id, "date": _iso(d),
                "steps": int(max(0, emp_rng.normal(steps_mean, 1500))),
                "sleep_h": round(float(np.clip(emp_rng.normal(sleep_mean, 0.6), 3.0, 10.0)), 2),
                "stress_index": round(float(np.clip(emp_rng.normal(stress_mean, 0.10), 0.0, 1.0)), 3),
                "hr_avg": round(float(np.clip(emp_rng.normal(72, 7), 50, 110)), 1),
            })
            wid += 1

    log.info("daily metrics rows: activity=%d digital=%d wearables=%d", len(activity), len(digital), len(wearables))
    return activity, digital, wearables


# ---------------------------------------------------------------------------
# Peer feedback, courses, assessments, vacations, jira/confluence/bitbucket,
# branch tasks, comm_style, meetings, finance, lifestyle, security, mobility
# ---------------------------------------------------------------------------

def gen_peer_feedback(rng: np.random.Generator, employees: list[dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    rid = 1
    emp_ids = [e["emp_id"] for e in employees]
    for e in employees:
        arc = A.by_name(e["archetype"])
        hire = date.fromisoformat(e["hire_date"])
        last = date.fromisoformat(e["term_date"]) if e["term_date"] else END_DATE
        months = max(1, (last - hire).days // 30)
        n = int(rng.poisson(arc.peer_volume_per_month * months))
        for _ in range(n):
            ts = hire + timedelta(days=int(rng.integers(0, max(1, (last - hire).days))))
            from_id = str(rng.choice(emp_ids))
            if from_id == e["emp_id"]:
                continue
            sentiment = float(np.clip(rng.normal(arc.peer_sentiment_mean, 0.30), -1.0, 1.0))
            # decline before term
            if e["status"] == "terminated":
                lag = (last - ts.date() if isinstance(ts, datetime) else last - ts).days
                if 0 <= lag < 90:
                    sentiment = float(np.clip(sentiment - 0.3, -1.0, 1.0))
            cat = str(rng.choice(["cooperation", "quality", "reliability", "leadership"]))
            out.append({
                "id": rid, "emp_id": e["emp_id"], "from_id": from_id,
                "ts": _iso(ts) + "T12:00:00",
                "sentiment_score": round(sentiment, 3),
                "category": cat,
                "text_summary": "autogen sentiment summary",
            })
            rid += 1
    return out


def gen_course_enrollments(rng: np.random.Generator, employees: list[dict],
                            courses: list[dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    rid = 1
    for e in employees:
        arc = A.by_name(e["archetype"])
        hire = date.fromisoformat(e["hire_date"])
        last = date.fromisoformat(e["term_date"]) if e["term_date"] else END_DATE
        n = int(rng.integers(1, 5))
        chosen = rng.choice(len(courses), size=n, replace=False)
        for idx in chosen:
            c = courses[int(idx)]
            sd = hire + timedelta(days=int(rng.integers(0, max(1, (last - hire).days))))
            ed = sd + timedelta(days=int(c["duration_h"] / 4) + int(rng.integers(7, 60)))
            if ed > last:
                ed = last
            r = rng.random()
            if r < arc.course_complete_rate:
                status = "completed"
                score = round(float(np.clip(rng.normal(0.7, 0.15), 0.0, 1.0)), 3)
            elif r < arc.course_complete_rate + 0.2:
                status = "in_progress"
                score = 0.0
            else:
                status = "dropped"
                score = 0.0
            out.append({
                "id": rid, "emp_id": e["emp_id"], "course_id": c["course_id"],
                "status": status, "start_date": _iso(sd), "end_date": _iso(ed),
                "score": score,
            })
            rid += 1
    return out


def gen_assessments(rng: np.random.Generator, employees: list[dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    rid = 1
    for e in employees:
        arc = A.by_name(e["archetype"])
        hire = date.fromisoformat(e["hire_date"])
        last = date.fromisoformat(e["term_date"]) if e["term_date"] else END_DATE
        # SberQ once a year, psych once at hire, 360 once a year, systemic computed
        for kind, freq in [("psych", 0), ("sberq", 365), ("360", 365), ("systemic", 365)]:
            if freq == 0:
                dates = [hire + timedelta(days=int(rng.integers(0, 60)))]
            else:
                dates = []
                cur = hire + timedelta(days=int(rng.integers(60, 200)))
                while cur < last:
                    dates.append(cur)
                    cur += timedelta(days=freq)
            for d in dates:
                # base score depends on archetype
                if kind == "360":
                    base = arc.cooperation_360_mean
                elif kind == "systemic":
                    base = max(0.2, min(0.95, 0.55 + (arc.focus_mean - 0.5)))
                else:
                    base = 0.55 + 0.10 * (arc.perf_score_mean - 3.0)
                score = float(np.clip(rng.normal(base, 0.10), 0.05, 0.99))
                details = {"kind": kind, "facets": {"cooperation": round(arc.cooperation_360_mean, 2)}}
                out.append({
                    "id": rid, "emp_id": e["emp_id"], "type": kind,
                    "date": _iso(d), "score": round(score, 3),
                    "details_json": json.dumps(details, ensure_ascii=False),
                })
                rid += 1
    return out


def gen_vacations(rng: np.random.Generator, employees: list[dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    rid = 1
    for e in employees:
        arc = A.by_name(e["archetype"])
        hire = date.fromisoformat(e["hire_date"])
        last = date.fromisoformat(e["term_date"]) if e["term_date"] else END_DATE
        years = max(1, (last - hire).days // 365)
        annual_count = years if not arc.burnout_prone else max(0, years - 1)
        for _ in range(annual_count):
            sd = hire + timedelta(days=int(rng.integers(60, max(61, (last - hire).days - 30))))
            ed = sd + timedelta(days=int(rng.choice([7, 10, 14, 21])))
            out.append({
                "id": rid, "emp_id": e["emp_id"], "kind": "annual",
                "start_date": _iso(sd), "end_date": _iso(ed),
            })
            rid += 1
        # sick leaves
        sick_n = int(rng.poisson(0.8 + (1.5 if arc.burnout_prone else 0)))
        for _ in range(sick_n):
            sd = hire + timedelta(days=int(rng.integers(0, max(1, (last - hire).days))))
            ed = sd + timedelta(days=int(rng.choice([2, 3, 5, 7])))
            out.append({
                "id": rid, "emp_id": e["emp_id"], "kind": "sick",
                "start_date": _iso(sd), "end_date": _iso(ed),
            })
            rid += 1
        # maternity for status==maternity employees
        if e["status"] == "maternity":
            sd = last - timedelta(days=int(rng.integers(60, 365)))
            ed = sd + timedelta(days=420)
            out.append({
                "id": rid, "emp_id": e["emp_id"], "kind": "maternity",
                "start_date": _iso(sd), "end_date": _iso(ed),
            })
            rid += 1
    return out


def gen_jira_issues(rng: np.random.Generator, employees: list[dict],
                     positions: list[dict]) -> list[dict[str, Any]]:
    pos_type = {p["position_id"]: p["type"] for p in positions}
    it_emps = [e for e in employees if pos_type.get(e["position_id"]) == "IT"]
    out: list[dict[str, Any]] = []
    if not it_emps:
        return out
    issue_idx = 1
    for e in it_emps:
        hire = date.fromisoformat(e["hire_date"])
        last = date.fromisoformat(e["term_date"]) if e["term_date"] else END_DATE
        weekdays_alive = [d for d in _weekdays(hire, last)]
        # ~1 issue per weekday on avg for IT
        n = int(rng.poisson(len(weekdays_alive) * 0.8))
        for _ in range(n):
            d = weekdays_alive[int(rng.integers(0, len(weekdays_alive)))]
            tcreated = datetime.combine(d, datetime.min.time()).replace(hour=10, minute=int(rng.integers(0, 59)))
            ttype = str(rng.choice(["bug", "feature", "task"], p=[0.30, 0.30, 0.40]))
            prio = str(rng.choice(["low", "med", "high", "critical"], p=[0.45, 0.40, 0.12, 0.03]))
            resolved = rng.random() < 0.85
            tres = ""
            status = "in_progress"
            if resolved:
                resd = d + timedelta(days=int(rng.integers(1, 14)))
                if resd > last:
                    resd = last
                tres = _iso(datetime.combine(resd, datetime.min.time()).replace(hour=18))
                status = "resolved"
            out.append({
                "issue_key": f"PULSE-{issue_idx:06d}",
                "emp_id": e["emp_id"],
                "status": status,
                "ts_created": _iso(tcreated),
                "ts_resolved": tres,
                "type": ttype,
                "priority": prio,
                "summary": f"autogen {ttype} {prio}",
            })
            issue_idx += 1
    return out


def gen_confluence_pages(rng: np.random.Generator, employees: list[dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    pid = 1
    for e in employees:
        n = int(rng.poisson(6))
        hire = date.fromisoformat(e["hire_date"])
        last = date.fromisoformat(e["term_date"]) if e["term_date"] else END_DATE
        for _ in range(n):
            d = hire + timedelta(days=int(rng.integers(0, max(1, (last - hire).days))))
            out.append({
                "page_id": f"page_{pid:06d}",
                "emp_id": e["emp_id"],
                "ts_created": _iso(d) + "T11:00:00",
                "length_chars": int(rng.integers(500, 8000)),
                "title": f"Заметка #{pid}",
            })
            pid += 1
    return out


def gen_bitbucket_commits(rng: np.random.Generator, employees: list[dict],
                           positions: list[dict]) -> list[dict[str, Any]]:
    pos_type = {p["position_id"]: p["type"] for p in positions}
    out: list[dict[str, Any]] = []
    cid = 1
    for e in employees:
        if pos_type.get(e["position_id"]) != "IT":
            continue
        hire = date.fromisoformat(e["hire_date"])
        last = date.fromisoformat(e["term_date"]) if e["term_date"] else END_DATE
        n = int(rng.poisson(150))
        for _ in range(n):
            d = hire + timedelta(days=int(rng.integers(0, max(1, (last - hire).days))))
            out.append({
                "commit_id": f"sha_{cid:08d}",
                "emp_id": e["emp_id"],
                "ts": _iso(d) + "T15:00:00",
                "lines_changed": int(rng.integers(1, 800)),
                "repo": str(rng.choice(["core", "api", "web", "data-platform", "infra"])),
            })
            cid += 1
    return out


def gen_branch_tasks(rng: np.random.Generator, employees: list[dict],
                      positions: list[dict]) -> list[dict[str, Any]]:
    pos_type = {p["position_id"]: p["type"] for p in positions}
    out: list[dict[str, Any]] = []
    rid = 1
    for e in employees:
        if pos_type.get(e["position_id"]) != "sales":
            continue
        hire = date.fromisoformat(e["hire_date"])
        last = date.fromisoformat(e["term_date"]) if e["term_date"] else END_DATE
        n = int(rng.poisson(180))
        for _ in range(n):
            d = hire + timedelta(days=int(rng.integers(0, max(1, (last - hire).days))))
            out.append({
                "id": rid, "emp_id": e["emp_id"],
                "ts": _iso(d) + "T13:00:00",
                "kind": str(rng.choice(["client_consult", "cash_op", "loan_application"])),
            })
            rid += 1
    return out


def gen_comm_style(rng: np.random.Generator, employees: list[dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in employees:
        arc = A.by_name(e["archetype"])
        out.append({
            "emp_id": e["emp_id"],
            "avg_length_chars": round(float(rng.normal(420, 130)), 1),
            "formality_score": round(float(np.clip(rng.normal(0.55 + (0.10 if arc.leader else 0), 0.15), 0, 1)), 3),
            "response_speed_h": round(float(np.clip(rng.normal(4.0 + (3 if arc.overworked else 0), 2.0), 0.1, 48)), 2),
            "sla_compliance_pct": round(float(np.clip(rng.normal(0.85 - (0.15 if arc.overworked else 0), 0.07), 0, 1)), 3),
        })
    return out


def gen_meeting_artifacts(rng: np.random.Generator, employees: list[dict]) -> tuple[list[dict], list[dict]]:
    artifacts: list[dict] = []
    transcripts: list[dict] = []
    aid = tid = 1
    for e in employees:
        arc = A.by_name(e["archetype"])
        hire = date.fromisoformat(e["hire_date"])
        last = date.fromisoformat(e["term_date"]) if e["term_date"] else END_DATE
        n = int(rng.poisson(8 + (12 if arc.leader else 0)))
        for _ in range(n):
            d = hire + timedelta(days=int(rng.integers(0, max(1, (last - hire).days))))
            mid = f"mtg_{aid:06d}"
            sentiment = float(np.clip(rng.normal(arc.peer_sentiment_mean + 0.1, 0.2), -1.0, 1.0))
            artifacts.append({
                "id": aid, "emp_id": e["emp_id"], "meeting_id": mid,
                "ts": _iso(d) + "T14:00:00",
                "summary": "автогенерированный протокол встречи",
                "sentiment": round(sentiment, 3),
            })
            transcripts.append({
                "id": tid, "emp_id": e["emp_id"], "meeting_id": mid,
                "agg_topics_json": json.dumps(["приоритеты", "блокеры", "следующие шаги"]),
                "sentiment": round(sentiment, 3),
            })
            aid += 1
            tid += 1
    return artifacts, transcripts


def gen_finance_lifestyle_security_mobility(rng: np.random.Generator,
                                            employees: list[dict]) -> tuple[list, list, list, list, list]:
    """Five tabs at once for tight memory: finance, investment, lifestyle, security, mobility."""
    fin: list[dict] = []
    inv: list[dict] = []
    life: list[dict] = []
    sec: list[dict] = []
    mob: list[dict] = []
    fid = lid = sid = mid = 1
    months: list[str] = []
    cur = START_DATE.replace(day=1)
    while cur <= END_DATE:
        months.append(f"{cur.year:04d}-{cur.month:02d}")
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)

    for e in employees:
        arc = A.by_name(e["archetype"])
        for m in months:
            inc_band = _band(min(1.0, 0.3 + 0.15 * e["grade_level"] + rng.normal(0, 0.08)))
            spend_band = _band(min(1.0, 0.3 + 0.10 * e["grade_level"] + rng.normal(0, 0.10)))
            savings = float(np.clip(rng.normal(0.18 - (0.10 if arc.burnout_prone else 0), 0.08), 0, 0.6))
            fin.append({
                "id": fid, "emp_id": e["emp_id"], "year_month": m,
                "income_band": inc_band, "spending_band": spend_band,
                "savings_ratio": round(savings, 3),
            })
            fid += 1

            life.append({
                "id": lid, "emp_id": e["emp_id"], "year_month": m,
                "okko_hours_band": _band(rng.random()),
                "samokat_orders_band": _band(rng.random()),
                "megamarket_band": _band(rng.random()),
            })
            lid += 1

            mob.append({
                "id": mid, "emp_id": e["emp_id"], "year_month": m,
                "trips_count": int(rng.poisson(0.2 + (0.3 if arc.leader else 0))),
                "countries_json": json.dumps(["RU"]) if rng.random() < 0.95 else json.dumps(["RU", "TR"]),
            })
            mid += 1

        # investment profile (snapshot)
        risk = "low"
        if rng.random() < 0.35:
            risk = "med"
        if rng.random() < 0.10:
            risk = "high"
        inv.append({
            "emp_id": e["emp_id"], "risk_score": risk,
            "portfolio_size_band": _band(rng.random()),
            "horizon_years": int(rng.choice([1, 3, 5, 10])),
        })

        # security flags — toxic and overworked produce slightly more
        n_flags = int(rng.poisson(0.3 + (1.0 if arc.toxic else 0) + (0.5 if arc.overworked else 0)))
        for _ in range(n_flags):
            d = date.fromisoformat(e["hire_date"]) + timedelta(days=int(rng.integers(0, 730)))
            sec.append({
                "id": sid, "emp_id": e["emp_id"],
                "ts": _iso(d) + "T08:00:00",
                "kind": str(rng.choice(["badge_anomaly", "login_anomaly", "access_denied"])),
                "severity": str(rng.choice(["info", "warning", "critical"], p=[0.6, 0.3, 0.1])),
            })
            sid += 1
    return fin, inv, life, sec, mob


def gen_collab_edges(rng: np.random.Generator, employees: list[dict]) -> list[dict[str, Any]]:
    """Barabási–Albert graph on employees, edge weights modulated by archetype.edge_density.

    Produces a connected component (BA is always connected for m≥1, n≥m+1).
    """
    n = len(employees)
    g = nx.barabasi_albert_graph(n, m=3, seed=42)
    out: list[dict[str, Any]] = []
    rid = 1
    arc_by_emp = {e["emp_id"]: A.by_name(e["archetype"]) for e in employees}
    emp_ids = [e["emp_id"] for e in employees]
    for u, v in g.edges:
        a, b = emp_ids[u], emp_ids[v]
        cap = min(arc_by_emp[a].edge_density, arc_by_emp[b].edge_density)
        weight = float(np.clip(rng.beta(2, 2) * cap + 0.05, 0.0, 1.0))
        ts = END_DATE - timedelta(days=int(rng.integers(0, 60)))
        out.append({
            "id": rid, "emp_a": a, "emp_b": b,
            "weight": round(weight, 3),
            "last_interact_ts": _iso(ts) + "T12:00:00",
        })
        rid += 1
    return out


def gen_similarity(rng: np.random.Generator, employees: list[dict],
                    units: list[dict]) -> list[dict[str, Any]]:
    """Per-employee cosine to each leaf unit. Same unit → high; cross-type → low."""
    out: list[dict[str, Any]] = []
    rid = 1
    leaves = [u for u in units if u["level"] == 2]
    for e in employees:
        for u in leaves:
            cos = 0.85 if u["unit_id"] == e["unit_id"] else float(rng.uniform(0.15, 0.65))
            out.append({
                "id": rid, "emp_id": e["emp_id"], "unit_id": u["unit_id"],
                "cosine": round(cos, 3),
                "attr_diff_json": json.dumps({"grade_diff": 0}),
            })
            rid += 1
    return out


def gen_event_participation(rng: np.random.Generator, employees: list[dict],
                             events: list[dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    rid = 1
    for ev in events:
        # Sample roughly 20-60% of employees for this event
        attend_count = int(rng.integers(20, 60))
        chosen = rng.choice(len(employees), size=attend_count, replace=False)
        for idx in chosen:
            e = employees[int(idx)]
            arc = A.by_name(e["archetype"])
            role = "attended"
            if arc.toxic and rng.random() < 0.6:
                role = "declined"
            elif arc.isolated and rng.random() < 0.7:
                role = "declined"
            elif arc.leader and rng.random() < 0.25:
                role = "speaker"
            out.append({
                "id": rid, "event_id": ev["event_id"],
                "emp_id": e["emp_id"], "role": role,
            })
            rid += 1
    return out


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------

def seed(db_path: Path, force: bool = False) -> dict[str, int]:
    """Generate the full sandbox. Returns row counts per table."""
    if db_path.exists() and not force:
        raise SystemExit(f"DB already exists at {db_path}; pass force=True (or --force).")
    if force and db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    db = Database(db_path)
    create_tables(db)

    rng = np.random.default_rng(42)
    Faker.seed(42)
    fake = Faker("ru_RU")

    # 1) static reference tables
    units = gen_units(rng, fake)
    positions = gen_positions(rng)
    courses = gen_courses(rng)
    events = gen_corp_events(rng, fake, START_DATE, END_DATE)
    db["units"].insert_all(units)
    db["positions"].insert_all(positions)
    db["courses"].insert_all(courses)
    db["corp_events"].insert_all(events)

    # 2) employees and adjacent
    employees = gen_employees(rng, fake, units, positions, START_DATE, END_DATE)
    db["employees"].insert_all(employees)
    db["family"].insert_all(gen_family(rng, employees))
    db["career_history"].insert_all(gen_career_history(rng, employees))
    db["promotions"].insert_all(gen_promotions(rng, employees))
    db["performance_reviews"].insert_all(gen_performance_reviews(rng, employees))

    # 3) feedback / learning / assessments
    db["peer_feedback"].insert_all(gen_peer_feedback(rng, employees))
    db["course_enrollments"].insert_all(gen_course_enrollments(rng, employees, courses))
    db["assessments"].insert_all(gen_assessments(rng, employees))
    db["vacations"].insert_all(gen_vacations(rng, employees))

    # 4) daily metrics — biggest tables
    activity, digital, wearables = gen_daily_metrics(rng, employees)
    db["activity_daily"].insert_all(activity, batch_size=2000)
    db["digital_patterns_daily"].insert_all(digital, batch_size=2000)
    db["wearables_daily"].insert_all(wearables, batch_size=2000)

    # 5) jira / confluence / bitbucket / branch
    db["jira_issues"].insert_all(gen_jira_issues(rng, employees, positions), batch_size=1000)
    db["confluence_pages"].insert_all(gen_confluence_pages(rng, employees), batch_size=1000)
    db["bitbucket_commits"].insert_all(gen_bitbucket_commits(rng, employees, positions), batch_size=1000)
    db["branch_tasks"].insert_all(gen_branch_tasks(rng, employees, positions), batch_size=1000)

    # 6) comm / meetings
    db["comm_style"].insert_all(gen_comm_style(rng, employees))
    art, trans = gen_meeting_artifacts(rng, employees)
    db["meeting_artifacts"].insert_all(art, batch_size=1000)
    db["vc_transcripts_summary"].insert_all(trans, batch_size=1000)

    # 7) finance / invest / lifestyle / security / mobility
    fin, inv, life, sec, mob = gen_finance_lifestyle_security_mobility(rng, employees)
    db["finance_health"].insert_all(fin, batch_size=2000)
    db["investment_profile"].insert_all(inv)
    db["lifestyle_signals"].insert_all(life, batch_size=2000)
    db["security_flags"].insert_all(sec)
    db["mobility"].insert_all(mob, batch_size=2000)

    # 8) graph + similarity + event participation
    db["collab_edges"].insert_all(gen_collab_edges(rng, employees))
    db["similarity_to_unit"].insert_all(gen_similarity(rng, employees, units))
    db["event_participation"].insert_all(gen_event_participation(rng, employees, events), batch_size=2000)

    # 9) snapshots in data/synthetic for transparency
    snap_dir = db_path.parent / "synthetic"
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / "employees.json").write_text(json.dumps(employees, ensure_ascii=False, indent=2), encoding="utf-8")
    (snap_dir / "units.json").write_text(json.dumps(units, ensure_ascii=False, indent=2), encoding="utf-8")
    (snap_dir / "positions.json").write_text(json.dumps(positions, ensure_ascii=False, indent=2), encoding="utf-8")
    (snap_dir / "archetypes.json").write_text(
        json.dumps([{"name": a.name, "share": a.share} for a in A.ARCHETYPES], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 10) seed_meta
    db["seed_meta"].insert_all([
        {"key": "seed", "value": "42"},
        {"key": "start_date", "value": _iso(START_DATE)},
        {"key": "end_date", "value": _iso(END_DATE)},
        {"key": "n_employees", "value": str(len(employees))},
        {"key": "n_terminations", "value": str(sum(1 for e in employees if e["status"] == "terminated"))},
    ])

    # build summary
    summary = {t: db[t].count for t in db.table_names()}
    log.info("seed complete: %s", summary)
    return summary


__all__ = ["seed", "END_DATE", "START_DATE"]
