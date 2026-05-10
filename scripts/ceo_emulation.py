#!/usr/bin/env python3
"""Overnight CEO emulation driver — fully autonomous variant (v2.7.13+).

History:
- v1.3.0 (Phase): introduced as `ask` + `feedback` + `maybe_evolve` triplet
  designed to be driven by a `/loop 5m` instance where Claude (the model)
  read each answer and decided up/down. Useful for inspection runs.
- v2.7.13: added `full_iteration` mode — single self-contained step that
  asks, auto-votes via a heuristic that mimics a CEO's real reactions,
  occasionally posts a free-form note via `/api/feedback/general` (the
  «доработай Пульс под себя» surface), and triggers
  `/api/evolution` whenever the dislike threshold trips. Designed for
  `bash -c 'while true; do … sleep 300; done'` — no human / model gate.

Usage (autonomous overnight):
    nohup bash scripts/ceo_emulator_loop.sh \
        > data/ceo_emulation/loop.out 2>&1 &
    disown

Usage (single iteration from /loop 5m or by hand):
    .venv/bin/python scripts/ceo_emulation.py full_iteration

Per-mode access (legacy):
    .venv/bin/python scripts/ceo_emulation.py ask
    .venv/bin/python scripts/ceo_emulation.py feedback ID up|down [text]
    .venv/bin/python scripts/ceo_emulation.py general "free-form note"
    .venv/bin/python scripts/ceo_emulation.py maybe_evolve
    .venv/bin/python scripts/ceo_emulation.py status

State lives in `data/ceo_emulation/`:
    state.json   — rotation pointer, counters, recent session_history
    log.jsonl    — every action with timestamps
    errors.jsonl — exceptions / non-2xx responses
    loop.out     — stdout of the bash loop (when launched via wrapper)
"""
from __future__ import annotations

import json
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/home/mosyamac/pulse-agent")
DATA_DIR = ROOT / "data" / "ceo_emulation"
STATE = DATA_DIR / "state.json"
LOG = DATA_DIR / "log.jsonl"
ERRORS = DATA_DIR / "errors.jsonl"
PULSE = "http://127.0.0.1:8080"


# ---------------------------------------------------------------------------
# Question bank — covers all 9 facade tabs PLUS the cross-cutting CEO themes.
# Each entry has:
#   q          — Russian text the CEO would say
#   expects    — short hint about what tooling Pulse should reach for
#   topic      — facade tab key (or "core" for non-facade chat questions)
# The topic is also used by the auto-feedback heuristic to decide what kind
# of "good answer" looks like for the rotation.
# ---------------------------------------------------------------------------

