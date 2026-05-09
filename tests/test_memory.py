"""Memory + reflection + backlog tests."""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

from pulse import memory


@pytest.fixture
def tmp_memory(tmp_path: Path):
    from pulse.config import PATHS
    object.__setattr__(PATHS, "data", tmp_path)
    object.__setattr__(PATHS, "memory", tmp_path / "memory")
    object.__setattr__(PATHS, "knowledge", tmp_path / "memory" / "knowledge")
    object.__setattr__(PATHS, "logs", tmp_path / "logs")
    object.__setattr__(PATHS, "state", tmp_path / "state")
    PATHS.ensure()
    return tmp_path


def test_bootstrap_writes_starter(tmp_memory: Path):
    memory.bootstrap_starter_files()
    assert memory.identity_path().exists()
    assert memory.scratchpad_path().exists()
    assert memory.patterns_path().exists()
    assert memory.backlog_path().exists()
    assert memory.feedback_classes_path().exists()


def test_append_scratchpad(tmp_memory: Path):
    memory.bootstrap_starter_files()
    memory.append_scratchpad("Открытый вопрос про emp_017.")
    txt = memory.read_scratchpad()
    assert "emp_017" in txt
    assert "## " in txt


def test_append_dated_section_locks(tmp_memory: Path):
    memory.bootstrap_starter_files()
    p = memory.scratchpad_path()
    n = 30
    def writer(i: int):
        memory.append_dated_section(p, f"запись {i}")
    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n)]
    for t in threads: t.start()
    for t in threads: t.join()
    txt = p.read_text(encoding="utf-8")
    for i in range(n):
        assert f"запись {i}" in txt


def test_backlog_append_and_list(tmp_memory: Path):
    from pulse.improvement_backlog import append_entry, list_entries, update_status
    e1 = append_entry("Сделать X", provenance="reflection:msg_001")
    e2 = append_entry("Сделать Y", provenance="evolution:fb-class-002")
    items = list_entries()
    assert len(items) == 2
    assert items[0].id == e1.id and items[0].intent == "Сделать X"
    assert items[1].provenance.startswith("evolution:")
    assert update_status(e1.id, "done")
    assert update_status(99999, "done") is False
    items2 = list_entries()
    assert items2[0].status == "done"


def test_pattern_register(tmp_memory: Path):
    from pulse.pattern_register import append_observation, read_patterns
    append_observation("err-001", "ZeroDivisionError в attrition", structural_fix="guard division")
    txt = read_patterns()
    assert "err-001" in txt
    assert "guard division" in txt


def test_reflection_uses_stub(tmp_memory: Path, monkeypatch: pytest.MonkeyPatch):
    """The autouse stub in conftest replaces _query_simple — make sure reflection
    captures BACKLOG: lines from it (we craft a stub that emits two)."""
    from pulse import reflection, llm

    async def fake(prompt: str, model: str = "sonnet", *, system: str | None = None,
                   kind: str = "simple") -> str:
        return ("Рефлексия: всё прошло OK.\n\n"
                "BACKLOG: добавить тул для X\n"
                "BACKLOG: переформулировать пункт SYSTEM.md\n")

    monkeypatch.setattr(llm, "_query_simple", fake)
    monkeypatch.setattr(reflection, "_query_simple", fake)
    rec = asyncio.get_event_loop().run_until_complete(
        reflection.reflect(question="q", answer="a", tool_calls=[], message_id="msg_test")
    )
    assert len(rec["candidates"]) == 2
    from pulse.improvement_backlog import list_entries
    items = list_entries()
    intents = [i.intent for i in items]
    assert "добавить тул для X" in intents
    assert any("переформулировать" in i for i in intents)


def test_should_reflect():
    from pulse.reflection import should_reflect
    assert should_reflect(n_tool_calls=5, had_error=False)
    assert should_reflect(n_tool_calls=0, had_error=True)
    assert not should_reflect(n_tool_calls=0, had_error=False)
