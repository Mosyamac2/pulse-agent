"""Versioning + commit-review parsing tests.

Skips operations that touch the live git repo (those are exercised in the
Phase-8 evolution dry-run test). Here we focus on the pure logic:
parse, bump, sync to artifact files, and JSON-verdict parser.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest


@pytest.fixture
def tmp_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Mirror the live repo's release artifacts into a tmp dir, rebind PATHS."""
    REAL = Path(__file__).resolve().parent.parent
    for name in ["VERSION", "pyproject.toml", "README.md", "BIBLE.md"]:
        shutil.copy(REAL / name, tmp_path / name)
    (tmp_path / "docs").mkdir()
    shutil.copy(REAL / "docs" / "ARCHITECTURE.md", tmp_path / "docs" / "ARCHITECTURE.md")
    shutil.copy(REAL / "docs" / "CHECKLISTS.md", tmp_path / "docs" / "CHECKLISTS.md")
    (tmp_path / "prompts").mkdir()
    for f in (REAL / "prompts").glob("*.md"):
        shutil.copy(f, tmp_path / "prompts" / f.name)

    from pulse.config import PATHS
    object.__setattr__(PATHS, "repo", tmp_path)
    object.__setattr__(PATHS, "version_file", tmp_path / "VERSION")
    object.__setattr__(PATHS, "architecture_doc", tmp_path / "docs" / "ARCHITECTURE.md")
    object.__setattr__(PATHS, "bible", tmp_path / "BIBLE.md")
    object.__setattr__(PATHS, "prompts", tmp_path / "prompts")
    return tmp_path


def test_parse_round_trip():
    from pulse.version_ops import parse
    for s in ["1.2.3", "0.0.1", "0.1.0-rc.0", "0.1.0-rc.42"]:
        assert str(parse(s)) == s


def test_bump_rc(tmp_repo: Path):
    from pulse.version_ops import current, bump
    cur = current()
    assert cur.rc is not None  # the live tree is on -rc.N
    nxt = bump("rc")
    assert nxt.rc == cur.rc + 1
    txt = (tmp_repo / "VERSION").read_text(encoding="utf-8").strip()
    assert txt == str(nxt)
    assert f'version = "{nxt}"' in (tmp_repo / "pyproject.toml").read_text(encoding="utf-8")
    assert f"version-{nxt.badge}-blue" in (tmp_repo / "README.md").read_text(encoding="utf-8")
    assert str(nxt) in (tmp_repo / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8").splitlines()[0]


def test_bump_minor_resets(tmp_repo: Path):
    from pulse.version_ops import current, bump, parse
    nxt = bump("minor")
    assert nxt.rc is None
    assert nxt.patch == 0


def test_bump_changelog_line(tmp_repo: Path):
    from pulse.version_ops import bump
    nxt = bump("rc", changelog_line="новая фича X")
    md = (tmp_repo / "README.md").read_text(encoding="utf-8")
    assert f"`v{nxt}` — новая фича X" in md


def test_assert_in_sync(tmp_repo: Path):
    from pulse.version_ops import assert_in_sync, bump
    bump("rc")  # ensure all 4 artifacts updated together
    assert_in_sync()


def test_protected_path_match():
    from pulse.git_ops import is_protected_path
    assert is_protected_path("BIBLE.md")
    assert is_protected_path("pulse/safety.py")
    assert is_protected_path("pulse/data_engine/schema.py")
    assert is_protected_path("pulse/llm.py")
    assert not is_protected_path("prompts/SYSTEM.md")
    assert not is_protected_path("data/memory/identity.md")


# --- commit_review parser --------------------------------------------------

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_parse_verdict_fenced_json():
    from pulse.commit_review import parse_verdict
    raw = """Тут какая-то преамбула.
```json
{"verdict": "pass", "findings": [], "reasoning": "ok"}
```
"""
    v = parse_verdict(raw)
    assert v.verdict == "pass"
    assert v.reasoning == "ok"


def test_parse_verdict_bare_json():
    from pulse.commit_review import parse_verdict
    raw = '{"verdict":"block","findings":[{"item":"version_sync","severity":"critical","detail":"x"}],"reasoning":"diff drift"}'
    v = parse_verdict(raw)
    assert v.is_block
    assert len(v.findings) == 1


def test_parse_verdict_garbage_falls_back():
    from pulse.commit_review import parse_verdict
    v = parse_verdict("это не json")
    assert v.verdict == "pass_with_advisory"  # graceful degrade


def test_review_uses_stub(tmp_repo: Path, monkeypatch):
    from pulse import commit_review, llm

    async def fake(prompt: str, model: str = "sonnet", *, system=None, kind="simple"):
        assert "BIBLE" in prompt or "Конституция" in prompt
        return '{"verdict":"pass","findings":[],"reasoning":"ok"}'

    monkeypatch.setattr(llm, "_query_simple", fake)
    monkeypatch.setattr(commit_review, "_query_simple", fake)
    v = _run(commit_review.review(diff="diff text", new_version="0.1.1",
                                   commit_message="v0.1.1: x"))
    assert v.verdict == "pass"
