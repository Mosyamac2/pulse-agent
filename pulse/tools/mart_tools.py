"""MCP tool wrappers over `pulse.data_engine.marts`.

These exist so the agent stops calling `get_employee_metrics` once per
employee in a 90-row team. Every tool here returns a single ranked or
aggregated result in one DB roundtrip. Output is Russian prose with a
compact ASCII table — no JSON dumps, per the same rule as `data_tools.py`.
"""
from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from ..data_engine.marts import (
    aggregate_metric_by,
    efficiency_ranking,
    list_metric_names,
    metric_distribution,
    metric_meta,
    top_collab_connectors,
    top_employees_by_metric,
)


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def _fmt_emp(row: dict[str, Any]) -> str:
    name = row.get("full_name") or row.get("emp_id")
    pos = row.get("position_title") or "—"
    unit = row.get("unit_name") or "—"
    return f"{name} ({row['emp_id']}) — {pos}, {unit}"


# ---------------------------------------------------------------------------

@tool(
    "top_employees_by_metric",
    "ОДНИМ запросом возвращает топ-N (или антитоп-N) сотрудников по любой "
    "метрике из реестра: tasks_done, hours_logged, meetings_count, focus_score, "
    "switches_per_min, working_hours, steps, sleep_h, stress_index, "
    "peer_sentiment. Используй ВМЕСТО цикла get_employee_metrics(emp_NNN) для "
    "вопросов 'кто больше/меньше всех X'. Параметры: metric (str), last_days "
    "(int, default 30), n (int, default 10), ascending (bool, default false — "
    "true = антитоп).",
    {"metric": str, "last_days": int, "n": int, "ascending": bool},
)
async def top_employees_by_metric_tool(args: dict[str, Any]) -> dict[str, Any]:
    metric = (args.get("metric") or "").strip()
    if not metric:
        return _err(f"metric обязательна. Доступны: {', '.join(list_metric_names())}.")
    try:
        spec = metric_meta(metric)
    except ValueError as ex:
        return _err(str(ex))
    last_days = int(args.get("last_days") or 30)
    n = int(args.get("n") or 10)
    ascending = bool(args.get("ascending"))
    rows = top_employees_by_metric(metric, last_days=last_days, n=n,
                                     ascending=ascending)
    if not rows:
        return _ok(f"Нет данных по метрике {metric} за последние {last_days} дн.")
    direction_label = "снизу" if ascending else "сверху"
    head = (f"Топ-{len(rows)} {direction_label} по метрике "
             f"`{metric}` ({spec['label']}, шкала {spec['scale']}, "
             f"направление: {spec['direction']}, окно {last_days} дн):")
    lines = [head, ""]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i:>2}. {_fmt_emp(r)} — {r['value']} (n={r['n_days']} дн)")
    return _ok("\n".join(lines))


# ---------------------------------------------------------------------------

@tool(
    "metric_distribution",
    "Распределение метрики по всем активным сотрудникам: среднее, медиана, "
    "квартили, мин/макс. Используй чтобы понять норму для роли/команды перед "
    "тем как сказать 'у этого человека высоко/низко'. Параметры: metric (str), "
    "last_days (int, default 30).",
    {"metric": str, "last_days": int},
)
async def metric_distribution_tool(args: dict[str, Any]) -> dict[str, Any]:
    metric = (args.get("metric") or "").strip()
    if not metric:
        return _err(f"metric обязательна. Доступны: {', '.join(list_metric_names())}.")
    try:
        d = metric_distribution(metric, last_days=int(args.get("last_days") or 30))
    except ValueError as ex:
        return _err(str(ex))
    if d.get("n_employees", 0) == 0:
        return _ok(f"Нет данных по метрике {metric}.")
    return _ok(
        f"Распределение `{metric}` ({d['label']}, шкала {d['scale']}, "
        f"направление: {d['direction']}) по {d['n_employees']} активным сотрудникам:\n"
        f"  среднее   = {d['mean']}\n"
        f"  медиана   = {d['p50']}\n"
        f"  квартили  = p25 {d['p25']} | p75 {d['p75']}\n"
        f"  диапазон  = {d['min']} … {d['max']}"
    )


# ---------------------------------------------------------------------------

