"""Chat-loop using Claude Agent SDK.

Per TZ §10.4 we **re-read** prompts and memory from disk on every chat turn,
so post-evolution edits take effect without a process restart. The system
prompt is assembled from:

  prompts/SYSTEM.md
  + BIBLE.md (full)
  + data/memory/identity.md
  + data/memory/scratchpad.md (last 5000 chars)
  + Data Sources Registry (computed inline)
  + last 5 entries of improvement-backlog.md (if any)

Two consumer surfaces over the SDK loop:

* `stream_chat_events` — async generator yielding event dicts as the SDK
  produces them (status, tool_call, tool_result, text, done, error). Used
  by POST /api/chat/stream so the UI can show progress during the 1–4
  minute turns instead of a static "думаю…".
* `handle_chat` — thin wrapper that drains the generator and returns the
  final `{message_id, answer, meta}` dict. Used by POST /api/chat (and by
  the test fixtures that monkeypatch this entry point directly).

Logging side effects (chat.jsonl, tools.jsonl, budget.jsonl, reflection)
all happen inside `stream_chat_events` so both surfaces produce identical
on-disk traces.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

from .config import PATHS
from .llm import MODEL_LIGHT, _extract_text, _extract_usage, build_options, log_usage, normalize_model
from .tools import build_chat_server, chat_allowed_tools

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System prompt assembly
# ---------------------------------------------------------------------------

def _safe_read(p: Path, max_chars: int | None = None) -> str:
    if not p.exists():
        return ""
    txt = p.read_text(encoding="utf-8")
    if max_chars is not None and len(txt) > max_chars:
        txt = txt[-max_chars:]
    return txt


def _data_sources_registry() -> str:
    """Compact summary of DB tables — what data is available right now."""
    if not PATHS.db.exists():
        return "## Data Sources Registry\n\nДанные ещё не сгенерированы (run `python -m scripts.seed --force`)."

    from sqlite_utils import Database
    db = Database(PATHS.db)
    parts: list[str] = ["## Data Sources Registry\n"]
    for name in ("employees", "activity_daily", "peer_feedback", "performance_reviews",
                 "courses", "course_enrollments", "jira_issues"):
        try:
            cnt = db[name].count
        except Exception:
            continue
        parts.append(f"- {name}: {cnt} строк")
    # latest data point
    rows = list(db.query("SELECT MAX(date) AS d FROM activity_daily"))
    if rows and rows[0]["d"]:
        parts.append(f"- последний day-tick: {rows[0]['d']}")
    # ML metrics
    for mname in ("attrition", "course_recommender", "role_success"):
        p = PATHS.ml_models / f"{mname}.joblib"
        if p.exists():
            parts.append(f"- ml_models/{mname}.joblib: present")
    return "\n".join(parts)


def _backlog_tail(n: int = 5) -> str:
    p = PATHS.knowledge / "improvement-backlog.md"
    if not p.exists():
        return ""
    lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


# ---------------------------------------------------------------------------
# Conversation history — keep multi-turn context across separate /api/chat calls
# ---------------------------------------------------------------------------

HISTORY_TURNS_CAP = 10
HISTORY_CHARS_CAP = 8000


def _format_history(history: list[dict[str, str]] | None) -> str:
    """Render prior turns as a bracketed block to prepend to the user message.

    Each ClaudeSDKClient is one-shot per /api/chat call (the SDK subprocess
    exits with the context manager), so without replaying prior turns the
    SDK has no memory of them. We replay by inlining: cheap, deterministic,
    no extra LLM round-trips. Capped at HISTORY_TURNS_CAP turns or
    HISTORY_CHARS_CAP chars (whichever is tighter) so long sessions don't
    blow out the prompt budget.

    Each item: {"question": str, "answer": str} (extra keys ignored). Empty
    history → empty string, so the user message is unchanged.
    """
    if not history:
        return ""
    turns = history[-HISTORY_TURNS_CAP:]
    lines: list[str] = ["[Контекст диалога — предыдущие реплики этой сессии]"]
    for t in turns:
        q = (t.get("question") or "").strip()
        a = (t.get("answer") or "").strip()
        if q:
            lines.append(f"Пользователь: {q}")
        if a:
            lines.append(f"Пульс: {a}")
    lines.append("[/Контекст диалога]")
    block = "\n".join(lines)
    if len(block) > HISTORY_CHARS_CAP:
        # Drop oldest content; keep the closing marker intact.
        head = "[Контекст диалога — предыдущие реплики (старое усечено)]\n"
        block = head + block[-(HISTORY_CHARS_CAP - len(head)):]
    return block + "\n\n"


def _compose_user_message(question: str,
                           history: list[dict[str, str]] | None,
                           tab_context: str | None = None) -> str:
    """Render the user message: optional tab context + history + question.

    `tab_context` is the v2.6.0+ HCM façade hook (P14): when the question
    arrives from a non-Pulse tab dock, the front-end forwards the tab key
    (e.g. "goals", "recruit") so the system prompt's «Контекст HCM-фасада»
    section knows which panel the user is looking at. We surface it as a
    short tagged line BEFORE the conversation history so the agent reads
    the freshest context first.
    """
    prefix = _format_history(history)
    parts: list[str] = []
    if tab_context:
        tag = tab_context.strip()[:60]
        parts.append(f"[Контекст вкладки: {tag}]\n")
    if prefix:
        parts.append(prefix)
    parts.append(question)
    return "".join(parts)


def build_system_prompt() -> str:
    """Assemble the system prompt for a chat turn. Pure function over disk state."""
    chunks: list[str] = []
    chunks.append(_safe_read(PATHS.prompts / "SYSTEM.md"))
    chunks.append("\n---\n## BIBLE.md (full)\n")
    chunks.append(_safe_read(PATHS.bible))
    chunks.append("\n---\n## identity.md\n")
    chunks.append(_safe_read(PATHS.memory / "identity.md") or "(пусто — заполнится по мере работы)")
    chunks.append("\n---\n## scratchpad.md (хвост)\n")
    chunks.append(_safe_read(PATHS.memory / "scratchpad.md", max_chars=5000) or "(пусто)")
    chunks.append("\n---\n")
    chunks.append(_data_sources_registry())
    backlog = _backlog_tail()
    if backlog:
        chunks.append("\n---\n## Improvement Backlog (хвост)\n" + backlog)
    return "\n".join(chunks)


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def _new_message_id() -> str:
    today = date.today().isoformat()
    return f"msg_{today}_{secrets.token_hex(3)}"


def log_chat(question: str, answer: str, message_id: str, meta: dict[str, Any]) -> None:
    PATHS.ensure()
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "message_id": message_id,
        "question": question,
        "answer": answer,
        "meta": meta,
    }
    with (PATHS.logs / "chat.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def log_tool_call(name: str, args: dict[str, Any], message_id: str) -> None:
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "message_id": message_id,
        "tool": name,
        "args": args,
    }
    with (PATHS.logs / "tools.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Block classification — pure helpers over SDK content blocks
# ---------------------------------------------------------------------------

_ARGS_SUMMARY_LIMIT = 200


def _summarize_args(args: Any) -> Any:
    """Return a JSON-serialisable summary of tool args, capped for UI display.

    Full args still go to tools.jsonl via `log_tool_call`; this is just for
    the live progress feed.
    """
    if args is None:
        return None
    try:
        s = json.dumps(args, ensure_ascii=False)
    except (TypeError, ValueError):
        return {"_repr": repr(args)[:_ARGS_SUMMARY_LIMIT]}
    if len(s) <= _ARGS_SUMMARY_LIMIT:
        return args
    return {"_truncated": s[: _ARGS_SUMMARY_LIMIT - 1] + "…"}


def _classify_block(block: Any) -> tuple[str, dict[str, Any]]:
    """Map an SDK content block to ('text'|'tool_use'|'tool_result'|'other', payload)."""
    name = getattr(block, "name", None)
    inp = getattr(block, "input", None)
    if isinstance(name, str) and name and inp is not None:
        return "tool_use", {"name": name, "input": inp,
                              "id": getattr(block, "id", None)}
    tool_use_id = getattr(block, "tool_use_id", None)
    if tool_use_id:
        return "tool_result", {
            "tool_use_id": tool_use_id,
            "is_error": bool(getattr(block, "is_error", False)),
        }
    txt = getattr(block, "text", None)
    if isinstance(txt, str) and txt:
        return "text", {"text": txt}
    return "other", {}


# ---------------------------------------------------------------------------
# Streaming chat handler — async generator of event dicts
# ---------------------------------------------------------------------------

async def stream_chat_events(question: str,
                             history: list[dict[str, str]] | None = None,
                             *, model: str = "sonnet",
                             tab_context: str | None = None) -> AsyncIterator[dict[str, Any]]:
    """Run one chat turn and yield events as the SDK produces them.

    Event shapes:
      {"type": "status",     "phase": "starting", "model": "<full-id>"}
      {"type": "tool_call",  "name": "<mcp-id>", "args": <summary>, "id": <str|null>}
      {"type": "tool_result","tool_use_id": "<id>", "ok": <bool>}
      {"type": "text",       "text": "<chunk>"}
      {"type": "done",       "message_id": "<id>", "answer": "<full>", "meta": {...}}
      {"type": "error",      "message": "<repr>"}

    Side effects (chat.jsonl/tools.jsonl/budget.jsonl/reflection) fire here
    so both `stream_chat_events` and `handle_chat` produce identical traces.
    """
    from claude_agent_sdk import ClaudeSDKClient  # type: ignore

    full_model = normalize_model(model)
    yield {"type": "status", "phase": "starting", "model": full_model}

    system_prompt = build_system_prompt()
    mcp = {"pulse-tools": build_chat_server()}
    options = build_options(
        system_prompt=system_prompt,
        allowed_tools=chat_allowed_tools(),
        mcp_servers=mcp,
        model=full_model,
        permission_mode="auto",
        max_turns=12,
        cwd=str(PATHS.repo),
    )

    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        log.warning("CLAUDE_CODE_OAUTH_TOKEN not set — SDK call will fail.")

    answer_chunks: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    usage = None
    failed = False

    user_message = _compose_user_message(question, history, tab_context=tab_context)

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(user_message)
            async for msg in client.receive_response():
                content = getattr(msg, "content", None)
                if content and not isinstance(content, str):
                    for block in content:
                        kind, data = _classify_block(block)
                        if kind == "tool_use":
                            tool_calls.append({"name": data["name"], "input": data["input"]})
                            yield {"type": "tool_call",
                                    "name": data["name"],
                                    "args": _summarize_args(data["input"]),
                                    "id": data.get("id")}
                        elif kind == "tool_result":
                            yield {"type": "tool_result",
                                    "tool_use_id": data["tool_use_id"],
                                    "ok": not data["is_error"]}
                        elif kind == "text":
                            answer_chunks.append(data["text"])
                            yield {"type": "text", "text": data["text"]}
                u = _extract_usage(msg, full_model)
                if u:
                    usage = u
    except Exception as ex:
        failed = True
        log.exception("chat stream failed")
        yield {"type": "error", "message": f"{type(ex).__name__}: {ex}"}

    answer = "".join(answer_chunks).strip()
    message_id = _new_message_id()
    meta = {"model": full_model, "tool_calls": tool_calls,
            "history_len": len(history or []),
            "history_chars": len(_format_history(history))}

    # Persist trace even on partial/failed turns so feedback can still pin them.
    log_chat(question, answer, message_id, meta)
    for tc in tool_calls:
        log_tool_call(tc["name"], tc["input"], message_id)
    if usage is not None:
        log_usage(usage, kind="chat")

    if not failed:
        try:
            from .reflection import reflect, should_reflect
            had_error = "is_error" in answer.lower() or "ошибк" in answer.lower()
            if should_reflect(n_tool_calls=len(tool_calls), had_error=had_error):
                await reflect(question=question, answer=answer, tool_calls=tool_calls,
                              message_id=message_id)
        except Exception as ex:
            log.warning("reflection failed: %s", ex)

        yield {"type": "done", "message_id": message_id, "answer": answer, "meta": meta}


# ---------------------------------------------------------------------------
# Non-streaming wrapper — preserves the original handle_chat contract
# ---------------------------------------------------------------------------

async def handle_chat(question: str, history: list[dict[str, str]] | None = None,
                      *, model: str = "sonnet",
                      tab_context: str | None = None) -> dict[str, Any]:
    """One chat turn. Returns {message_id, answer, meta}.

    Drains `stream_chat_events` and returns the final `done` payload. If the
    turn errored before producing `done`, raises so FastAPI returns 500 —
    matches pre-streaming behavior.
    """
    final: dict[str, Any] | None = None
    error_msg: str | None = None
    async for ev in stream_chat_events(question, history, model=model,
                                          tab_context=tab_context):
        if ev["type"] == "done":
            final = {"message_id": ev["message_id"],
                      "answer": ev["answer"],
                      "meta": ev["meta"]}
        elif ev["type"] == "error":
            error_msg = ev["message"]
    if final is None:
        raise RuntimeError(error_msg or "chat stream ended without a done event")
    return final


__all__ = [
    "build_system_prompt",
    "handle_chat",
    "stream_chat_events",
    "log_chat",
    "log_tool_call",
    "_new_message_id",
    "_format_history",
    "_compose_user_message",
]
