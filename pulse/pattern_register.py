"""Thin convenience layer over patterns.md (technical-error class registry).

We do **not** parse the table programmatically here. The agent maintains
the table itself in evolution mode (LLM is the editor — see P5 LLM-First).
This module just exposes read/append helpers and renders a starter row.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .memory import file_lock, patterns_path, read_text


def read_patterns() -> str:
    return read_text(patterns_path())


def append_observation(class_id: str, summary: str, *, structural_fix: str = "—") -> None:
    """Append a row to the patterns.md table. The agent later may merge / refine."""
    p = patterns_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        from .memory import bootstrap_starter_files
        bootstrap_starter_files()

    today = datetime.now(timezone.utc).date().isoformat()
    row = f"| {class_id} | {summary[:120]} | {today} | {today} | 1 | {structural_fix[:120]} |\n"
    with file_lock(p):
        with p.open("a", encoding="utf-8") as f:
            f.write(row)


__all__ = ["read_patterns", "append_observation"]
