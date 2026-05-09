"""Improvement backlog — structured markdown table at `data/memory/knowledge/improvement-backlog.md`.

A small markdown-table store keyed by integer ID. Used by reflection,
evolution, and the consciousness loop to persist structural ideas with
their provenance ("where did this come from").

Schema:
  ID, Created (UTC), Status (open|in_progress|done|abandoned), Intent,
  Provenance, Human review? (yes|no)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .memory import backlog_path, file_lock, read_text


@dataclass
class BacklogEntry:
    id: int
    created: str
    status: str
    intent: str
    provenance: str
    human_review: bool

    def as_row(self) -> str:
        hr = "yes" if self.human_review else "no"
        return f"| {self.id} | {self.created} | {self.status} | {_clean(self.intent)} | {_clean(self.provenance)} | {hr} |"


def _clean(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ").strip()


_HEADER_LINES = 3   # title + table header + delimiter


def _parse_lines(raw: str) -> tuple[list[str], list[BacklogEntry]]:
    """Split file into header-prefix lines and parsed rows."""
    lines = raw.splitlines()
    header: list[str] = []
    body: list[BacklogEntry] = []
    in_table = False
    for ln in lines:
        if not in_table:
            header.append(ln)
            if re.match(r"^\|\s*[-:]+\s*\|", ln):  # delimiter row
                in_table = True
            continue
        if not ln.strip().startswith("|"):
            # past the table; ignore trailing prose for now
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) != 6:
            continue
        try:
            entry = BacklogEntry(
                id=int(cells[0]),
                created=cells[1],
                status=cells[2],
                intent=cells[3],
                provenance=cells[4],
                human_review=cells[5].lower().startswith("y"),
            )
        except ValueError:
            continue
        body.append(entry)
    return header, body


def _render(header_lines: list[str], entries: Iterable[BacklogEntry]) -> str:
    return "\n".join(header_lines + [e.as_row() for e in entries]) + "\n"


# ---------------------------------------------------------------------------

def list_entries() -> list[BacklogEntry]:
    raw = read_text(backlog_path())
    if not raw:
        return []
    _, entries = _parse_lines(raw)
    return entries


def append_entry(intent: str, *, provenance: str = "manual",
                  human_review: bool = False) -> BacklogEntry:
    """Append a fresh entry. Auto-generates the ID."""
    p = backlog_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        # ensure starter is on disk
        from .memory import bootstrap_starter_files
        bootstrap_starter_files()

    with file_lock(p):
        raw = read_text(p)
        header, entries = _parse_lines(raw)
        next_id = (max((e.id for e in entries), default=0)) + 1
        entry = BacklogEntry(
            id=next_id,
            created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            status="open",
            intent=intent.strip()[:240],
            provenance=provenance.strip()[:120],
            human_review=human_review,
        )
        entries.append(entry)
        p.write_text(_render(header, entries), encoding="utf-8")
    return entry


def update_status(entry_id: int, new_status: str) -> bool:
    if new_status not in {"open", "in_progress", "done", "abandoned"}:
        raise ValueError(new_status)
    p = backlog_path()
    with file_lock(p):
        raw = read_text(p)
        if not raw:
            return False
        header, entries = _parse_lines(raw)
        found = False
        for e in entries:
            if e.id == entry_id:
                e.status = new_status
                found = True
                break
        if not found:
            return False
        p.write_text(_render(header, entries), encoding="utf-8")
    return True


def tail(n: int = 5) -> list[BacklogEntry]:
    return list_entries()[-n:]


__all__ = ["BacklogEntry", "list_entries", "append_entry", "update_status", "tail"]
