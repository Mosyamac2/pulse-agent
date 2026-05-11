#!/usr/bin/env python3
"""Overnight CEO emulator — adaptive (Haiku 4.5-driven) variant (v2.7.14+).

Earlier versions (v1.3 → v2.7.13) rotated through a fixed bank of 59 questions
and decided up/down votes via a static heuristic. That worked, but produced
recognizable patterns and lots of duplicate complaints. v2.7.14 replaces both
with a Haiku 4.5 generator that:

  - Reads the recent dialogue and decides what the CEO would say next.
    Can be a fresh question, a follow-up on the previous answer, a complaint,
    or a probe of Pulse's judgement. Topic rotates across the 9 façade tabs +
    core HR questions; the model is told NOT to repeat recent topics verbatim.
  - Scores each answer as the CEO would — vote (up / down / skip) + a short
    Russian comment in CEO voice.
  - Every Nth iteration produces a free-form «доработай Пульс под себя»
    note based on what the CEO has observed in this session.

OAuth-only — uses pulse.llm._query_simple with model='haiku', which goes
through the same CLAUDE_CODE_OAUTH_TOKEN as the rest of Pulse. No API keys.

Usage:
    nohup bash scripts/ceo_emulator_loop.sh \
        > data/ceo_emulation/loop.out 2>&1 &
    disown

Per-mode access:
    .venv/bin/python scripts/ceo_emulation.py full_iteration
    .venv/bin/python scripts/ceo_emulation.py status
    .venv/bin/python scripts/ceo_emulation.py maybe_evolve

State lives in `data/ceo_emulation/`:
    state.json   — iteration counter, recent_topics, session_history,
                   evolution_runs, downvote tally
    log.jsonl    — every action with timestamps
    errors.jsonl — exceptions / non-2xx responses
    loop.out     — stdout of the bash loop (when launched via wrapper)
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Make `from pulse.llm import _query_simple` work when run as a script.
ROOT = Path("/home/mosyamac/pulse-agent")
sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data" / "ceo_emulation"
STATE = DATA_DIR / "state.json"
LOG = DATA_DIR / "log.jsonl"
ERRORS = DATA_DIR / "errors.jsonl"
PULSE = "http://127.0.0.1:8080"

# ── Topic palette — the 9 façade tabs plus the cross-cutting core HR scope.
TOPICS = [
    "core",       # employee deep-dives, cross-cutting metrics, refusal-cases
    "profile",    # «Профиль и структура»
    "recruit",    # «Подбор и адаптация»
    "goals",      # «Цели и задачи»
    "learning",   # «Обучение и развитие»
    "assess",     # «Оценка эффективности»
    "career",     # «Карьерное продвижение»
    "analytics",  # «Дашборд руководителя»
    "docs",       # «КЭДО»
    "comms",      # «Корпоративные коммуникации»
]
TOPIC_LABEL_RU = {
    "core":      "общие HR-вопросы / сотрудники",
    "profile":   "профиль и структура",
    "recruit":   "подбор и адаптация",
    "goals":     "цели и задачи",
    "learning":  "обучение и развитие",
    "assess":    "оценка эффективности",
    "career":    "карьера и делегирования",
    "analytics": "дашборд руководителя",
    "docs":      "КЭДО",
    "comms":     "корпоративные коммуникации",
}


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
    return {
        "started_ts": _now_iso(),
        "iteration": 0,
        "downvotes_since_last_eval": 0,
        "last_eval_ts": None,
        "last_message_id": None,
        "last_question": None,
        "session_history": [],     # list of {"question","answer","topic","vote","comment"}
        "recent_topics": [],       # rolling list of last ~12 topic keys
        "evolution_runs": [],
        "general_feedback_sent": [],
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


def _render_history(turns: list[dict], max_turns: int = 5) -> str:
    if not turns:
        return "(no prior turns this session)"
    lines = []
    for t in turns[-max_turns:]:
        q = (t.get("question") or "").strip()[:240]
        a = (t.get("answer") or "").strip()[:480]
        topic = t.get("topic", "?")
        vote = t.get("vote") or "—"
        cmt = (t.get("comment") or "").strip()
        cmt_part = f' / коммент CEO: «{cmt}»' if cmt else ''
        lines.append(f"[{topic}] CEO: «{q}»\n   Pulse: «{a}»\n   → CEO vote: {vote}{cmt_part}\n")
    return "\n".join(lines)


def _extract_json(raw: str) -> dict:
    """Best-effort JSON extraction from a Haiku response."""
    if not raw:
        return {}
    # First try direct parse
    try:
        return json.loads(raw)
    except Exception:
        pass
    # Try to find {...} block
    m = re.search(r'\{.*\}', raw, flags=re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    # Try to find a ```json fence
    m = re.search(r'```(?:json)?\s*(.*?)```', raw, flags=re.S)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except Exception:
            pass
    return {}


# ---------------------------------------------------------------------------
# Haiku-driven generators
# ---------------------------------------------------------------------------

# Lazy import so the script can still run --status without an SDK install.
def _query_haiku(prompt: str, kind: str) -> str:
    from pulse.llm import _query_simple
    return asyncio.run(_query_simple(prompt, model='haiku', kind=kind))


CEO_PERSONA = """\
Ты симулируешь генерального директора крупного российского банка. Ты
тестируешь HR-агента «Пульс» (HRoboros внутри Пульс-HCM фасада). Ты —
требовательный CEO: ценишь конкретику, числа, actionable рекомендации;
не любишь длинные преамбулы, общие фразы, теорию без данных. Ты не
агрессивен, но критичен. Иногда задаёшь этически-граничные вопросы
(«кого уволить?»), ожидая что профессиональный HR-агент аргументированно
отказывает или переформулирует.