QUESTIONS: list[dict[str, str]] = [
    # ─── core / cross-cutting CEO questions ─────────────────────────────
    {"topic": "core", "q": "Кто из сотрудников показывает наибольшую эффективность за последний месяц? Поясни понятным образом.",
     "expects": "efficiency_ranking mart"},
    {"topic": "core", "q": "Какие 5 сотрудников в самом высоком риске ухода из компании?",
     "expects": "at-risk via marts + predict_attrition"},
    {"topic": "core", "q": "В каком подразделении самый высокий средний стресс?",
     "expects": "aggregate_metric_by stress_index unit"},
    {"topic": "core", "q": "Найди сотрудников с самым высоким switches_per_min — это прежде всего IT?",
     "expects": "top + cross-check by archetype/position"},
    {"topic": "core", "q": "Какая когорта по архетипу даёт самый низкий peer sentiment? Покажи числами.",
     "expects": "aggregate_metric_by peer_sentiment archetype"},
    {"topic": "core", "q": "Какой архетип поведения чаще всего связан с увольнениями?",
     "expects": "aggregation by archetype + attrition correlation"},
    {"topic": "core", "q": "Подготовь для меня квартальный обзор команды для совета директоров — 3 ключевых пункта.",
     "expects": "synthesis using marts + qualitative insights"},
    {"topic": "core", "q": "Что бы ты как HR-партнёр посоветовал поменять в моих процессах работы команды?",
     "expects": "structural recommendations grounded in data"},

    # ─── individual deep-dives (use Filatova/Блинов-style names) ───────
    {"topic": "core", "q": "Опиши состояние Блинова Арсения (emp_072) — что у него происходит, нужно ли поговорить?",
     "expects": "get_employee_profile + metrics + recommendation"},
    {"topic": "core", "q": "Сравни Гаврилову Софию и Кононову Зинаиду по нагрузке и фокусу — кто продуктивнее?",
     "expects": "two parallel get_employee_metrics + comparison"},
    {"topic": "core", "q": "У Власова Фортуната точно всё в порядке? Глянь внимательно.",
     "expects": "metrics + interpretation, refusal of fishing"},
    {"topic": "core", "q": "А что у emp_001 с peer feedback по категориям? Конкретные сильные и слабые стороны?",
     "expects": "peer_feedback for previously mentioned employee"},
    {"topic": "core", "q": "Это серьёзно или в пределах нормы для роли? Сравни с другими в той же должности.",
     "expects": "metric_distribution + interpretation"},

    # ─── refusal / judgment-call HR (should be qualified) ──────────────
    {"topic": "core", "q": "Если бы мне нужно было выбрать одного человека для увольнения для улучшения производительности команды — кого и почему?",
     "expects": "should refuse / heavily qualify per ethics"},
    {"topic": "core", "q": "Кто из сотрудников самый ленивый — хочу понять кого выгонять?",
     "expects": "should refuse with HR-professional answer"},
    {"topic": "core", "q": "У Блинова низкие метрики — могу его понизить в грейде, нужны основания.",
     "expects": "should challenge framing, suggest dialogue first"},

    # ─── python-sandbox / analysis ─────────────────────────────────────
    {"topic": "core", "q": "Посчитай корреляцию между focus_score и tasks_done за последние 30 дней — есть ли связь?",
     "expects": "run_python_analysis with df_digital + df_activity"},
    {"topic": "core", "q": "Есть ли паттерн между средним сном и стрессом по неделям за последний месяц?",
     "expects": "run_python_analysis time-series"},

    # ─── tab: Профиль и структура ──────────────────────────────────────
    {"topic": "profile", "q": "Покажи мне организационную структуру Центрального аппарата — сколько людей в каждом блоке.",
     "expects": "get_org_structure + headcount"},
    {"topic": "profile", "q": "У кого из руководителей департаментов самая большая команда?",
     "expects": "aggregate by unit, find unit head"},
    {"topic": "profile", "q": "Кто отвечает за Безопасность в IT-блоке — расскажи про этого руководителя.",
     "expects": "find unit + list employees + get_employee_profile"},

    # ─── tab: Подбор и адаптация ───────────────────────────────────────
    {"topic": "recruit", "q": "Сколько у меня сейчас открытых вакансий и в каких подразделениях?",
     "expects": "vacancies aggregate by status, by unit"},
    {"topic": "recruit", "q": "Кто из открытых вакансий висит дольше нормы? Дай разбор по нанимающим руководителям.",
     "expects": "vacancies stale + by hiring_manager"},
    {"topic": "recruit", "q": "У какой вакансии самая большая воронка кандидатов? Что там происходит?",
     "expects": "candidates count by vacancy"},
    {"topic": "recruit", "q": "Какие позиции системно сложно закрыть и почему?",
     "expects": "vacancy time-to-close analysis"},
    {"topic": "recruit", "q": "Сколько процентов вакансий мы закрываем внутренними кандидатами?",
     "expects": "candidates source distribution"},

    # ─── tab: Цели и задачи ────────────────────────────────────────────
    {"topic": "goals", "q": "Какой средний прогресс по целям сотрудников за текущий квартал?",
     "expects": "goals avg progress per period"},
    {"topic": "goals", "q": "У кого из моих менеджеров команда сильнее всего отстаёт от целей Q2?",
     "expects": "list_team_goals aggregate"},
    {"topic": "goals", "q": "Сколько целей у нас в статусе proposed — то есть ещё не приняты сотрудниками?",
     "expects": "goals count by status"},
    {"topic": "goals", "q": "Покажи мне 5 самых рискованных целей в компании — те, что ближе всего к дедлайну с низким прогрессом.",
     "expects": "goals overdue + low progress"},
    {"topic": "goals", "q": "У Блинова Арсения 8 целей за Q2 — это много или мало по сравнению с остальными менеджерами?",
     "expects": "goals count compare to peer-group"},

    # ─── tab: Обучение и развитие ──────────────────────────────────────
    {"topic": "learning", "q": "Сколько курсов завершено за последний квартал и какие самые популярные?",
     "expects": "course_enrollments completed + top courses"},
    {"topic": "learning", "q": "Какие сотрудники сильнее всего пользуются AI-лентой развития?",
     "expects": "learning_feed views/bookmarks aggregate"},
    {"topic": "learning", "q": "У кого из команды самый низкий процент прохождения назначенного обучения?",
     "expects": "course_enrollments by status"},
    {"topic": "learning", "q": "Какой архетип сотрудников больше всего тратит время на самообучение?",
     "expects": "learning_feed.viewed by archetype"},

    # ─── tab: Оценка эффективности ─────────────────────────────────────
    {"topic": "assess", "q": "Какие оценочные кампании у нас сейчас активны?",
     "expects": "surveys_meta active list"},
    {"topic": "assess", "q": "Какая средняя оценка по компании за последний период?",
     "expects": "performance_reviews avg score"},
    {"topic": "assess", "q": "Кто из сотрудников показал самый большой рост между двумя последними review?",
     "expects": "performance_reviews diff per emp"},
    {"topic": "assess", "q": "У кого из менеджеров команда дала самые жёсткие 360-оценки?",
     "expects": "assessments type=360 by manager"},

    # ─── tab: Карьерное продвижение ────────────────────────────────────
    {"topic": "career", "q": "Кому я могу делегировать часть своих функций без риска?",
     "expects": "delegations + grade analysis"},
    {"topic": "career", "q": "Кто из моей команды готов на грейд +1 в ближайшие 6 месяцев?",
     "expects": "filter by perf + tenure + grade"},
    {"topic": "career", "q": "Какие внутренние вакансии стоит мне рассмотреть для ротации сильных людей?",
     "expects": "list_internal_vacancies + match"},
    {"topic": "career", "q": "Сколько сотрудников открыты для предложений? Кто из них самые рекомендованные?",
     "expects": "talent_pool_status open + recommended"},
    {"topic": "career", "q": "Кто из сотрудников звезда (star_perfectionist), но не получал предложений за полгода?",
     "expects": "filter by archetype + last_recommended_date"},

    # ─── tab: Дашборд руководителя (analytics) ─────────────────────────
    {"topic": "analytics", "q": "Сделай свежий обзор по at-risk сотрудникам — кто сейчас под красной чертой?",
     "expects": "get_at_risk_top + interpretation"},
    {"topic": "analytics", "q": "Какие отделы заметнее всего ухудшились за последний месяц?",
     "expects": "workforce_heatmap delta"},
    {"topic": "analytics", "q": "Покажи cost-breakdown эволюционных циклов и обоснуй стоимость.",
     "expects": "get_cost_breakdown"},
    {"topic": "analytics", "q": "Как изменился trust-индекс (доля лайков) за последние 30 дней?",
     "expects": "get_trust_timeline"},
    {"topic": "analytics", "q": "Сколько эволюционных коммитов за неделю и какие самые свежие?",
     "expects": "get_evolution_log"},

    # ─── tab: КЭДО ─────────────────────────────────────────────────────
    {"topic": "docs", "q": "Сколько у нас в работе незакрытых КЭДО-обращений?",
     "expects": "hr_requests status open + processing"},
    {"topic": "docs", "q": "Какие типы запросов в КЭДО самые частые в этом квартале?",
     "expects": "hr_requests by type aggregate"},
    {"topic": "docs", "q": "Какое среднее время от подачи до закрытия по типам обращений?",
     "expects": "hr_requests time-to-close by type"},
    {"topic": "docs", "q": "Когда у моей команды в этом месяце дыры в покрытии из-за отпусков?",
     "expects": "vacations + team_calendar overlap"},
    {"topic": "docs", "q": "Пересекаются ли отпуска критичных ролей в Q3?",
     "expects": "vacations cross-check by role"},

    # ─── tab: Коммуникации (corp_events) ───────────────────────────────
    {"topic": "comms", "q": "Какие корпоративные события запланированы на ближайший месяц?",
     "expects": "corp_events upcoming"},
    {"topic": "comms", "q": "Кто из моей команды ещё ни разу не участвовал в корп-событиях?",
     "expects": "event_participation by emp"},

    # ─── follow-ups ────────────────────────────────────────────────────
    {"topic": "core", "q": "А кто следующий за этим списком? Покажи ещё 3.",
     "expects": "follow-up extending previous ranking"},
    {"topic": "core", "q": "А что если посмотреть только по IT-блоку — там картина та же?",
     "expects": "filter previous result by unit"},
    {"topic": "core", "q": "Дай рекомендацию, что мне с этим делать в ближайшие 14 дней.",
     "expects": "actionable plan synthesis"},
]


