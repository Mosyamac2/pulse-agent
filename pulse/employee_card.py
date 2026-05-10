"""Employee passport + sparkline aggregations for chat hover-cards (v1.9.0).

Read-only over data/sber_hr.db. Two functions:

  get_employee_card(emp_id, window=30, db=None) → dict
      Hero passport for the floating card the chat shows when CEO hovers
      a name or emp_NNN: ФИО, должность, отдел, архетип, стаж + 4 ключевые
      метрики 30d с severity (норма/выше/ниже) + risk-flags + attrition
      probability (если ML-модель обучена; иначе None — карточка остаётся
      рабочей). Не падает на отсутствии данных.

  get_sparkline(emp_id, metric, window=30, db=None) → dict
      30-точечная серия для inline-визуализации в markdown-таблицах. Возвращает
      {emp_id, metric, dates: [iso], values: [float|None], min, max, mean,
       direction}. Нормализация и рендер — клиентская.

Severity calculation reuses pulse.dashboard.HEATMAP_METRICS so the colour
coding in chat matches the dashboard heatmap exactly.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from sqlite_utils import Database

from .config import PATHS
from .dashboard import (AT_RISK_FLAGS, AT_RISK_MIN_FLAGS, BURNOUT_FLAGS,
                          BURNOUT_MIN_FLAGS, HEATMAP_METRICS,
                          _employee_composite, _flag_count, _flagged_reasons)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sparkline metric registry — extends dashboard's heatmap set with a few
# common aliases so headers like "стресс" / "стресс_index" / "stress" all
# resolve to the same series.
# ---------------------------------------------------------------------------

_METRIC_ALIASES: dict[str, str] = {
    # column → canonical (matches HEATMAP_METRICS keys + a few others)
    "stress": "stress_index", "stress_index": "stress_index", "стресс": "stress_index",
    "sleep": "sleep_h", "sleep_h": "sleep_h", "сон": "sleep_h",
    "hours": "hours_logged", "hours_logged": "hours_logged", "часы": "hours_logged",
    "focus": "focus_score", "focus_score": "focus_score", "фокус": "focus_score",
    "sentiment": "peer_sentiment", "peer_sentiment": "peer_sentiment",
    "tasks": "tasks_done", "tasks_done": "tasks_done", "задачи": "tasks_done",
}

# table + column for each canonical metric
_METRIC_SOURCES: dict[str, tuple[str, str]] = {
    "stress_index":   ("wearables_daily",         "stress_index"),
    "sleep_h":        ("wearables_daily",         "sleep_h"),
    "hours_logged":   ("activity_daily",          "hours_logged"),
    "focus_score":    ("digital_patterns_daily",  "focus_score"),
    "peer_sentiment": ("peer_feedback",           "sentiment_score"),
    "tasks_done":     ("activity_daily",          "tasks_done"),
}


def resolve_metric(label: str) -> str | None:
    """Best-effort: column header / mention → canonical metric key."""
    if not label:
        return None
    key = label.strip().lower().replace(",", " ").replace(":", " ").split()[0]
    return _METRIC_ALIASES.get(key)


def _db(db: Database | None) -> Database:
    return db if db is not None else Database(PATHS.db)


# ---------------------------------------------------------------------------
# Hover-card
# ---------------------------------------------------------------------------

CARD_METRICS: list[dict[str, Any]] = [
    {"key": "stress_index",   "label": "Стресс",
     "direction": "lower_is_better",
     "tooltip": "Stress index, 0—1. Среднее по wearables за 30 дней (вариабельность HR + сон + активность). Норма ≤ 0.5; ≥ 0.65 — устойчиво высокий стресс."},
    {"key": "sleep_h",        "label": "Сон",
     "direction": "near_norm", "norm": 7.5,
     "tooltip": "Часы сна в день, среднее за 30 дней (wearables). Норма 7—8 ч; < 6.5 ч — недосып, > 9 ч — возможный пересып или болезнь."},
    {"key": "focus_score",    "label": "Фокус",
     "direction": "higher_is_better",
     "tooltip": "Focus score, 0—1. Доля рабочего времени без переключений контекста (digital_patterns). Хорошо ≥ 0.6; < 0.4 — фрагментированный день."},
    {"key": "peer_sentiment", "label": "Sentiment",
     "direction": "higher_is_better",
     "tooltip": "Усреднённая оценка от коллег в peer feedback за 30 дней, шкала −1…+1. Хорошо ≥ 0.30; < 0.0 — конфликтные сигналы, нужна аккуратность."},
]


# Russian labels for archetypes — used by sidebar and hover-card.
ARCHETYPE_RU: dict[str, str] = {
    "newbie_enthusiast":    "Новичок-энтузиаст",
    "tired_midfielder":     "Уставший середняк",
    "star_perfectionist":   "Звезда-перфекционист",
    "quiet_rear_guard":     "Тихий тыл",
    "drifting_veteran":     "Дрейфующий ветеран",
    "toxic_high_performer": "Токсичный лидер",
    "isolated_newbie":      "Одинокий новичок",
    "overwhelmed_manager":  "Перегруженный руководитель",
}


def archetype_ru(key: str | None) -> str | None:
    if not key:
        return None
    return ARCHETYPE_RU.get(key, key)


def _severity(value: float, mean: float, std: float, m: dict[str, Any]) -> float:
    if std <= 0:
        return 0.0
    z = (value - mean) / std
    if m["direction"] == "lower_is_better":
        return max(-3.0, min(3.0, -z))
    if m["direction"] == "higher_is_better":
        return max(-3.0, min(3.0, z))
    norm = m.get("norm", mean)
    return max(-3.0, min(3.0, -abs(value - norm) / std))


def get_employee_card(emp_id: str, *, window: int = 30,
                        db: Database | None = None) -> dict[str, Any] | None:
    """Hover-card payload. None when emp_id not found."""
    db = _db(db)
    rows = _employee_composite(db, days_back=window)
    me = next((r for r in rows if r["emp_id"] == emp_id), None)
    if me is None:
        # Maybe terminated — fall back to passport without metrics
        emp_rows = list(db.query("SELECT * FROM employees WHERE emp_id=:e", {"e": emp_id}))
        if not emp_rows:
            return None
        e = emp_rows[0]
        unit_row = list(db.query("SELECT name FROM units WHERE unit_id=:u", {"u": e.get("unit_id")}))
        pos_row  = list(db.query("SELECT title FROM positions WHERE position_id=:p", {"p": e.get("position_id")}))
        return {
            "emp_id": emp_id,
            "full_name": e.get("full_name") or emp_id,
            "position": pos_row[0]["title"] if pos_row else None,
            "unit_name": unit_row[0]["name"] if unit_row else None,
            "archetype": e.get("archetype"),
            "status": e.get("status"),
            "tenure_years": _tenure(e.get("hire_date")),
            "metrics": [],
            "at_risk_flags": [],
            "burnout_flags": [],
            "attrition_probability": None,
            "window_days": window,
        }

    # Population stats for severity tinting
    pop_stats: dict[str, dict[str, float]] = {}
    for m in CARD_METRICS:
        vals = [r[m["key"]] for r in rows if r.get(m["key"]) is not None]
        if not vals:
            pop_stats[m["key"]] = {"mean": 0.0, "std": 1.0}
            continue
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / max(1, len(vals) - 1)
        pop_stats[m["key"]] = {"mean": mean, "std": var ** 0.5 or 1.0}

    # Peer group: same position_id + grade_level, exclude self
    peer_group = _peer_group(db, emp_id, rows)

    metrics_out: list[dict[str, Any]] = []
    for m in CARD_METRICS:
        v = me.get(m["key"])
        peer_mean = peer_group.get("metrics", {}).get(m["key"])
        if v is None:
            metrics_out.append({"key": m["key"], "label": m["label"],
                                  "value": None, "severity": 0.0,
                                  "direction": m["direction"],
                                  "tooltip": m.get("tooltip"),
                                  "peer_mean": peer_mean})
            continue
        sev = _severity(v, pop_stats[m["key"]]["mean"], pop_stats[m["key"]]["std"], m)
        metrics_out.append({
            "key": m["key"], "label": m["label"],
            "value": round(v, 3),
            "severity": round(sev, 2),
            "direction": m["direction"],
            "tooltip": m.get("tooltip"),
            "peer_mean": peer_mean,
        })

    # Risk and burnout flags reuse dashboard thresholds
    at_flags = _flagged_reasons(me, AT_RISK_FLAGS)
    bo_flags = _flagged_reasons(me, BURNOUT_FLAGS)

    # Attrition probability — best-effort, model may be untrained
    attrition: dict[str, Any] | None = None
    try:
        from .data_engine.ml_predict import predict_attrition_for_emp
        attrition = predict_attrition_for_emp(emp_id)
    except Exception as ex:
        log.debug("attrition prediction unavailable for %s: %s", emp_id, ex)

    # Emp record for tenure/status
    emp_rows = list(db.query("SELECT hire_date, status FROM employees WHERE emp_id=:e", {"e": emp_id}))
    hire = emp_rows[0]["hire_date"] if emp_rows else None
    status = emp_rows[0]["status"] if emp_rows else "active"

    return {
        "emp_id": emp_id,
        "full_name": me.get("full_name") or emp_id,
        "position": me.get("position_title"),
        "unit_id": me.get("unit_id"),
        "unit_name": me.get("unit_name"),
        "archetype": me.get("archetype"),
        "archetype_label": archetype_ru(me.get("archetype")),
        "status": status,
        "tenure_years": _tenure(hire),
        "metrics": metrics_out,
        "peer_group": peer_group,
        "at_risk_flags": at_flags,
        "at_risk_flag_count": len(at_flags),
        "at_risk": len(at_flags) >= AT_RISK_MIN_FLAGS,
        "burnout_flags": bo_flags,
        "burnout_flag_count": len(bo_flags),
        "burnout": len(bo_flags) >= BURNOUT_MIN_FLAGS,
        "attrition_probability": (round(attrition["probability"], 3)
                                    if attrition else None),
        "attrition_factors": (attrition["factors"]
                               if attrition else None),
        "window_days": window,
    }


def _peer_group(db: Database, emp_id: str,
                  composite_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute mean of the 4 card metrics across employees with the same
    position_id and grade_level, excluding the focal employee. Returns
    {position_id, grade_level, n_peers, metrics: {key: float}}.
    """
    rows = list(db.query(
        "SELECT position_id, grade_level FROM employees WHERE emp_id = :e",
        {"e": emp_id},
    ))
    if not rows:
        return {"position_id": None, "grade_level": None, "n_peers": 0, "metrics": {}}
    pos = rows[0]["position_id"]
    grade = rows[0]["grade_level"]

    peer_ids = {r["emp_id"] for r in db.query(
        "SELECT emp_id FROM employees "
        "WHERE position_id = :p AND grade_level = :g "
        "AND status = 'active' AND emp_id != :e",
        {"p": pos, "g": grade, "e": emp_id},
    )}
    if not peer_ids:
        return {"position_id": pos, "grade_level": grade, "n_peers": 0, "metrics": {}}

    sums: dict[str, float] = {m["key"]: 0.0 for m in CARD_METRICS}
    counts: dict[str, int] = {m["key"]: 0 for m in CARD_METRICS}
    for r in composite_rows:
        if r["emp_id"] not in peer_ids:
            continue
        for m in CARD_METRICS:
            v = r.get(m["key"])
            if v is None:
                continue
            sums[m["key"]] += v
            counts[m["key"]] += 1
    means = {k: round(sums[k] / counts[k], 3)
             for k in sums if counts[k] > 0}
    return {
        "position_id": pos,
        "grade_level": grade,
        "n_peers": len(peer_ids),
        "metrics": means,
    }


