"""Read-only HR data tools for the Pulse agent.

All tools return natural-language Russian text; structured fields are
formatted as compact bullet lists. Per TZ §9.4 we avoid returning raw JSON
to the model — the agent should receive ready-to-quote prose, not data
dumps it has to re-render.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from claude_agent_sdk import tool
from sqlite_utils import Database

from ..config import PATHS


def _db() -> Database:
    return Database(PATHS.db)


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def _format_date(s: str | None) -> str:
    if not s:
        return "—"
    return s[:10]


# ---------------------------------------------------------------------------

@tool(
    "get_employee_profile",
    "Получить компактный профиль сотрудника по emp_id (имя, должность, грейд, "
    "подразделение, статус, базовые социо-демо). Используй, когда вопрос касается "
    "конкретного человека и нужен контекст «кто он такой».",
    {"emp_id": str},
)
async def get_employee_profile(args: dict[str, Any]) -> dict[str, Any]:
    emp_id = args.get("emp_id", "").strip()
    if not emp_id:
        return _err("emp_id обязателен.")
    db = _db()
    rows = list(db.query(
        """
        SELECT e.*, p.title AS pos_title, p.type AS pos_type,
               u.name AS unit_name, f.marital_status, f.kids_count
        FROM employees e
        LEFT JOIN positions p ON p.position_id = e.position_id
        LEFT JOIN units u ON u.unit_id = e.unit_id
        LEFT JOIN family f ON f.emp_id = e.emp_id
        WHERE e.emp_id = :e
        """,
        {"e": emp_id},
    ))
    if not rows:
        return _err(f"Сотрудник {emp_id} не найден.")
    e = rows[0]
    age = ""
    if e.get("birth_date"):
        try:
            d = date.fromisoformat(e["birth_date"])
            age = f", {(date.today() - d).days // 365} лет"
        except ValueError:
            age = ""
    status_ru = {
        "active": "активен",
        "terminated": f"уволен {_format_date(e.get('term_date'))}",
        "maternity": "в декретном отпуске",
        "long_sick": "на длительном больничном",
    }.get(e["status"], e["status"])

    text = (
        f"Сотрудник {e['emp_id']} — {e['full_name']} ({e['gender']}{age}).\n"
        f"  Должность: {e['pos_title']} (грейд {e['grade_level']}, тип «{e['pos_type']}»).\n"
        f"  Подразделение: {e['unit_name']} ({e['unit_id']}).\n"
        f"  Статус: {status_ru}. Принят: {_format_date(e['hire_date'])}. Архетип (внутр.): {e['archetype']}.\n"
        f"  Город: {e.get('city') or '—'}. Образование: {e.get('education') or '—'}.\n"
        f"  Семейное положение: {e.get('marital_status') or '—'}, детей: {e.get('kids_count') or 0}."
    )
    return _ok(text)


# ---------------------------------------------------------------------------

@tool(
    "get_employee_metrics",
    "Сводка метрик активности сотрудника за последние N дней (по умолчанию 30). "
    "Включает: средние tasks_done, hours_logged, focus_score, switches_per_min, "
    "stress_index, sleep_h, число встреч; среднее sentiment пир-фидбэка.",
    {"emp_id": str, "last_days": int},
)
async def get_employee_metrics(args: dict[str, Any]) -> dict[str, Any]:
    emp_id = args.get("emp_id", "").strip()
    last_days = int(args.get("last_days") or 30)
    if not emp_id:
        return _err("emp_id обязателен.")
    db = _db()
    rows = list(db.query("SELECT MAX(date) AS d FROM activity_daily WHERE emp_id=:e", {"e": emp_id}))
    if not rows or not rows[0]["d"]:
        return _err(f"Нет метрик для {emp_id}.")
    last_d = date.fromisoformat(rows[0]["d"])
    lo = (last_d - timedelta(days=last_days)).isoformat()
    hi = last_d.isoformat()

    def _avg(table: str, col: str) -> float | None:
        r = list(db.query(
            f"SELECT AVG({col}) AS m FROM {table} WHERE emp_id=:e AND date>=:lo AND date<=:hi",
            {"e": emp_id, "lo": lo, "hi": hi}))
        return r[0]["m"] if r and r[0]["m"] is not None else None

    def _fmt(x: float | None, digits: int = 2) -> str:
        return "—" if x is None else f"{x:.{digits}f}"

    pf_rows = list(db.query(
        "SELECT AVG(sentiment_score) AS m, COUNT(*) AS n FROM peer_feedback "
        "WHERE emp_id=:e AND ts>=:lo AND ts<=:hi",
        {"e": emp_id, "lo": lo, "hi": hi + "T23:59:59"}))
    pf_m = pf_rows[0]["m"] if pf_rows else None
    pf_n = pf_rows[0]["n"] if pf_rows else 0

    text = (
        f"Метрики {emp_id} за последние {last_days} дней (до {hi}):\n"
        f"  Активность: tasks_done≈{_fmt(_avg('activity_daily', 'tasks_done'), 1)}; "
        f"hours_logged≈{_fmt(_avg('activity_daily', 'hours_logged'), 1)}; "
        f"meetings≈{_fmt(_avg('activity_daily', 'meetings_count'), 1)}/день.\n"
        f"  Цифровой паттерн: focus={_fmt(_avg('digital_patterns_daily', 'focus_score'), 2)}, "
        f"switches/min={_fmt(_avg('digital_patterns_daily', 'switches_per_min'), 2)}, "
        f"working_hours={_fmt(_avg('digital_patterns_daily', 'working_hours'), 1)}.\n"
        f"  Носимые: stress={_fmt(_avg('wearables_daily', 'stress_index'), 2)}, "
        f"sleep_h={_fmt(_avg('wearables_daily', 'sleep_h'), 1)}, "
        f"steps≈{_fmt(_avg('wearables_daily', 'steps'), 0)}.\n"
        f"  Peer-feedback за период: n={pf_n}, средний sentiment={_fmt(pf_m, 2)}."
    )
    return _ok(text)


# ---------------------------------------------------------------------------

@tool(
    "list_employees",
    "Список сотрудников с фильтрами. Параметры опциональны: unit_id, archetype, "
    "status (active/terminated/maternity), limit (по умолчанию 50). Возвращает emp_id + ФИО + грейд.",
    {"unit_id": str, "archetype": str, "status": str, "limit": int},
)
async def list_employees(args: dict[str, Any]) -> dict[str, Any]:
    unit_id = args.get("unit_id") or None
    archetype = args.get("archetype") or None
    status = args.get("status") or None
    limit = int(args.get("limit") or 50)

    where = []
    params: dict[str, Any] = {"lim": limit}
    if unit_id:
        where.append("unit_id = :u")
        params["u"] = unit_id
    if archetype:
        where.append("archetype = :a")
        params["a"] = archetype
    if status:
        where.append("status = :s")
        params["s"] = status
    sql = "SELECT emp_id, full_name, grade_level, position_id, unit_id, status FROM employees"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY emp_id ASC LIMIT :lim"

    rows = list(_db().query(sql, params))
    if not rows:
        return _ok("По заданным фильтрам сотрудников не найдено.")
    body = "\n".join(
        f"  {r['emp_id']}: {r['full_name']} — грейд {r['grade_level']}, "
        f"{r['position_id']}, {r['unit_id']}, {r['status']}"
        for r in rows
    )
    return _ok(f"Найдено {len(rows)} сотрудников:\n{body}")


# ---------------------------------------------------------------------------

@tool(
    "get_collab_neighbors",
    "Получить ближайших коллег сотрудника по рабочему графу (collab_edges) с весом ≥ min_weight "
    "(по умолчанию 0.3). Полезно, когда нужно понять «с кем человек работает по факту».",
    {"emp_id": str, "min_weight": float},
)
async def get_collab_neighbors(args: dict[str, Any]) -> dict[str, Any]:
    emp_id = args.get("emp_id", "").strip()
    min_w = float(args.get("min_weight") or 0.3)
    if not emp_id:
        return _err("emp_id обязателен.")
    db = _db()
    rows = list(db.query(
        """
        SELECT CASE WHEN emp_a = :e THEN emp_b ELSE emp_a END AS peer_id, weight
        FROM collab_edges
        WHERE (emp_a = :e OR emp_b = :e) AND weight >= :w
        ORDER BY weight DESC LIMIT 30
        """,
        {"e": emp_id, "w": min_w},
    ))
    if not rows:
        return _ok(f"У {emp_id} нет связей с весом ≥ {min_w}.")
    peers = [r["peer_id"] for r in rows]
    name_rows = list(db.query(
        "SELECT emp_id, full_name FROM employees WHERE emp_id IN ({})".format(",".join("?" * len(peers))),
        peers,
    ))
    names = {r["emp_id"]: r["full_name"] for r in name_rows}
    body = "\n".join(f"  {r['peer_id']}: {names.get(r['peer_id'], '—')} (вес {r['weight']:.2f})"
                      for r in rows)
    return _ok(f"Ближайшие коллеги {emp_id} (≥ {min_w}):\n{body}")


__all__ = [
    "get_employee_profile",
    "get_employee_metrics",
    "list_employees",
    "get_collab_neighbors",
]
