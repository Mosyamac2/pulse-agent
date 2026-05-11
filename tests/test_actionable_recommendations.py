"""Smoke-test for the `actionable_recommendations` skill (fb-class-005).

The skill itself is a Markdown instruction file consumed by the model at
runtime; we cannot evaluate "did the model follow it?" inside pytest. What
we *can* do is enforce two structural invariants:

1. The skill file exists, parses, and declares the right triggers / format.
2. Any replay-style fixture of a *provocative* HR question (the five
   acceptance cases that historically slipped into HR theory) — once
   captured into `tests/replay_actionable_fixtures/*.md` — contains the
   structural markers of the "Что делать" block. Without those markers,
   the replay case fails and the regression is caught.

The fixture directory is intentionally optional: if it doesn't exist (e.g.
in a fresh checkout right after this skill was introduced and no replays
have been recorded yet), the fixture tests are skipped — but the skill
contract tests still run. As soon as anyone drops a replay `.md` in, it
becomes binding.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_FILE = REPO_ROOT / "skills" / "actionable_recommendations" / "SKILL.md"
FIXTURE_DIR = REPO_ROOT / "tests" / "replay_actionable_fixtures"


# ---------------------------------------------------------------------------
# Provocative questions — the 5 acceptance cases from the evolution plan.
# Each maps to an optional fixture filename. If a fixture is present, we
# grep it for the actionable markers; if absent, the test is skipped (not
# failed) so that early bootstrap doesn't go red.
# ---------------------------------------------------------------------------
PROVOCATIVE_REPLAYS: list[tuple[str, str]] = [
    ("dev_security_quarter.md", "Как развивать команду Безопасности в этом квартале?"),
    ("burnout_ops_clear.md", "Что делать с выгоранием в ops_clear?"),
    ("engagement_seleznyova.md", "Как повысить engagement Селезнёвой?"),
    ("system_risks_my_team.md", "Какие системные риски в моей команде и как их чинить?"),
    ("promote_shashkova_q3.md", "Стоит ли продвигать Шашкову в Q3?"),
]

# Markers that prove an answer carries an applied "Что делать" block.
# At least ONE family must match for the answer to count as actionable.
#   - "Что делать" header (the canonical block name)
#   - "План недели" / "План спринта" / "План на N недель" (allowed aliases)
#   - "Action items" (English alias)
#   - A markdown table that pairs Owner/Ответственный with Deadline/Срок
ACTIONABLE_MARKERS = [
    re.compile(r"(?im)^[#*\s]*Что\s+делать"),
    re.compile(r"(?im)^[#*\s]*План\s+(недели|спринта|на\s+\d)"),
    re.compile(r"(?im)^[#*\s]*Action\s*items"),
    re.compile(
        r"\|\s*(Owner|Ответствен\w+)\s*\|.*\|\s*(Deadline|Срок)\s*\|",
        re.IGNORECASE,
    ),
]


def _has_actionable_block(text: str) -> bool:
    return any(rx.search(text) for rx in ACTIONABLE_MARKERS)


# ---------------------------------------------------------------------------
# Contract tests for the skill file itself.
# ---------------------------------------------------------------------------
def test_skill_file_exists():
    assert SKILL_FILE.exists(), f"missing skill: {SKILL_FILE}"


def test_skill_frontmatter_declares_name_and_trigger():
    txt = SKILL_FILE.read_text(encoding="utf-8")
    assert txt.startswith("---"), "skill must start with YAML frontmatter"
    assert "name: actionable_recommendations" in txt
    assert "when_to_use:" in txt
    # The skill must mention fb-class-005 anchor so the regression source is traceable.
    assert "fb-class-005" in txt


def test_skill_declares_block_columns():
    txt = SKILL_FILE.read_text(encoding="utf-8")
    # The four-column contract is the whole point: Action / Owner / Deadline / Критерий.
    for col in ("Action", "Owner", "Deadline", "Критерий"):
        assert col in txt, f"skill must declare column {col!r}"


def test_skill_declares_self_check():
    txt = SKILL_FILE.read_text(encoding="utf-8")
    assert "Self-check" in txt or "self-check" in txt.lower()


def test_system_prompt_wires_skill_in():
    """SYSTEM.md must reference the skill so it isn't an orphan."""
    sys_prompt = (REPO_ROOT / "prompts" / "SYSTEM.md").read_text(encoding="utf-8")
    assert "actionable_recommendations" in sys_prompt
    assert "actionable-test" in sys_prompt or "actionable-test." in sys_prompt


# ---------------------------------------------------------------------------
# Replay-based check: provocative HR questions must produce an actionable block.
# Fixture format: plain `.md` file with the model's final answer.
# Drop a recorded answer into tests/replay_actionable_fixtures/<file>.md to
# bind the corresponding case.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fixture_name,question", PROVOCATIVE_REPLAYS)
def test_provocative_replay_has_actionable_block(fixture_name: str, question: str):
    fixture = FIXTURE_DIR / fixture_name
    if not fixture.exists():
        pytest.skip(
            f"no replay fixture yet for: {question!r} "
            f"(drop one at {fixture.relative_to(REPO_ROOT)} to bind this case)"
        )
    text = fixture.read_text(encoding="utf-8")
    assert _has_actionable_block(text), (
        f"replay for {question!r} is missing an actionable block "
        f"(expected one of: 'Что делать', 'План недели/спринта/N', 'Action items', "
        f"or a table with Owner+Deadline columns). "
        f"This is the fb-class-005 regression — the answer drifted back into HR theory."
    )


# ---------------------------------------------------------------------------
# Negative controls — fact / pure-analytics questions must NOT be forced
# into the actionable shape. We only document them here as a static list;
# they are exercised when matching fixtures are recorded.
# ---------------------------------------------------------------------------
CONTROL_REPLAYS: list[tuple[str, str]] = [
    ("fact_who_manages_emp009.md", "Кто менеджер emp_009?"),
    ("analytics_corr_stress_sleep.md", "Покажи корреляцию stress и sleep по компании"),
]


@pytest.mark.parametrize("fixture_name,question", CONTROL_REPLAYS)
def test_control_replay_is_not_forced_into_actionable(fixture_name: str, question: str):
    """Control case: factual / pure-analytics answers may omit the block.

    We don't assert the block is absent (skill says "optional" — model may
    still add it if it sees a red flag). We only assert the fixture loads
    and isn't accidentally empty, so the control stays a real check rather
    than dead code.
    """
    fixture = FIXTURE_DIR / fixture_name
    if not fixture.exists():
        pytest.skip(f"no control fixture yet for: {question!r}")
    text = fixture.read_text(encoding="utf-8").strip()
    assert text, f"control fixture {fixture_name} is empty"