def _tenure(hire_date: str | None) -> float | None:
    if not hire_date:
        return None
    try:
        h = date.fromisoformat(hire_date)
    except ValueError:
        return None
    today = datetime.now().date()
    return round((today - h).days / 365.25, 1)


# ---------------------------------------------------------------------------
# Sparkline
# ---------------------------------------------------------------------------

def get_sparkline(emp_id: str, metric: str, *, window: int = 30,
                    db: Database | None = None) -> dict[str, Any] | None:
    """Daily series for `metric` over `window` days. None if metric unknown."""
    canonical = resolve_metric(metric) or metric
    if canonical not in _METRIC_SOURCES:
        return None
    db = _db(db)
    table, col = _METRIC_SOURCES[canonical]
    date_col = "ts" if table == "peer_feedback" else "date"

    sql = f"""
      SELECT date({date_col}) AS d, AVG({col}) AS v
      FROM {table}
      WHERE emp_id = :e
        AND date({date_col}) >= date((SELECT MAX({date_col}) FROM {table}), '-{int(window)} day')
      GROUP BY date({date_col})
      ORDER BY date({date_col})
    """
    rows = list(db.query(sql, {"e": emp_id}))
    if not rows:
        return {"emp_id": emp_id, "metric": canonical,
                "dates": [], "values": [], "min": None, "max": None,
                "mean": None, "n": 0,
                "direction": _direction_for(canonical)}

    dates = [r["d"] for r in rows]
    values = [round(float(r["v"]), 4) if r["v"] is not None else None for r in rows]
    real = [v for v in values if v is not None]
    return {
        "emp_id": emp_id,
        "metric": canonical,
        "dates": dates,
        "values": values,
        "min": round(min(real), 4) if real else None,
        "max": round(max(real), 4) if real else None,
        "mean": round(sum(real) / len(real), 4) if real else None,
        "n": len(values),
        "direction": _direction_for(canonical),
    }


def _direction_for(canonical: str) -> str:
    for m in HEATMAP_METRICS + CARD_METRICS:
        if m["key"] == canonical:
            return m["direction"]
    return "near_norm"


__all__ = [
    "CARD_METRICS",
    "ARCHETYPE_RU",
    "archetype_ru",
    "resolve_metric",
    "get_employee_card",
    "get_sparkline",
]
