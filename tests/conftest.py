"""Shared fixtures. Tests must NEVER hit live Claude API.

`pulse.llm._query_simple` and SDK entry points are stubbed here so unit tests run
on a clean machine without `CLAUDE_CODE_OAUTH_TOKEN` or the `claude` CLI.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace pulse.llm._query_simple with a deterministic stub by default."""
    async def fake(prompt: str, model: str = "sonnet", *, system: str | None = None,
                   kind: str = "simple") -> str:
        return f"[stub::{kind}::{model}] " + prompt[:120]
    try:
        from pulse import llm
    except Exception:  # pragma: no cover — pulse not importable at collection time
        return
    monkeypatch.setattr(llm, "_query_simple", fake, raising=False)


@pytest.fixture
def tmp_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Spin up a clone of the runtime layout in a tmp dir; isolates data/."""
    data = tmp_path / "data"
    (data / "logs").mkdir(parents=True)
    (data / "state").mkdir(parents=True)
    (data / "memory" / "knowledge").mkdir(parents=True)
    (data / "ml_models").mkdir(parents=True)
    (data / "synthetic").mkdir(parents=True)

    # Re-import config with overridden paths so PATHS/SETTINGS pick them up.
    monkeypatch.setenv("PULSE_REPO_DIR", str(REPO_ROOT))
    monkeypatch.setenv("PULSE_DATA_DIR", str(data))
    # purge cached pulse module so config rebinds.
    for k in list(sys.modules):
        if k == "pulse" or k.startswith("pulse."):
            del sys.modules[k]
    return tmp_path


def _run(coro: Any) -> Any:
    return asyncio.get_event_loop().run_until_complete(coro)
