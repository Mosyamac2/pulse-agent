"""Post-task reflection. Triggered at the end of a chat turn when:

* the assistant made ≥ 3 tool calls in this turn (rich task), OR
* the response contained an error block, OR
* the user later puts a 👎 on this msg_id (handled by evolution loop, not here).

Light model (Sonnet 4.6). 150-250 word free-form reflection. Optionally produces
0-3 structured backlog candidates that get appended to `improvement-backlog.md`.

Output is logged to `data/logs/task_reflections.jsonl` and the entry is
referenced from there going forward.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from .config import PATHS
from .improvement_backlog import append_entry
from .llm import _query_simple
from .memory import reflections_log_path

log = logging.getLogger(__name__)


REFLECTION_PROMPT = """Ты — Пульс. Только что завершил задачу. Ниже:
- вопрос пользователя,
- твой ответ,
- список вызванных инструментов.

Сделай короткую (150-250 слов) рефлексию на русском:
1) что прошло хорошо;
2) что было слабо или ошибочно;
3) есть ли структурный класс проблемы (P2 «Meta-over-Patch»), который ты подсветил здесь?

В конце сформулируй 0-3 кандидата в backlog в формате:
BACKLOG: <одна короткая фраза-намерение>
BACKLOG: <…>

Не более 3-х. Если кандидатов нет — не пиши строки BACKLOG.
"""


def should_reflect(*, n_tool_calls: int, had_error: bool) -> bool:
    return n_tool_calls >= 3 or had_error


async def reflect(*, question: str, answer: str, tool_calls: list[dict[str, Any]],
                  message_id: str) -> dict[str, Any]:
    """Run a reflection step. Returns the parsed result and writes to disk."""
    tool_summary = ", ".join(t.get("name", "?") for t in tool_calls) or "(нет)"
    prompt = (
        f"Вопрос пользователя:\n{question}\n\n"
        f"Твой ответ:\n{answer}\n\n"
        f"Вызванные тулы: {tool_summary}\n"
    )
    txt = await _query_simple(prompt, model="sonnet", system=REFLECTION_PROMPT, kind="reflection")
    candidates = _extract_backlog_candidates(txt)

    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "message_id": message_id,
        "reflection": txt,
        "candidates": candidates,
    }
    PATHS.ensure()
    with reflections_log_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    for cand in candidates:
        append_entry(cand, provenance=f"reflection:{message_id}")

    log.info("reflection logged for %s; backlog_candidates=%d", message_id, len(candidates))
    return record


_BACKLOG_RX = re.compile(r"^\s*BACKLOG:\s*(.+?)\s*$", re.MULTILINE)


def _extract_backlog_candidates(text: str) -> list[str]:
    return [m.group(1).strip()[:240] for m in _BACKLOG_RX.finditer(text)][:3]


__all__ = ["should_reflect", "reflect"]
