"""Knowledge-base tools: read / write / list per-topic markdown files."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from ..config import PATHS


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,60}$")


def _resolve(topic: str) -> Path:
    name = topic.strip().lower()
    if not _SAFE_NAME.match(name):
        raise ValueError(f"unsafe knowledge topic: {topic!r}")
    if not name.endswith(".md"):
        name = f"{name}.md"
    return PATHS.knowledge / name


@tool(
    "knowledge_read",
    "Прочитать topic из data/memory/knowledge/. Имя topic — только латиница, цифры, '._-'. "
    "Пример: 'patterns', 'feedback-classes'.",
    {"topic": str},
)
async def knowledge_read(args: dict[str, Any]) -> dict[str, Any]:
    try:
        p = _resolve(args.get("topic", ""))
    except ValueError as ex:
        return _err(str(ex))
    if not p.exists():
        return _ok(f"Топик {p.name} ещё не создан.")
    txt = p.read_text(encoding="utf-8")
    return _ok(txt or f"Топик {p.name} пуст.")


@tool(
    "knowledge_write",
    "Перезаписать topic в knowledge/ полным новым содержимым. Используй ОЧЕНЬ редко — только когда "
    "осмысленно меняешь весь файл (напр. реестр feedback-classes полностью пересобран).",
    {"topic": str, "content": str},
)
async def knowledge_write(args: dict[str, Any]) -> dict[str, Any]:
    try:
        p = _resolve(args.get("topic", ""))
    except ValueError as ex:
        return _err(str(ex))
    content = args.get("content") or ""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return _ok(f"{p.name} перезаписан ({len(content)} символов).")


@tool(
    "knowledge_list",
    "Перечислить все topic-файлы в data/memory/knowledge/.",
    {},
)
async def knowledge_list(args: dict[str, Any]) -> dict[str, Any]:
    PATHS.knowledge.mkdir(parents=True, exist_ok=True)
    files = sorted(p.name for p in PATHS.knowledge.glob("*.md"))
    if not files:
        return _ok("Knowledge base пуста.")
    return _ok("Топики:\n" + "\n".join(f"  - {f}" for f in files))


__all__ = ["knowledge_read", "knowledge_write", "knowledge_list"]
