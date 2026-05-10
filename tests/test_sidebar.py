"""Smoke tests for the sidebar aggregations added to pulse.dashboard in v1.9.0."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pulse.data_engine.seed import seed


@pytest.fixture(scope="module")
def seeded_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db_path = tmp_path_factory.mktemp("sidebar") / "sber_hr.db"
    seed(db_path, force=True)
    return db_path


@pytest.fixture
def db(seeded_db: Path):
    from sqlite_utils import Database
    return Database(seeded_db)


def test_archetype_counts_are_8_or_fewer(db):
    from pulse.dashboard import get_archetype_counts
    rows = get_archetype_counts(db=db)
    assert 1 <= len(rows) <= 8
    total = sum(r["count"] for r in rows)
    assert total >= 1
    counts = [r["count"] for r in rows]
    assert counts == sorted(counts, reverse=True)


def test_department_counts_filter_empty_units(db):
    from pulse.dashboard import get_department_counts
    rows = get_department_counts(db=db)
    assert len(rows) >= 1
    for r in rows:
        assert r["count"] >= 1
        assert r["unit_id"]
        assert r["name"]


def test_recent_threads_reads_jsonl(tmp_path):
    from pulse.dashboard import get_recent_threads
    p = tmp_path / "chat.jsonl"
    rows = [
        {"ts": "2026-05-09T10:00:00+00:00", "message_id": "m1",
         "question": "первый вопрос", "answer": "первый ответ"},
        {"ts": "2026-05-09T10:01:00+00:00", "message_id": "m2",
         "question": "второй", "answer": "второй ответ"},
        {"ts": "2026-05-09T10:02:00+00:00", "message_id": "m3",
         "question": "третий", "answer": "третий ответ"},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = get_recent_threads(n=2, path=p)
    assert len(out) == 2
    # Newest-first
    assert out[0]["message_id"] == "m3"
    assert out[1]["message_id"] == "m2"


def test_recent_threads_missing_file_is_empty(tmp_path):
    from pulse.dashboard import get_recent_threads
    out = get_recent_threads(n=10, path=tmp_path / "missing.jsonl")
    assert out == []


def test_employee_index_returns_active_only(db):
    from pulse.dashboard import get_employee_index
    rows = get_employee_index(db=db)
    assert len(rows) >= 1
    for r in rows:
        assert r["emp_id"].startswith("emp_")
        assert r["full_name"]
    # ordered alphabetically
    names = [r["full_name"] for r in rows]
    assert names == sorted(names)
