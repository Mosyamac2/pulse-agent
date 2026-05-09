"""Single point of contact with Claude Agent SDK.

All LLM calls go through this module. Handles model selection, budget logging,
and the small set of "simple query" helpers used by classification, planning,
review, and reflection. Tests should monkeypatch `_query_simple` and
`build_options` rather than the SDK itself.

The SDK runs the `claude` CLI as a subprocess; OAuth via the
`CLAUDE_CODE_OAUTH_TOKEN` env var is the only auth path. We never set
`ANTHROPIC_API_KEY`.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import PATHS

log = logging.getLogger(__name__)

# Model aliases the SDK accepts (short forms). The full IDs are kept for budget bookkeeping.
MODEL_HEAVY = "claude-opus-4-7"          # Opus 4.7 — plan, review, evolution, deep self-review
MODEL_LIGHT = "claude-sonnet-4-6"        # Sonnet 4.6 — chat, consciousness, safety

# Short aliases passed to ClaudeAgentOptions(model=...)
MODEL_ALIASES: dict[str, str] = {
    "opus": MODEL_HEAVY,
    "sonnet": MODEL_LIGHT,
    MODEL_HEAVY: MODEL_HEAVY,
    MODEL_LIGHT: MODEL_LIGHT,
}

# USD per million tokens — for advisory budget tracking only. Max plan is flat-rate.
PRICES_USD_PER_MTOK: dict[str, dict[str, float]] = {
    MODEL_HEAVY:  {"in": 15.0, "out": 75.0, "cache_in": 1.5,  "cache_out": 18.75},
    MODEL_LIGHT:  {"in": 3.0,  "out": 15.0, "cache_in": 0.3,  "cache_out": 3.75},
}


def normalize_model(name: str) -> str:
    return MODEL_ALIASES.get(name, name)


@dataclass
class Usage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def cost_usd(self) -> float:
        prices = PRICES_USD_PER_MTOK.get(self.model)
        if not prices:
            return 0.0
        m = 1_000_000.0
        return (
            self.input_tokens * prices["in"] / m
            + self.output_tokens * prices["out"] / m
            + self.cache_creation_input_tokens * prices["cache_out"] / m
            + self.cache_read_input_tokens * prices["cache_in"] / m
        )


def log_usage(usage: Usage, *, kind: str) -> None:
    """Append a usage record to data/logs/budget.jsonl."""
    PATHS.ensure()
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": kind,
        "model": usage.model,
        "in": usage.input_tokens,
        "out": usage.output_tokens,
        "cache_in": usage.cache_read_input_tokens,
        "cache_out": usage.cache_creation_input_tokens,
        "usd": round(usage.cost_usd(), 5),
    }
    with (PATHS.logs / "budget.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# --- SDK abstractions --------------------------------------------------------

def build_options(*, system_prompt: str, allowed_tools: list[str] | None = None,
                  mcp_servers: dict[str, Any] | None = None, model: str = MODEL_LIGHT,
                  permission_mode: str = "auto", max_turns: int = 15,
                  cwd: str | None = None, hooks: dict | None = None) -> Any:
    """Build a ClaudeAgentOptions object. Imported lazily so tests can run without SDK."""
    from claude_agent_sdk import ClaudeAgentOptions  # type: ignore
    kwargs: dict[str, Any] = dict(
        system_prompt=system_prompt,
        model=model,
        permission_mode=permission_mode,
        max_turns=max_turns,
    )
    if allowed_tools is not None:
        kwargs["allowed_tools"] = allowed_tools
    if mcp_servers is not None:
        kwargs["mcp_servers"] = mcp_servers
    if cwd is not None:
        kwargs["cwd"] = cwd
    if hooks is not None:
        kwargs["hooks"] = hooks
    return ClaudeAgentOptions(**kwargs)


async def _query_simple(prompt: str, model: str = "sonnet", *,
                        system: str | None = None, kind: str = "simple") -> str:
    """One-shot query. Returns concatenated assistant text. Streams via SDK `query()`.

    For tests, monkeypatch this function directly.
    """
    from claude_agent_sdk import query  # type: ignore

    full_model = normalize_model(model)
    options = build_options(
        system_prompt=system or "Ты помощник. Отвечай по делу.",
        allowed_tools=[],
        mcp_servers={},
        model=full_model,
        permission_mode="auto",
        max_turns=1,
    )

    out_chunks: list[str] = []
    usage = Usage(model=full_model)
    t0 = time.time()
    async for msg in query(prompt=prompt, options=options):
        # SDK message types differ between versions; we look at .text and .usage by attr.
        text = _extract_text(msg)
        if text:
            out_chunks.append(text)
        u = _extract_usage(msg, full_model)
        if u:
            usage = u
    log.info("llm.query kind=%s model=%s elapsed=%.2fs", kind, full_model, time.time() - t0)
    log_usage(usage, kind=kind)
    return "".join(out_chunks).strip()


def _extract_text(msg: Any) -> str:
    """Pull assistant text from SDK message regardless of variant."""
    # AssistantMessage(content=[TextBlock(text=...)])
    content = getattr(msg, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    out: list[str] = []
    for block in content:
        t = getattr(block, "text", None)
        if isinstance(t, str):
            out.append(t)
    return "".join(out)


def _extract_usage(msg: Any, model: str) -> Usage | None:
    """Pull a usage record from a ResultMessage if present."""
    u = getattr(msg, "usage", None)
    if u is None:
        return None
    # SDK passes a dict in newer versions.
    if isinstance(u, dict):
        return Usage(
            model=model,
            input_tokens=int(u.get("input_tokens", 0)),
            output_tokens=int(u.get("output_tokens", 0)),
            cache_creation_input_tokens=int(u.get("cache_creation_input_tokens", 0)),
            cache_read_input_tokens=int(u.get("cache_read_input_tokens", 0)),
        )
    return Usage(
        model=model,
        input_tokens=int(getattr(u, "input_tokens", 0)),
        output_tokens=int(getattr(u, "output_tokens", 0)),
        cache_creation_input_tokens=int(getattr(u, "cache_creation_input_tokens", 0)),
        cache_read_input_tokens=int(getattr(u, "cache_read_input_tokens", 0)),
    )


__all__ = [
    "MODEL_HEAVY",
    "MODEL_LIGHT",
    "PRICES_USD_PER_MTOK",
    "Usage",
    "normalize_model",
    "build_options",
    "log_usage",
    "_query_simple",
]