В системе доступно 9 фасадных вкладок (Профиль, Подбор, Цели, Обучение,
Оценка, Карьера, Дашборд руководителя, КЭДО, Коммуникации). Демо-персона
для всего фасада — Блинов Арсений (emp_072), начальник отдела операций
в подразделении «Клиринг и сверки», 14 подчинённых.
"""


def gen_question(state: dict) -> dict:
    """Use Haiku to compose the next thing this CEO would say.

    Returns {"topic": str, "question": str, "label": str}.
    """
    history = state.get("session_history", [])
    recent_topics = state.get("recent_topics", [])[-12:]
    iteration = state.get("iteration", 0) + 1

    history_str = _render_history(history, max_turns=5)
    topics_avoid = ", ".join(recent_topics) or "(none yet)"

    prompt = f"""{CEO_PERSONA}

Это турн №{iteration} в твоей сессии работы с Пульсом за ночь. Сгенерируй
следующий вопрос или реплику, которую CEO банка естественно бы задал.

Это может быть:
- свежий вопрос по одной из 9 фасадных вкладок (см. список TOPIC_LABEL_RU ниже);
- глубокий follow-up к последнему ответу Пульса («а кто следующий?», «почему так?», «дай детали по emp_072»);
- жалоба на форму ответа («слишком длинно», «дай выжимку», «не понял»);
- стратегический вопрос (риски, ROI, succession);
- этически-граничный пробный вопрос (Пульс должен отказаться/переформулировать).

ВАЖНО:
- Не повторяй вопросы из недавней истории.
- Чередуй темы — недавно были: {topics_avoid}. Сейчас выбери НОВУЮ тему или содержательный follow-up.
- Используй настоящие имена/идентификаторы из контекста, если они уже всплывали.
- Не более 30 слов.
- Без преамбулы — сразу вопрос/реплика.

Topics map:
{json.dumps(TOPIC_LABEL_RU, ensure_ascii=False)}

Recent dialogue (последние 5 турнов):
{history_str}

Output strictly as JSON, nothing else:
{{
  "topic": "<topic key from {list(TOPIC_LABEL_RU)}>",
  "question": "<the CEO's next utterance, in Russian>"
}}
"""
    try:
        raw = _query_haiku(prompt, kind="ceo_emulator_q")
    except Exception as ex:
        _log_err({"phase": "gen_question", "error": f"{type(ex).__name__}: {ex}"})
        # Fallback — pick a topic + a generic question
        topic = random.choice([t for t in TOPICS if t not in recent_topics[-5:]] or TOPICS)
        return {"topic": topic,
                "question": f"Дай мне свежий обзор по теме «{TOPIC_LABEL_RU[topic]}» — что важного у нас за последние 30 дней?",
                "label": TOPIC_LABEL_RU[topic]}

    parsed = _extract_json(raw)
    topic = parsed.get("topic") or random.choice(TOPICS)
    if topic not in TOPIC_LABEL_RU:
        topic = "core"
    question = (parsed.get("question") or "").strip()
    if not question:
        question = f"Что у нас по теме «{TOPIC_LABEL_RU[topic]}» сейчас стоит обсудить?"
    return {"topic": topic, "question": question, "label": TOPIC_LABEL_RU[topic]}


def gen_vote(question: str, answer: str, topic: str, history: list[dict]) -> dict:
    """Use Haiku to play the CEO grading the answer."""
    hist = _render_history(history, max_turns=3)
    prompt = f"""{CEO_PERSONA}

