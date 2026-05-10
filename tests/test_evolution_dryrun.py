"""Evolution loop dry-run.

We mock all three LLM calls and the SDK self-edit. The point is to verify
the state machine — gating, anti-oscillator, offset advance, commit-review
gate — without burning tokens or hitting the SDK.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from git import Repo


@pytest.fixture
def tmp_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up a tmp git repo mirroring the live tree's release artifacts and
    a tiny 'pulse' package layout that test_smoke.py needs to pass."""
    real = Path(__file__).resolve().parent.parent

    # copy minimal subset of the repo so test_smoke.py passes when
    # pytest is invoked under cwd=tmp_repo by `run_self_test`.
    for src_rel in ["BIBLE.md", "VERSION", "pyproject.toml", "README.md"]:
        shutil.copy(real / src_rel, tmp_path / src_rel)
    for d in ["docs", "prompts", "skills", "pulse", "tests", "scripts"]:
        shutil.copytree(real / d, tmp_path / d)

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "memory" / "knowledge").mkdir(parents=True)
    (tmp_path / "data" / "logs").mkdir()
    (tmp_path / "data" / "state").mkdir()

    # Seed feedback log with 6 downvotes (above threshold 5)
    fb = tmp_path / "data" / "logs" / "feedback.jsonl"
    chat = tmp_path / "data" / "logs" / "chat.jsonl"
    msg_ids = [f"msg_2026-05-09_{i:03x}" for i in range(6)]
    with chat.open("w", encoding="utf-8") as f:
        for mid in msg_ids:
            f.write(json.dumps({
                "ts": "2026-05-09T10:00:00Z",
                "message_id": mid,
                "question": "что с emp_017?",
                "answer": "автогенерированный ответ",
                "meta": {"tool_calls": [{"name": "get_employee_profile"}]},
            }, ensure_ascii=False) + "\n")
    with fb.open("w", encoding="utf-8") as f:
        for mid in msg_ids:
            f.write(json.dumps({
                "ts": "2026-05-09T10:01:00Z",
                "message_id": mid, "verdict": "down",
                "comment": "не учёл декретный статус",
            }, ensure_ascii=False) + "\n")

    # init a real git repo in tmp_path so commit_evolution can run
    repo = Repo.init(tmp_path)
    repo.config_writer().set_value("user", "email", "test@local").release()
    repo.config_writer().set_value("user", "name", "test").release()
    repo.git.add(A=True)
    repo.index.commit("initial")

    # rebind PATHS to the tmp repo
    from pulse.config import PATHS
    object.__setattr__(PATHS, "repo", tmp_path)
    object.__setattr__(PATHS, "data", tmp_path / "data")
    object.__setattr__(PATHS, "memory", tmp_path / "data" / "memory")
    object.__setattr__(PATHS, "knowledge", tmp_path / "data" / "memory" / "knowledge")
    object.__setattr__(PATHS, "logs", tmp_path / "data" / "logs")
    object.__setattr__(PATHS, "state", tmp_path / "data" / "state")
    object.__setattr__(PATHS, "ml_models", tmp_path / "data" / "ml_models")
    object.__setattr__(PATHS, "synthetic", tmp_path / "data" / "synthetic")
    object.__setattr__(PATHS, "version_file", tmp_path / "VERSION")
    object.__setattr__(PATHS, "architecture_doc", tmp_path / "docs" / "ARCHITECTURE.md")
    object.__setattr__(PATHS, "bible", tmp_path / "BIBLE.md")
    object.__setattr__(PATHS, "prompts", tmp_path / "prompts")
    object.__setattr__(PATHS, "skills", tmp_path / "skills")
    object.__setattr__(PATHS, "db", tmp_path / "data" / "sber_hr.db")

    from pulse.memory import bootstrap_starter_files
    bootstrap_starter_files()

    return tmp_path