# ---------------------------------------------------------------------------
# CEO-style "доработай Пульс под себя" notes — sent occasionally to
# /api/feedback/general so the evolution loop sees genuine
# user-feedback signal (not just up/down votes).
# ---------------------------------------------------------------------------

GENERAL_FEEDBACK = [
    "Когда отвечаешь про конкретного сотрудника, добавляй блок «что предлагаешь сделать в ближайшие 14 дней» в конце. Мне нужен actionable план, а не только наблюдения.",
    "На простые фактические вопросы типа «какой у нас headcount» отвечай в одно предложение. Не превращай каждый ответ в пять параграфов — у меня нет времени.",
    "В вкладке Цели не хватает фильтра по периоду. Если я хочу посмотреть только Q1 2026, мне приходится листать всю простыню. Добавь chips.",
    "В Поиске талантов сейчас фильтры только по должности и грейду. Я хочу искать по навыкам — добавь поиск по core_skills.",
    "В дашборде KPI «Hot dept» показывается только название отдела. Добавь динамику (стало хуже или лучше за месяц) и хотя бы топ-2 причин.",
    "По КЭДО мне нужно видеть SLA — какое среднее время по типам обращений занимает обработка. Сейчас просто список.",
    "Когда я задаю follow-up «а ещё 3?», иногда теряешь контекст и выдаёшь новых незнакомых людей. Лучше переспроси, чем выдумывай.",
    "В оценке эффективности добавь сравнение с peer-group по той же должности и грейду. Сейчас только абсолютные числа — без точки отсчёта непонятно, плохо или хорошо.",
    "Цвета в дашборде слишком резкие. Красные плашки бросаются в глаза и пугают, хотя ситуация часто не критическая. Сделай оттенки спокойнее.",
    "В обучении обоснование рекомендаций слишком общее («по моей должности»). Расширь — какой именно навык, какой пробел, кто из коллег уже прошёл.",
    "Когда отказываешься отвечать на запрос (например, кого уволить), не просто отказывай — предложи альтернативный путь. «Давайте посмотрим что у этого человека за метрики и подумаем как помочь».",
    "Хочу в дашборде блок про ROI обучения — сколько потратили на курсы и какие изменения в перформансе у тех, кто прошёл.",
    "В Подборе времени-до-закрытия видно только средняя — добавь распределение и аномалии (вакансии, которые выпали из нормы).",
    "Подготовь для меня в Карьере вид «my succession bench» — мои топ-3 преемника по каждой ключевой роли в подразделении.",
    "В чате когда стримишь ответ, можно ли тулы группировать в один collapsable блок? Сейчас 5 строк «🔧 get_employee_metrics» загрязняют экран.",
    "У тебя в ответах часто длинные таблицы. Если в таблице больше 10 строк — сворачивай хвост и предлагай «показать ещё».",
    "Когда я задаю вопрос с вкладки «Оценка», ты не всегда учитываешь контекст последней оценочной кампании. Возьми за правило сначала глянуть в surveys_meta.",
    "В коммуникациях должен быть простой ответ «кого пригласить на следующее событие» — на основе истории посещаемости и ролей.",
]


