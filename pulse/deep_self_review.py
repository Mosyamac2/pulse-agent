"""Deep self-review — one heavyweight Opus call against the full memory pack.

Triggered manually (CLI / endpoint) or by the consciousness loop on a long
cadence (multi-day). Output is saved to `data/memory/deep_review.md`,
overwriting the previous one — there is exactly one current snapshot.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import PATHS
from .llm import _query_simple
from .memory import (
    backlog_path,
    feedback_classes_path,
    identity_path,
    patterns_path,
    read_text,
    scratchpad_path,
)

log = logging.getLogger(__name__)


def _tail(path: Path, n_lines: int) -> str:
    if not path.exists():
        return ""
    return "\n".join(path.read_text(encoding="utf-8").splitlines()[-n_lines:])


def _bundle_pack() -> str:
    parts: list[str] = []
    parts.append("# BIBLE.md\n\n" + read_text(PATHS.bible))
    parts.append("\n\n# identity.md\n\n" + read_text(identity_path()))
    parts.append("\n\n# scratchpad.md (хвост)\n\n" + read_text(scratchpad_path())[-6_000:])
    parts.append("\n\n# patterns.md\n\n" + read_text(patterns_path()))
    parts.append("\n\n# feedback-classes.md\n\n" + read_text(feedback_classes_path()))
    parts.append("\n\n# improvement-backlog.md\n\n" + read_text(backlog_path()))
    parts.append("\n\n# logs/chat.jsonl (хвост 30)\n\n" + _tail(PATHS.logs / "chat.jsonl", 30))
    parts.append("\n\n# logs/events.jsonl (хвост 30)\n\n" + _tail(PATHS.logs / "events.jsonl", 30))
    return "\n".join(parts)


async def deep_self_review() -> dict[str, Any]:
    template = read_text(PATHS.prompts / "DEEP_SELF_REVIEW.md")
    pack = _bundle_pack()
    prompt = template + "\n\n---\n\n" + pack
    raw = await _query_simple(prompt, model="opus", kind="deep_self_review")
    out = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "text": raw,
    }
    PATHS.ensure()
    PATHS.memory.mkdir(parents=True, exist_ok=True)
    (PATHS.memory / "deep_review.md").write_text(
        f"# Deep Self-Review\n\n_Сгенерировано {out['ts']}_\n\n{raw}\n",
        encoding="utf-8",
    )
    rec_path = PATHS.logs / "events.jsonl"
    with rec_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": out["ts"], "kind": "deep_self_review_done"},
                            ensure_ascii=False) + "\n")
    return out


__all__ = ["deep_self_review"]
