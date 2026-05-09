"""Feedback-log reader for the evolution loop."""
from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool

from ..config import PATHS


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


@tool(
    "get_recent_feedback",
    "Прочитать последние N записей из data/logs/feedback.jsonl (лайки/дизлайки). "
    "Параметр `n` — количество записей (по умолчанию 20, максимум 200).",
    {"n": int},
)
async def get_recent_feedback(args: dict[str, Any]) -> dict[str, Any]:
    n = max(1, min(200, int(args.get("n") or 20)))
    p = PATHS.logs / "feedback.jsonl"
    if not p.exists():
        return _ok("feedback.jsonl ещё не создан — нет ни одного отклика.")

    # tail-read: read all (file is small) and slice last n.
    lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    tail = lines[-n:]
    n_up = sum(1 for ln in tail if '"verdict":"up"' in ln or '"verdict": "up"' in ln)
    n_down = len(tail) - n_up
    body = []
    for ln in tail:
        try:
            rec = json.loads(ln)
            verdict = rec.get("verdict", "?")
            comment = (rec.get("comment") or "").strip()
            mid = rec.get("message_id", "")
            ts = rec.get("ts", "")
            body.append(f"  [{verdict}] {ts} {mid}: {comment[:120]}")
        except json.JSONDecodeError:
            body.append("  [parse-error]")
    return _ok(
        f"Последние {len(tail)} откликов: {n_up} 👍, {n_down} 👎.\n"
        + "\n".join(body)
    )


__all__ = ["get_recent_feedback"]
