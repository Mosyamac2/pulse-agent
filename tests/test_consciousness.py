"""Consciousness + deep_self_review tests."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


@pytest.fixture
def tmp_runtime(tmp_path: Path):
    from pulse.config import PATHS
    object.__setattr__(PATHS, "data", tmp_path)
    object.__setattr__(PATHS, "memory", tmp_path / "memory")
    object.__setattr__(PATHS, "knowledge", tmp_path / "memory" / "knowledge")
    object.__setattr__(PATHS, "logs", tmp_path / "logs")
    object.__setattr__(PATHS, "state", tmp_path / "state")
    object.__setattr__(PATHS, "ml_models", tmp_path / "ml_models")
    PATHS.ensure()
    from pulse.memory import bootstrap_starter_files
    bootstrap_starter_files()
    return tmp_path


def test_run_one_step_advances_state(tmp_runtime):
    from pulse.consciousness import run_one_maintenance_step, STEPS
    from pulse.state import load_state
    n_before = load_state().get("consciousness", {}).get("wakeups_total", 0)
    res = run_one_maintenance_step()
    assert res["step"] in {name for name, _ in STEPS}
    state = load_state()
    cs = state["consciousness"]
    assert cs["wakeups_total"] == n_before + 1
    assert cs["rotation_idx"] in range(len(STEPS))


def test_rotation_visits_all_steps(tmp_runtime):
    from pulse.consciousness import run_one_maintenance_step, STEPS
    seen: set[str] = set()
    for _ in range(len(STEPS) + 2):
        res = run_one_maintenance_step()
        seen.add(res["step"])
    assert seen == {name for name, _ in STEPS}


def test_thread_lifecycle(tmp_runtime):
    from pulse import consciousness
    consciousness.start(interval_s=60)
    assert consciousness.is_alive()
    # second start is a no-op
    consciousness.start(interval_s=60)
    assert consciousness.is_alive()
    consciousness.stop()


def test_deep_self_review_writes_file(tmp_runtime, monkeypatch):
    from pulse import deep_self_review, llm

    async def fake(prompt, model="sonnet", *, system=None, kind="simple"):
        return "Тестовая саморефлексия. Класс: дизлайки. Backlog: ничего."

    monkeypatch.setattr(llm, "_query_simple", fake)
    monkeypatch.setattr(deep_self_review, "_query_simple", fake)
    out = asyncio.get_event_loop().run_until_complete(deep_self_review.deep_self_review())
    assert "Тестовая" in out["text"]
    from pulse.config import PATHS
    p = PATHS.memory / "deep_review.md"
    assert p.exists()
    assert "Тестовая" in p.read_text(encoding="utf-8")
