"""Self-introspection tools (evolution mode only).

Restricted to repo-relative paths so the agent cannot wander the filesystem.
Read-only — actual writes still go through Read/Edit/Write built-in tools
when permission_mode="acceptEdits" is set in the evolution session.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from ..config import PATHS


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def _safe_repo_path(rel: str) -> Path:
    p = (PATHS.repo / rel).resolve()
    repo = PATHS.repo.resolve()
    if not str(p).startswith(str(repo) + "/") and p != repo:
        raise ValueError(f"Path {rel!r} escapes repo root.")
    return p


@tool(
    "repo_read",
    "Прочитать файл из текущего репозитория Пульса (только относительный путь). "
    "Доступно только в эволюционном режиме.",
    {"path": str},
)
async def repo_read(args: dict[str, Any]) -> dict[str, Any]:
    rel = (args.get("path") or "").strip()
    if not rel:
        return _err("path обязателен.")
    try:
        p = _safe_repo_path(rel)
    except ValueError as ex:
        return _err(str(ex))
    if not p.exists() or p.is_dir():
        return _err(f"{rel} не существует или каталог.")
    txt = p.read_text(encoding="utf-8", errors="replace")
    if len(txt) > 60_000:
        txt = txt[:60_000] + "\n\n[truncated]"
    return _ok(txt)


@tool(
    "repo_list",
    "Перечислить файлы по glob-маске относительно корня репо. Например: 'prompts/*.md', 'pulse/**/*.py'.",
    {"glob": str},
)
async def repo_list(args: dict[str, Any]) -> dict[str, Any]:
    pattern = (args.get("glob") or "").strip() or "**/*"
    repo = PATHS.repo
    matches = sorted(str(p.relative_to(repo)) for p in repo.glob(pattern) if p.is_file())[:200]
    if not matches:
        return _ok(f"По маске {pattern} файлов нет.")
    return _ok("\n".join(matches))


__all__ = ["repo_read", "repo_list"]