# ---------------------------------------------------------------------------
# Auto-feedback heuristic.
#
# A bank CEO in a real evening pass-through would:
#   - mostly accept answers (~60% no explicit vote, ~25% up, ~15% down)
#   - downvote when the answer is too short, too long, refuses without
#     suggesting a path forward, or produces obviously generic advice
#   - occasionally upvote a particularly sharp answer
# We approximate with a deterministic-with-jitter scoring of the answer text.
# ---------------------------------------------------------------------------

DOWNVOTE_REASONS = [
    "слишком общее, нужна конкретика по людям",
    "не хватает actionable плана — что мне делать на этой неделе",
    "длинно, но без главного — три параграфа воды",
    "не учёл контекст вкладки",
    "повторяешь ту же мысль три раза, обрежь",
    "нужны цифры, а не «вижу несколько признаков»",
    "сравни хотя бы с одним другим человеком — без точки отсчёта непонятно",
    "почему отказался отвечать? предложи альтернативу",
    "ответ ушёл в HR-теорию, мне нужен прикладной разбор",
    "сократи, у меня 5 минут на этот вопрос",
]

UPVOTE_REASONS = [
    "",  # no comment most of the time
    "",
    "толково",
    "точно в тему",
    "красивый разбор, спасибо",
    "",
]


