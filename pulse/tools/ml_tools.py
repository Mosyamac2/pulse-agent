"""ML-prediction tools for the Pulse agent.

Wrap `pulse.data_engine.ml_predict.*` and return Russian-language
explanations. Per the TZ, JSON-in-text is forbidden — the agent expects
prose it can quote without re-formatting.
"""
from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from ..data_engine import ml_predict


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


@tool(
    "predict_attrition",
    "Получить вероятность увольнения сотрудника на горизонте 3 месяца. Возвращает probability "
    "и топ-3 фактора (приближение SHAP). Используй только для статуса 'active' — для уволенных "
    "и в декретном отпуске модель не валидна.",
    {"emp_id": str},
)
async def predict_attrition(args: dict[str, Any]) -> dict[str, Any]:
    emp_id = args.get("emp_id", "").strip()
    if not emp_id:
        return _err("emp_id обязателен.")
    try:
        out = ml_predict.predict_attrition_for_emp(emp_id)
    except Exception as ex:
        return _err(f"Ошибка предсказания: {ex}")
    factors = "; ".join(f"{f['feature']} ({f['weighted']:+.3f})" for f in out["factors"])
    text = (
        f"P(увольнение в 3 мес) для {out['emp_id']} ≈ {out['probability']*100:.1f}% "
        f"(на дату {out['ref_date']}; модель AUC={out['model_auc']:.2f}).\n"
        f"Топ-факторы: {factors}.\n"
        "Это вероятностная оценка; интерпретируй как сигнал, а не диагноз."
    )
    return _ok(text)


@tool(
    "recommend_courses",
    "Подобрать топ-5 курсов для сотрудника на основе ближайших коллег по эмбеддингу профиля "
    "(архетип + грейд + уже завершённые тематики). Уже пройденные курсы исключаются.",
    {"emp_id": str, "top_k": int},
)
async def recommend_courses(args: dict[str, Any]) -> dict[str, Any]:
    emp_id = args.get("emp_id", "").strip()
    top_k = int(args.get("top_k") or 5)
    if not emp_id:
        return _err("emp_id обязателен.")
    try:
        out = ml_predict.recommend_courses_for_emp(emp_id, top_k=top_k)
    except Exception as ex:
        return _err(f"Ошибка рекомендации: {ex}")
    if not out["recommendations"]:
        return _ok(
            f"Для {emp_id} не нашлось новых курсов: либо все из соседского кластера уже пройдены, "
            "либо у него уникальный профиль. Соседи (top-5): " + ", ".join(out["neighbours"])
        )
    body = "\n".join(
        f"  {r.get('title', r['course_id'])} ({r.get('topic', '?')}, {r.get('duration_h', '?')}ч) — "
        f"за: {r['vote_count']} голосов из соседей"
        for r in out["recommendations"]
    )
    return _ok(
        f"Рекомендации курсов для {emp_id} (на основе соседей по эмбеддингу: "
        f"{', '.join(out['neighbours'])}):\n{body}"
    )


@tool(
    "predict_role_success",
    "Спрогнозировать вероятность того, что сотрудник на конкретной позиции (position_id) получит "
    "performance score ≥ 4 в первые 6 месяцев. Учитывает разрыв в грейдах.",
    {"emp_id": str, "position_id": str},
)
async def predict_role_success(args: dict[str, Any]) -> dict[str, Any]:
    emp_id = args.get("emp_id", "").strip()
    position_id = args.get("position_id", "").strip()
    if not emp_id or not position_id:
        return _err("emp_id и position_id обязательны.")
    try:
        out = ml_predict.predict_role_success(emp_id, position_id)
    except Exception as ex:
        return _err(f"Ошибка предсказания: {ex}")
    if "note" in out and "unknown" in out["note"]:
        return _err(out["note"])
    text = (
        f"P(успех {out['emp_id']} на {out['position_id']}) ≈ {out['probability']*100:.1f}% "
        f"(grade_gap={out['grade_gap']}). {out.get('note', '')}"
    )
    return _ok(text)


__all__ = ["predict_attrition", "recommend_courses", "predict_role_success"]