def _stub_llm(monkeypatch, plan_yaml: str, classify_md: str = None,
               review_json: str = '{"verdict":"pass","findings":[],"reasoning":"ok"}'):
    classify_md = classify_md or """# Feedback Classes Register

| ID | Summary | Count | First seen | Last seen | Severity | Sample comment |
|----|---------|-------|------------|-----------|----------|---------------|
| fb-class-001 | Игнорирует декрет/больничный | 6 | 2026-05-09 | 2026-05-09 | high | "не учёл" |
"""

    async def fake_query(prompt, model="sonnet", *, system=None, kind="simple"):
        if kind == "evolution_classify":
            return classify_md
        if kind == "evolution_plan":
            return plan_yaml
        if kind == "commit_review":
            return review_json
        return ""

    from pulse import llm, evolution, commit_review
    monkeypatch.setattr(llm, "_query_simple", fake_query)
    monkeypatch.setattr(evolution, "_query_simple", fake_query)
    monkeypatch.setattr(commit_review, "_query_simple", fake_query)


def test_aggregate_feedback(tmp_repo: Path):
    from pulse.evolution import aggregate_feedback
    agg = aggregate_feedback()
    assert len(agg.new_downvotes) == 6
    assert agg.new_offset > 0


def test_threshold_skip_when_low(tmp_repo: Path, monkeypatch):
    # rewrite feedback to only 1 downvote
    fb = tmp_repo / "data" / "logs" / "feedback.jsonl"
    fb.write_text(fb.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
    _stub_llm(monkeypatch, plan_yaml="intent: x\n")
    from pulse.evolution import evolution_cycle
    res = asyncio.get_event_loop().run_until_complete(evolution_cycle(force=False))
    assert not res.triggered


def test_full_cycle_committed(tmp_repo: Path, monkeypatch):
    plan = """```yaml
intent: "перестать игнорировать декретный статус сотрудника"
class_addressed: "fb-class-001"
escalate_to_human: false
diff_targets:
  - "skills/employee_status/SKILL.md"
plan: |
  Усилить when_to_use в skill, явно перечислить триггеры
expected_effect: |
  Ответы про декретниц перестают предлагать курсы и предсказывать отток
risks: |
  Маленький — skill уже существует
acceptance: |
  Спросить про emp в декрете и убедиться, что Пульс упомянул статус
```"""
    _stub_llm(monkeypatch, plan_yaml=plan)

    # Stub run_self_test to skip the subprocess pytest
    from pulse import evolution
    monkeypatch.setattr(evolution, "run_self_test",
                         lambda: evolution.SelfTestResult(pytest_ok=True, protected_paths_touched=[]))

    res = asyncio.get_event_loop().run_until_complete(
        evolution.evolution_cycle(force=True, sdk_apply=False)
    )
    assert res.triggered
    assert res.committed is True
    assert res.version is not None
    # state advanced
    from pulse.state import load_state
    state = load_state()
    assert state["evolution"]["last_offset"] > 0
    assert state["evolution"]["last_version"] == res.version
    # version artifact updated
    new_v = (tmp_repo / "VERSION").read_text(encoding="utf-8").strip()
    assert new_v == res.version


def test_commit_review_blocks(tmp_repo: Path, monkeypatch):
    plan = """```yaml
intent: "fix x"
class_addressed: "fb-class-001"
diff_targets:
  - "skills/employee_status/SKILL.md"
plan: |
  do nothing
expected_effect: |
  none
risks: |
  none
acceptance: |
  X
```"""
    _stub_llm(monkeypatch, plan_yaml=plan,
               review_json='{"verdict":"block","findings":[{"item":"intent_clarity","severity":"critical","detail":"empty diff"}],"reasoning":"no real change"}')
    from pulse import evolution
    monkeypatch.setattr(evolution, "run_self_test",
                         lambda: evolution.SelfTestResult(pytest_ok=True, protected_paths_touched=[]))
    res = asyncio.get_event_loop().run_until_complete(
        evolution.evolution_cycle(force=True, sdk_apply=False)
    )
    assert res.triggered
    assert res.committed is False


def test_human_escalation(tmp_repo: Path, monkeypatch):
    """Since v1.4.0 the human-review gate fires only when the plan touches
    PROTECTED_PATHS (BIBLE.md, prompts/SAFETY.md, pulse/data_engine/schema.py).
    Other targets — even Python ones — auto-apply per the lifted policy."""
    plan = """```yaml
intent: "правка схемы БД"
class_addressed: "fb-class-007"
escalate_to_human: true
requires_human_review: true
diff_targets:
  - "pulse/data_engine/schema.py"
plan: |
  Нужно править схему БД
expected_effect: |
  Невозможно без явного согласия
risks: |
  Может сломать синтетику
acceptance: |
  X
```"""
    _stub_llm(monkeypatch, plan_yaml=plan)
    from pulse import evolution
    res = asyncio.get_event_loop().run_until_complete(
        evolution.evolution_cycle(force=True, sdk_apply=False)
    )
    assert res.triggered
    assert res.skipped_reason == "escalated_to_human"
    from pulse.improvement_backlog import list_entries
    items = list_entries()
    assert any(it.human_review for it in items)


def test_v140_bypass_for_non_protected_paths(tmp_repo: Path, monkeypatch):
    """A plan with escalate_to_human=true but no protected paths should
    bypass the gate (v1.4.0 policy). It might still fail elsewhere, but
    skipped_reason must NOT be 'escalated_to_human'."""
    plan = """```yaml
intent: "small UI tweak"
class_addressed: "fb-class-008"
escalate_to_human: true
requires_human_review: true
diff_targets:
  - "web/index.html"
  - "pulse/llm.py"
plan: |
  Frontend touch + harmless internal refactor
expected_effect: |
  Markdown renders correctly
risks: |
  None — gated by self-test
acceptance: |
  X
```"""
    _stub_llm(monkeypatch, plan_yaml=plan)
    from pulse import evolution
    monkeypatch.setattr(evolution, "run_self_test",
                         lambda: evolution.SelfTestResult(pytest_ok=True, protected_paths_touched=[]))
    res = asyncio.get_event_loop().run_until_complete(
        evolution.evolution_cycle(force=True, sdk_apply=False)
    )
    assert res.skipped_reason != "escalated_to_human"


def test_anti_oscillator(tmp_repo: Path, monkeypatch):
    """Three cycles addressing the same class triggers cooldown and human escalation."""
    plan = """```yaml
intent: "iter"
class_addressed: "fb-class-XYZ"
diff_targets:
  - "skills/employee_status/SKILL.md"
plan: |
  small touch
expected_effect: |
  none
risks: |
  none
acceptance: |
  X
```"""
    _stub_llm(monkeypatch, plan_yaml=plan)
    from pulse import evolution
    monkeypatch.setattr(evolution, "run_self_test",
                         lambda: evolution.SelfTestResult(pytest_ok=True, protected_paths_touched=[]))

    for _ in range(3):
        # Add a new downvote per cycle so the threshold is met fresh
        fb = tmp_repo / "data" / "logs" / "feedback.jsonl"
        with fb.open("a", encoding="utf-8") as f:
            for i in range(6):
                f.write(json.dumps({
                    "ts": "2026-05-09T10:01:00Z",
                    "message_id": f"msg_extra_{_}_{i}",
                    "verdict": "down", "comment": "x"}, ensure_ascii=False) + "\n")
        res = asyncio.get_event_loop().run_until_complete(
            evolution.evolution_cycle(force=True, sdk_apply=False)
        )
        assert res.triggered

    # After 3 same-class cycles, cooldown should be set
    from pulse.evolution import is_in_cooldown
    assert is_in_cooldown("fb-class-XYZ")


def test_lock_busy(tmp_repo: Path):
    from pulse.evolution import _acquire_lock, _release_lock, CycleLockBusy
    _acquire_lock()
    try:
        with pytest.raises(CycleLockBusy):
            _acquire_lock()
    finally:
        _release_lock()