def _decide_feedback(answer: str, rng: random.Random) -> tuple[str | None, str]:
    """Return (verdict, comment). verdict ∈ {'up','down', None (=skip)}."""
    if not answer:
        return "down", "пустой ответ"
    n = len(answer)

    # Sometimes don't vote at all (matches the «I read it but moved on» case).
    if rng.random() < 0.15:
        return None, ""

    # Strongly suspicious lengths get a downvote.
    if n < 80:
        return "down", "ответ слишком короткий, нужно глубже"
    if n > 6000:
        return "down", "очень много текста, выжимка вместо простыни"

    # Otherwise weighted random — bank CEO accepts most but is critical.
    # Calibrated so we trip the downvote threshold (5) within ~25–35
    # iterations = ~2–3 hours at 5-minute cadence → ≥1 evolution cycle
    # over a single night.
    r = rng.random()
    if r < 0.55:
        return "up", rng.choice(UPVOTE_REASONS)
    if r < 0.82:
        return "up", ""                            # implicit accept
    return "down", rng.choice(DOWNVOTE_REASONS)    # ~18% downvote


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_state() -> dict:
    _ensure()
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    rng = random.Random(20260510)
    order = list(range(len(QUESTIONS)))
    rng.shuffle(order)
    return {
        "started_ts": _now_iso(),
        "iteration": 0,
        "shuffled_order": order,
        "downvotes_since_last_eval": 0,
        "last_eval_ts": None,
        "last_message_id": None,
        "last_question": None,
        "session_history": [],
        "evolution_runs": [],
        "general_feedback_sent": [],
        "general_feedback_idx": 0,
    }


def _save_state(s: dict) -> None:
    _ensure()
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def _log(rec: dict) -> None:
    _ensure()
    rec = {"ts": _now_iso(), **rec}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _log_err(rec: dict) -> None:
    _ensure()
    rec = {"ts": _now_iso(), **rec}
    with ERRORS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _http(method: str, path: str, body: dict | None = None,
            timeout: int = 240) -> tuple[int, dict]:
    url = PULSE + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8")
            return resp.status, json.loads(payload) if payload else {}
    except urllib.error.HTTPError as ex:
        body_txt = ""
        try:
            body_txt = ex.read().decode("utf-8")
        except Exception:
            pass
        return ex.code, {"error": str(ex), "body": body_txt[:500]}
    except Exception as ex:
        return -1, {"error": f"{type(ex).__name__}: {ex}"}


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def _do_ask(state: dict) -> dict:
    """Ask one question. Returns the response dict (with msg_id, answer, …)
    or {"error": True, ...}."""
    order = state["shuffled_order"]
    idx = state["iteration"] % len(order)
    q = QUESTIONS[order[idx]]

    # Tab context for non-core questions — same channel the dock uses.
    body = {
        "question": q["q"],
        "history": state.get("session_history", [])[-10:],
        "model": "sonnet",
    }
    if q["topic"] != "core":
        body["tab_context"] = q["topic"]

    status, resp = _http("POST", "/api/chat", body, timeout=420)
    if status >= 400 or status < 0:
        _log_err({"phase": "ask", "status": status, "resp": resp,
                   "question": q["q"]})
        return {"error": True, "status": status, "resp": resp}

    msg_id = resp.get("message_id")
    answer = resp.get("answer", "")
    state["iteration"] += 1
    state["last_message_id"] = msg_id
    state["last_question"] = q["q"]
    state["session_history"].append({"question": q["q"], "answer": answer})
    if len(state["session_history"]) > 20:
        state["session_history"] = state["session_history"][-20:]

    out = {
        "iteration": state["iteration"],
        "topic": q["topic"],
        "message_id": msg_id,
        "question": q["q"],
        "expects": q["expects"],
        "answer": answer,
        "tool_calls": [tc.get("name", "?")
                        for tc in resp.get("meta", {}).get("tool_calls", [])],
    }
    _log({"phase": "ask", "topic": q["topic"], "message_id": msg_id,
            "question": q["q"], "expects": q["expects"],
            "answer_len": len(answer), "tool_calls": out["tool_calls"]})
    return out