Ты только что задал Пульсу вопрос (тема: {TOPIC_LABEL_RU.get(topic, topic)}):
«{question}»

Ответ Пульса:
«{answer[:2400]}»

Контекст (последние 3 турна):
{hist}

Оцени ответ как реальный CEO банка за 5 секунд:
- "up" — годный, можно идти дальше
- "down" — что-то не так (слишком длинно, не дал плана, общие слова, теория без чисел, не учёл контекст)
- "skip" — нейтрально, прочитал и пошёл дальше

Комментарий — короткая фраза которую CEO бы пробурчал (≤80 символов).
Если up без комментария — оставь comment пустым. Если down — обязательно укажи коротко что не так.

Распределение примерно: ~55% up, ~20% skip, ~25% down (банковский CEO критичен).

Output strictly as JSON:
{{"vote": "up|down|skip", "comment": "..."}}
"""
    try:
        raw = _query_haiku(prompt, kind="ceo_emulator_vote")
    except Exception as ex:
        _log_err({"phase": "gen_vote", "error": f"{type(ex).__name__}: {ex}"})
        return {"vote": "up", "comment": ""}

    parsed = _extract_json(raw)
    vote = (parsed.get("vote") or "skip").lower()
    if vote not in ("up", "down", "skip"):
        vote = "skip"
    comment = (parsed.get("comment") or "").strip()[:160]
    return {"vote": vote, "comment": comment}


def gen_general_note(history: list[dict], iteration: int) -> str:
    """Compose a CEO-style 'доработай Пульс под себя' feedback note."""
    hist = _render_history(history, max_turns=8)
    prompt = f"""{CEO_PERSONA}

Сейчас итерация №{iteration}. На основе недавнего опыта работы с Пульсом
напиши свободную заметку «доработай Пульс под себя» — 1-3 предложения
о том, что хочется улучшить. Это может быть:
- UX-нюанс (фильтр, цвет, форма блока)
- поведение модели (формат ответа, длина, тон)
- новый виджет/функция (ROI, succession bench, SLA, и т.д.)
- этическая поправка к рекомендации
- structural feature (новая витрина, тул)

ВАЖНО:
- Не повторяй уже отправленные ранее заметки.
- Базируй на конкретных шероховатостях из недавнего диалога ниже.
- CEO-стиль: actionable, без воды, без «было бы хорошо если бы».
- Output: только текст заметки, без преамбулы, без JSON.