@tool(
    "aggregate_metric_by",
    "Среднее метрики по группе (подразделение / должность / архетип / грейд). "
    "Используй для ответов вида 'в каком отделе самый высокий стресс'. "
    "Параметры: metric (str), group_by ('unit'|'position'|'archetype'|'grade'), "
    "last_days (int, default 30).",
    {"metric": str, "group_by": str, "last_days": int},
)
async def aggregate_metric_by_tool(args: dict[str, Any]) -> dict[str, Any]:
    metric = (args.get("metric") or "").strip()
    group_by = (args.get("group_by") or "unit").strip()
    last_days = int(args.get("last_days") or 30)
    if not metric:
        return _err(f"metric обязательна. Доступны: {', '.join(list_metric_names())}.")
    try:
        rows = aggregate_metric_by(metric, group_by=group_by, last_days=last_days)
    except ValueError as ex:
        return _err(str(ex))
    if not rows:
        return _ok(f"Нет данных по {metric} в разрезе {group_by}.")
    spec = metric_meta(metric)
    head = (f"Среднее `{metric}` ({spec['label']}) по группе {group_by} "
             f"за {last_days} дн (отсортировано по убыванию):")
    lines = [head, ""]
    for r in rows:
        lines.append(f"  {r.get('group_label') or r.get('group_id')}: "
                      f"{r['value']} (сотрудников: {r['n_employees']})")
    return _ok("\n".join(lines))


# ---------------------------------------------------------------------------

@tool(
    "top_collab_connectors",
    "Самые связанные сотрудники по графу collab_edges. by='weight_sum' — "
    "по суммарной интенсивности контактов (default), by='degree' — по числу "
    "уникальных партнёров. Закрывает вопрос 'кто больше всех взаимодействует "
    "с коллегами' одним запросом. Параметры: by (str), n (int, default 10).",
    {"by": str, "n": int},
)
async def top_collab_connectors_tool(args: dict[str, Any]) -> dict[str, Any]:
    by = (args.get("by") or "weight_sum").strip()
    n = int(args.get("n") or 10)
    try:
        rows = top_collab_connectors(by=by, n=n)
    except ValueError as ex:
        return _err(str(ex))
    if not rows:
        return _ok("Нет данных по коллаборациям.")
    label = "интенсивности (сумма весов)" if by == "weight_sum" else "ширине сети (число партнёров)"
    head = f"Топ-{len(rows)} самых связанных сотрудников по {label}:"
    lines = [head, ""]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i:>2}. {_fmt_emp(r)} — degree={r['degree']}, "
                      f"weight_sum={r['weight_sum']}")
    return _ok("\n".join(lines))


# ---------------------------------------------------------------------------

@tool(
    "efficiency_ranking",
    "Композитный индекс эффективности: задачи в час × качество фокуса. "
    "Формула explainable: `(tasks_done / max(hours_logged, 4)) * (0.5 + focus)`. "
    "Будни, last_days дней, ≥5 рабочих дней. Используй для вопроса "
    "'кто эффективнее/менее эффективный'. Параметры: last_days (int, default 30), "
    "n (int, default 10), ascending (bool, default false).",
    {"last_days": int, "n": int, "ascending": bool},
)
async def efficiency_ranking_tool(args: dict[str, Any]) -> dict[str, Any]:
    last_days = int(args.get("last_days") or 30)
    n = int(args.get("n") or 10)
    ascending = bool(args.get("ascending"))
    rows = efficiency_ranking(last_days=last_days, n=n, ascending=ascending)
    if not rows:
        return _ok("Не хватает данных для расчёта индекса.")
    direction_label = "наименее эффективные" if ascending else "наиболее эффективные"
    head = (f"Топ-{len(rows)}: {direction_label} за {last_days} дн "
             f"(индекс = задачи/час × фактор фокуса 0.5–1.5):")
    lines = [head, ""]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"{i:>2}. {_fmt_emp(r)} — индекс {r['score']} "
            f"(tasks/day={r['tasks_per_day']}, h/day={r['hours_per_day']}, "
            f"focus={r['focus_avg']}, n={r['n_days']} дн)"
        )
    return _ok("\n".join(lines))


# ---------------------------------------------------------------------------

@tool(
    "list_available_metrics",
    "Список метрик которые принимают top_employees_by_metric / "
    "metric_distribution / aggregate_metric_by, с человекочитаемыми "
    "названиями, шкалой и направлением (higher_is_better / lower_is_better / "
    "near_norm). Вызови первым делом если не помнишь точное имя метрики.",
    {},
)
async def list_available_metrics_tool(args: dict[str, Any]) -> dict[str, Any]:
    from ..data_engine.marts import METRIC_REGISTRY
    lines = ["Доступные метрики (имя → описание, шкала, направление):", ""]
    for name in sorted(METRIC_REGISTRY):
        s = METRIC_REGISTRY[name]
        lines.append(f"  {name:20s} — {s['label']} ({s['scale']}, {s['direction']})")
    return _ok("\n".join(lines))


__all__ = [
    "top_employees_by_metric_tool",
    "metric_distribution_tool",
    "aggregate_metric_by_tool",
    "top_collab_connectors_tool",
    "efficiency_ranking_tool",
    "list_available_metrics_tool",
]