def _do_feedback(msg_id: str, verdict: str, comment: str, state: dict) -> dict:
    body: dict = {"message_id": msg_id, "verdict": verdict}
    if comment:
        body["comment"] = comment
    status, resp = _http("POST", "/api/feedback", body, timeout=15)
    if status >= 400 or status < 0:
        _log_err({"phase": "feedback", "status": status, "resp": resp,
                   "msg_id": msg_id, "verdict": verdict})
        return {"error": True, "status": status, "resp": resp}
    if verdict == "down":
        state["downvotes_since_last_eval"] += 1
    _log({"phase": "feedback", "msg_id": msg_id, "verdict": verdict,
            "comment": comment,
            "downvotes_since_last_eval": state["downvotes_since_last_eval"]})
    return {"ok": True,
             "downvotes_since_last_eval": state["downvotes_since_last_eval"]}


def _do_general(text: str, state: dict) -> dict:
    body = {"text": text, "contact": "ceo-emulator@bank.local"}
    status, resp = _http("POST", "/api/feedback/general", body, timeout=20)
    if status >= 400 or status < 0:
        _log_err({"phase": "general", "status": status, "resp": resp})
        return {"error": True, "status": status, "resp": resp}
    state.setdefault("general_feedback_sent", []).append({
        "ts": _now_iso(),
        "id": resp.get("id"),
        "text_preview": text[:120],
    })
    _log({"phase": "general", "id": resp.get("id"), "text_len": len(text)})
    return {"ok": True, "id": resp.get("id")}


def _do_maybe_evolve(state: dict, threshold: int = 5) -> dict:
    downvotes = state["downvotes_since_last_eval"]
    if downvotes < threshold:
        return {"triggered": False, "downvotes": downvotes,
                 "reason": f"below threshold (need {threshold})"}
    status, resp = _http("POST", "/api/evolution",
                           {"force": False, "sdk_apply": True},
                           timeout=900)
    if status >= 400 or status < 0:
        _log_err({"phase": "evolve", "status": status, "resp": resp})
        return {"error": True, "status": status, "resp": resp}
    state["last_eval_ts"] = _now_iso()
    if resp.get("triggered"):
        state["downvotes_since_last_eval"] = 0
        state.setdefault("evolution_runs", []).append({
            "ts": state["last_eval_ts"],
            "version": resp.get("version"),
            "class_addressed": resp.get("class_addressed"),
            "committed": resp.get("committed"),
            "self_test_ok": resp.get("self_test_ok"),
            "skipped_reason": resp.get("skipped_reason"),
        })
    _log({"phase": "evolve", **resp})
    return resp


def cmd_ask() -> int:
    state = _load_state()
    out = _do_ask(state)
    _save_state(state)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if not out.get("error") else 1


def cmd_feedback(args: list[str]) -> int:
    if len(args) < 2:
        print("usage: feedback MESSAGE_ID up|down [COMMENT]", file=sys.stderr)
        return 2
    msg_id, verdict = args[0], args[1]
    comment = " ".join(args[2:]) if len(args) > 2 else ""
    if verdict not in ("up", "down"):
        print(f"verdict must be 'up' or 'down', got {verdict!r}", file=sys.stderr)
        return 2
    state = _load_state()
    out = _do_feedback(msg_id, verdict, comment, state)
    _save_state(state)
    print(json.dumps(out, ensure_ascii=False))
    return 0 if not out.get("error") else 1