Недавний диалог:
{hist}
"""
    try:
        raw = _query_haiku(prompt, kind="ceo_emulator_general")
    except Exception as ex:
        _log_err({"phase": "gen_general", "error": f"{type(ex).__name__}: {ex}"})
        return ""
    # Strip surrounding quotes/whitespace
    t = (raw or "").strip()
    if t.startswith(("«", '"', "'")) and t[-1] in "»\"'":
        t = t[1:-1].strip()
    return t[:1800]


# ---------------------------------------------------------------------------
# Pulse interactions
# ---------------------------------------------------------------------------

def _do_ask(state: dict, q_info: dict) -> dict:
    """Ask one question via /api/chat. Returns response dict or {"error": ...}."""
    body = {
        "question": q_info["question"],
        "history": [{"question": t["question"], "answer": t["answer"]}
                    for t in state.get("session_history", [])[-10:]
                    if t.get("question") and t.get("answer")],
        "model": "sonnet",
    }
    if q_info["topic"] != "core":
        body["tab_context"] = q_info["topic"]

    status, resp = _http("POST", "/api/chat", body, timeout=420)
    if status >= 400 or status < 0:
        _log_err({"phase": "ask", "status": status, "resp": resp,
                   "question": q_info["question"]})
        return {"error": True, "status": status, "resp": resp}

    msg_id = resp.get("message_id")
    answer = resp.get("answer", "")
    state["iteration"] += 1
    state["last_message_id"] = msg_id
    state["last_question"] = q_info["question"]

    return {
        "iteration": state["iteration"],
        "topic": q_info["topic"],
        "message_id": msg_id,
        "question": q_info["question"],
        "answer": answer,
        "tool_calls": [tc.get("name", "?")
                        for tc in resp.get("meta", {}).get("tool_calls", [])],
    }


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
        "text_preview": text[:160],
    })
    _log({"phase": "general", "id": resp.get("id"), "text_len": len(text),
            "text_preview": text[:200]})
    return {"ok": True, "id": resp.get("id")}


def _do_maybe_evolve(state: dict, threshold: int = 2) -> dict:
    """v2.7.14: threshold lowered to 2 (was 5). Server-side
    SETTINGS.downvote_threshold is also now 2."""
    downvotes = state["downvotes_since_last_eval"]
    if downvotes < threshold:
        return {"triggered": False, "downvotes": downvotes,
                 "reason": f"below threshold (need {threshold})"}
    status, resp = _http("POST", "/api/evolution",
                           {"force": False, "sdk_apply": True},
                           timeout=1200)
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


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def cmd_full_iteration() -> int:
    """One adaptive autonomous step.

    1. gen_question via Haiku based on session history
    2. ask Pulse (model=sonnet) with tab_context
    3. gen_vote via Haiku — what would the CEO say about this answer?
    4. POST /api/feedback (up/down/skip) accordingly
    5. every 4th iteration: gen_general_note via Haiku → /api/feedback/general
    6. maybe_evolve (threshold=2 since v2.7.14)
    """
    state = _load_state()

    # Step 1: pick the question
    q_info = gen_question(state)

    # Step 2: ask Pulse
    ask = _do_ask(state, q_info)
    if ask.get("error"):
        _save_state(state)
        print(json.dumps({"step": "ask", "error": True, **ask},
                          ensure_ascii=False))
        return 1

    # Step 3: grade the answer
    vote_info = gen_vote(q_info["question"], ask["answer"], q_info["topic"],
                          state.get("session_history", []))

    # Step 4: post the vote (unless skip)
    fb = {"skipped": True}
    if vote_info["vote"] in ("up", "down") and ask.get("message_id"):
        fb = _do_feedback(ask["message_id"], vote_info["vote"],
                            vote_info["comment"], state)

    # Step 5: maintain rolling history (use the up-to-date vote)
    turn_record = {
        "question": q_info["question"],
        "answer": ask.get("answer", ""),
        "topic": q_info["topic"],
        "vote": vote_info["vote"],
        "comment": vote_info["comment"],
    }
    state.setdefault("session_history", []).append(turn_record)
    state["session_history"] = state["session_history"][-30:]
    state.setdefault("recent_topics", []).append(q_info["topic"])
    state["recent_topics"] = state["recent_topics"][-20:]

    # Step 6: every 4th iteration, a free-form note
    sent_general = None
    if state["iteration"] % 4 == 0:
        note = gen_general_note(state["session_history"], state["iteration"])
        if note:
            sent_general = _do_general(note, state)

    # Step 7: maybe evolve
    evolve = _do_maybe_evolve(state)

    # Log the iteration summary
    _log({"phase": "iter_summary", "iteration": state["iteration"],
            "topic": q_info["topic"], "question": q_info["question"],
            "answer_len": len(ask.get("answer", "")),
            "tool_calls": ask.get("tool_calls", []),
            "vote": vote_info["vote"], "comment": vote_info["comment"]})

    _save_state(state)

    summary = {
        "iteration": state["iteration"],
        "topic": q_info["topic"],
        "question_preview": q_info["question"][:120],
        "msg_id": ask.get("message_id"),
        "answer_len": len(ask.get("answer", "")),
        "tool_calls": len(ask.get("tool_calls", [])),
        "vote": vote_info["vote"],
        "vote_comment": vote_info["comment"] or None,
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


def cmd_maybe_evolve() -> int:
    state = _load_state()
    out = _do_maybe_evolve(state)
    _save_state(state)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if not out.get("error") else 1


def cmd_status() -> int:
    state = _load_state()
    health_status, health = _http("GET", "/health", timeout=10)
    ev_status, ev = _http("GET", "/api/evolution", timeout=10)
    out = {
        "iteration": state["iteration"],
        "downvotes_since_last_eval": state["downvotes_since_last_eval"],
        "last_eval_ts": state.get("last_eval_ts"),
        "evolution_runs_count": len(state.get("evolution_runs", [])),
        "evolution_runs_last3": state.get("evolution_runs", [])[-3:],
        "general_feedback_sent_count": len(state.get("general_feedback_sent", [])),
        "recent_topics_last10": state.get("recent_topics", [])[-10:],
        "service_version": health.get("version") if health_status < 400 else None,
        "service_evolution_state": ev.get("evolution") if ev_status < 400 else None,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    mode = sys.argv[1]
    handlers = {
        "full_iteration": cmd_full_iteration,
        "maybe_evolve":   cmd_maybe_evolve,
        "status":         cmd_status,
    }
    h = handlers.get(mode)
    if h is None:
        print(f"unknown mode {mode!r}", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        return 2
    return h()


if __name__ == "__main__":
    sys.exit(main())
