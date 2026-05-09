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

`handle_chat` returns `{message_id, answer, meta}`. Both question and answer
are appended to `data/logs/chat.jsonl` as a single record so feedback can
later refer to that message_id.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

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
# Chat handler
# ---------------------------------------------------------------------------

async def handle_chat(question: str, history: list[dict[str, str]] | None = None,
                      *, model: str = "sonnet") -> dict[str, Any]:
    """One chat turn. Returns {message_id, answer, meta}.

    `history` is currently informational — the SDK keeps its own session via
    ClaudeSDKClient context. We pass it through `meta` for the chat log only.
    """
    from claude_agent_sdk import ClaudeSDKClient  # type: ignore

    full_model = normalize_model(model)
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

    answer_chunks: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    usage = None

    # IMPORTANT: this OAuth env-var must be set for the SDK to authenticate via the Max plan.
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        log.warning("CLAUDE_CODE_OAUTH_TOKEN not set — SDK call will fail.")

    async with ClaudeSDKClient(options=options) as client:
        await client.query(question)
        async for msg in client.receive_response():
            txt = _extract_text(msg)
            if txt:
                answer_chunks.append(txt)
            # Some SDK versions emit ToolUseBlock items inside content — pull tool names if present.
            content = getattr(msg, "content", None)
            if content and not isinstance(content, str):
                for block in content:
                    name = getattr(block, "name", None)
                    inp = getattr(block, "input", None)
                    if name and inp is not None:
                        tool_calls.append({"name": name, "input": inp})
            u = _extract_usage(msg, full_model)
            if u:
                usage = u
    answer = "".join(answer_chunks).strip()

    message_id = _new_message_id()
    meta = {"model": full_model, "tool_calls": tool_calls, "history_len": len(history or [])}
    log_chat(question, answer, message_id, meta)
    for tc in tool_calls:
        log_tool_call(tc["name"], tc["input"], message_id)
    if usage is not None:
        log_usage(usage, kind="chat")

    # Phase-6 hook: post-task reflection when the turn was rich or had errors.
    try:
        from .reflection import reflect, should_reflect
        had_error = "is_error" in answer.lower() or "ошибк" in answer.lower()
        if should_reflect(n_tool_calls=len(tool_calls), had_error=had_error):
            await reflect(question=question, answer=answer, tool_calls=tool_calls,
                          message_id=message_id)
    except Exception as ex:
        log.warning("reflection failed: %s", ex)

    return {"message_id": message_id, "answer": answer, "meta": meta}


__all__ = [
    "build_system_prompt",
    "handle_chat",
    "log_chat",
    "log_tool_call",
    "_new_message_id",
]
