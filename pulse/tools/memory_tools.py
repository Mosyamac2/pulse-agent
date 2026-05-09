"""Memory tools — append-style updates to identity.md and scratchpad.md.

Phase-4 keeps the implementation simple (raw file write). Phase 6 adds a
proper file lock + diff. The agent uses these to evolve its self-model.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from ..config import PATHS


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text("", encoding="utf-8")


@tool(
    "update_scratchpad",
    "Дописать абзац в data/memory/scratchpad.md (рабочая память). Используй для открытых "
    "гипотез и не‑закрытых вопросов в текущем диалоге. Параметр `entry` — короткий текст.",
    {"entry": str},
)
async def update_scratchpad(args: dict[str, Any]) -> dict[str, Any]:
    entry = (args.get("entry") or "").strip()
    if not entry:
        return _ok("Пустая запись — пропустил.")
    p = PATHS.memory / "scratchpad.md"
    _ensure(p)
    with p.open("a", encoding="utf-8") as f:
        f.write(f"\n## {_now()}\n\n{entry}\n")
    return _ok(f"scratchpad.md обновлён ({len(entry)} символов).")


@tool(
    "update_identity",
    "Дописать абзац в data/memory/identity.md (самопонимание). Используй редко — только когда "
    "что-то изменилось в твоём «как я работаю / кто я как помощник». Параметр `entry` — текст.",
    {"entry": str},
)
async def update_identity(args: dict[str, Any]) -> dict[str, Any]:
    entry = (args.get("entry") or "").strip()
    if not entry:
        return _ok("Пустая запись — пропустил.")
    p = PATHS.memory / "identity.md"
    _ensure(p)
    with p.open("a", encoding="utf-8") as f:
        f.write(f"\n## {_now()}\n\n{entry}\n")
    return _ok(f"identity.md обновлён ({len(entry)} символов).")


__all__ = ["update_scratchpad", "update_identity"]