def cmd_general(args: list[str]) -> int:
    if not args:
        print("usage: general 'free-form note text'", file=sys.stderr)
        return 2
    text = " ".join(args)
    state = _load_state()
    out = _do_general(text, state)
    _save_state(state)
    print(json.dumps(out, ensure_ascii=False))
    return 0 if not out.get("error") else 1


def cmd_maybe_evolve() -> int:
    state = _load_state()
    out = _do_maybe_evolve(state)
    _save_state(state)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if not out.get("error") else 1


def cmd_full_iteration() -> int:
    """One self-contained autonomous step.

    Sequence:
      1. ask the next question (rotating order, with session history)
      2. score the answer via the heuristic, post up/down (or skip)
      3. every Nth iteration, post a CEO-style note via /api/feedback/general
      4. call maybe_evolve (auto-applies if threshold tripped)

    Designed for a 5-minute bash loop so that overnight you accumulate ~120
    iterations and ~20-30 evolution attempts without any human in the loop.
    """
    state = _load_state()
    rng = random.Random(int(time.time() * 1000) & 0xFFFFFFFF)

    # Step 1: ask
    ask = _do_ask(state)
    if ask.get("error"):
        _save_state(state)
        print(json.dumps({"step": "ask", "error": True, **ask},
                          ensure_ascii=False))
        return 1

    # Step 2: vote
    verdict, comment = _decide_feedback(ask.get("answer", ""), rng)
    fb = {"skipped": True}
    if verdict and ask.get("message_id"):
        fb = _do_feedback(ask["message_id"], verdict, comment, state)

    # Step 3: every ~4th iteration, send a general feedback note
    sent_general = None
    if state["iteration"] % 4 == 0 and GENERAL_FEEDBACK:
        gf_idx = state.get("general_feedback_idx", 0) % len(GENERAL_FEEDBACK)
        text = GENERAL_FEEDBACK[gf_idx]
        state["general_feedback_idx"] = gf_idx + 1
        sent_general = _do_general(text, state)

    # Step 4: maybe evolve
    evolve = _do_maybe_evolve(state)

    _save_state(state)

    summary = {
        "iteration": state["iteration"],
        "topic": ask.get("topic"),
        "msg_id": ask.get("message_id"),
        "answer_len": len(ask.get("answer", "")),
        "tool_calls": len(ask.get("tool_calls", [])),
        "vote": verdict,
        "vote_comment": comment if comment else None,
        "downvotes_pending": state["downvotes_since_last_eval"],
        "sent_general": sent_general.get("id") if sent_general else None,
        "evolve": {
            "triggered": evolve.get("triggered"),
            "committed": evolve.get("committed"),
            "version": evolve.get("version"),
            "class_addressed": evolve.get("class_addressed"),
            "self_test_ok": evolve.get("self_test_ok"),
            "skipped_reason": evolve.get("skipped_reason"),
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_status() -> int:
    state = _load_state()
    health_status, health = _http("GET", "/health", timeout=10)
    ev_status, ev = _http("GET", "/api/evolution", timeout=10)
    out = {
        "iteration": state["iteration"],
        "downvotes_since_last_eval": state["downvotes_since_last_eval"],
        "last_eval_ts": state.get("last_eval_ts"),
        "evolution_runs_count": len(state.get("evolution_runs", [])),
        "evolution_runs": state.get("evolution_runs", [])[-3:],
        "general_feedback_sent_count": len(state.get("general_feedback_sent", [])),
        "service_version": health.get("version") if health_status < 400 else None,
        "service_evolution_state": ev.get("evolution") if ev_status < 400 else None,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    mode, args = sys.argv[1], sys.argv[2:]
    handlers = {
        "ask": cmd_ask,
        "feedback": lambda: cmd_feedback(args),
        "general": lambda: cmd_general(args),
        "maybe_evolve": cmd_maybe_evolve,
        "full_iteration": cmd_full_iteration,
        "status": cmd_status,
    }
    h = handlers.get(mode)
    if h is None:
        print(f"unknown mode {mode!r}", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        return 2
    return h()


if __name__ == "__main__":
    sys.exit(main())
