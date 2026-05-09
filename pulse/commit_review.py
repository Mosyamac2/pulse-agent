"""Single-model scope review against `docs/CHECKLISTS.md` (Opus 4.7).

Asks Opus to vet the staged diff with the checklist. Returns a structured
verdict. The evolution loop blocks the commit if `verdict == "block"`.

This is the entire P3 «immune system» surface — no triad reviewer, no
plan review, just one careful read by the heaviest model. Per TZ §3.3
step F.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .config import PATHS
from .llm import _query_simple

log = logging.getLogger(__name__)


@dataclass
class Verdict:
    verdict: str                       # pass | block | pass_with_advisory
    findings: list[dict[str, Any]] = field(default_factory=list)
    reasoning: str = ""
    raw: str = ""

    @property
    def is_block(self) -> bool:
        return self.verdict == "block"


# ---------------------------------------------------------------------------

def _read(p) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _checklists_md() -> str:
    return _read(PATHS.repo / "docs" / "CHECKLISTS.md")


def _commit_review_prompt() -> str:
    return _read(PATHS.prompts / "COMMIT_REVIEW.md")


def build_prompt(*, diff: str, new_version: str, commit_message: str,
                  intent: str = "", acceptance: str = "", replay_score: float | None = None) -> str:
    """Substitute placeholders in COMMIT_REVIEW.md and return the final prompt body."""
    template = _commit_review_prompt() or "Проверь коммит против чек-листа."
    replay = "—" if replay_score is None else f"{replay_score:.2f}"
    bible = _read(PATHS.bible)
    checklists = _checklists_md()
    return (
        template
        .replace("{bible}", bible)
        .replace("{checklists}", checklists)
        .replace("{diff}", diff[:30_000])
        .replace("{new_version}", new_version)
        .replace("{commit_message}", commit_message)
        .replace("{intent}", intent or "—")
        .replace("{acceptance}", acceptance or "—")
        .replace("{replay_score}", replay)
    )


_JSON_BLOCK_RX = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_BARE_JSON_RX = re.compile(r"^\s*(\{.*\})\s*$", re.DOTALL)


def parse_verdict(raw: str) -> Verdict:
    """Pull a JSON object from Opus's reply. Forgiving — strips fences,
    falls back to a regex search for the first {...} block."""
    body: str | None = None
    m = _JSON_BLOCK_RX.search(raw)
    if m:
        body = m.group(1)
    else:
        m = _BARE_JSON_RX.match(raw.strip())
        if m:
            body = m.group(1)
        else:
            # last-resort: greedy extract
            start = raw.find("{")
            end = raw.rfind("}")
            if 0 <= start < end:
                body = raw[start: end + 1]
    if not body:
        return Verdict(verdict="pass_with_advisory", reasoning="cannot parse verdict", raw=raw)
    try:
        obj = json.loads(body)
    except json.JSONDecodeError as ex:
        log.warning("commit_review: JSON parse failed: %s", ex)
        return Verdict(verdict="pass_with_advisory",
                        reasoning=f"json parse failed: {ex}", raw=raw)
    return Verdict(
        verdict=str(obj.get("verdict", "pass_with_advisory")),
        findings=list(obj.get("findings", []) or []),
        reasoning=str(obj.get("reasoning", "")),
        raw=raw,
    )


# ---------------------------------------------------------------------------

async def review(*, diff: str, new_version: str, commit_message: str,
                 intent: str = "", acceptance: str = "",
                 replay_score: float | None = None) -> Verdict:
    """One Opus call, parsed verdict. Tests stub `_query_simple`."""
    prompt = build_prompt(
        diff=diff, new_version=new_version, commit_message=commit_message,
        intent=intent, acceptance=acceptance, replay_score=replay_score,
    )
    raw = await _query_simple(prompt, model="opus", kind="commit_review")
    return parse_verdict(raw)


__all__ = ["Verdict", "build_prompt", "parse_verdict", "review"]
