"""Phase B — HCM façade schema smoke test.

Verifies that pulse.data_engine.hcm_schema.create_hcm_tables creates the
nine façade tables, is idempotent, and that the documented indexes exist.
The data itself is generated in Phase C (hcm_seed.py); here we only check
shape, so the suite stays fast.
"""
from __future__ import annotations

import sqlite3

import pytest
from sqlite_utils import Database

from pulse.data_engine.hcm_schema import create_hcm_tables


HCM_TABLES = (
    "vacancies", "candidates",
    "goals", "key_results",
    "learning_feed",
    "talent_pool_status",
    "delegations",
    "hr_requests",
    "surveys_meta",
)


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "hcm_test.db"
    return Database(p)


def test_create_hcm_tables_creates_all_nine(db: Database):
    create_hcm_tables(db)
    names = set(db.table_names())
    for t in HCM_TABLES:
        assert t in names, f"missing table: {t}"


def test_create_hcm_tables_is_idempotent(db: Database):
    create_hcm_tables(db)
    create_hcm_tables(db)  # second call must not raise
    create_hcm_tables(db)
    # all rows still 0 (no data inserted in Phase B)
    for t in HCM_TABLES:
        assert db[t].count == 0


def test_primary_keys_set(db: Database):
    create_hcm_tables(db)
    expected_pk = {
        "vacancies": ["vacancy_id"],
        "candidates": ["candidate_id"],
        "goals": ["goal_id"],
        "key_results": ["kr_id"],
        "learning_feed": ["feed_id"],
        "talent_pool_status": ["emp_id"],
        "delegations": ["delegation_id"],
        "hr_requests": ["request_id"],
        "surveys_meta": ["survey_id"],
    }
    for table, pks in expected_pk.items():
        assert list(db[table].pks) == pks, f"{table} PK mismatch"


def test_documented_indexes_present(db: Database):
    create_hcm_tables(db)
    # Spot-check the indexes that hcm_panels.py is going to lean on for fast
    # filtering. Index names are sqlite-utils auto-generated as
    # "idx_{table}_{col1}_{col2}".
    expected = {
        "vacancies": [["status"], ["unit_id"]],
        "candidates": [["vacancy_id"], ["funnel_stage"]],
        "goals": [["emp_id", "period"], ["status"]],
        "key_results": [["goal_id"]],
        "learning_feed": [["emp_id"], ["recommended_date"]],
        "delegations": [["from_emp_id"], ["to_emp_id"]],
        "hr_requests": [["emp_id"], ["status"]],
    }
    for table, idx_cols_list in expected.items():
        existing = [list(idx.columns) for idx in db[table].indexes]
        for cols in idx_cols_list:
            assert cols in existing, (
                f"{table} missing index on {cols}; have {existing}"
            )


def test_hook_in_seed():
    """Phase B: seed.py imports + calls create_hcm_tables right after create_tables."""
    src = (
        __import__("pulse.data_engine.seed", fromlist=["seed"])
        .__file__
    )
    txt = open(src, encoding="utf-8").read()
    assert "from .hcm_schema import create_hcm_tables" in txt
    assert "create_hcm_tables(db)" in txt


def test_existing_tables_untouched(db: Database):
    """Façade schema must not collide with the immune-core schema names.

    If anyone ever renames a façade table to one already used by the core
    (e.g. accidentally adds "courses" to hcm_schema), sqlite would raise on
    the second create_table — but only at seed time. This guard catches it
    in the unit suite.
    """
    from pulse.data_engine.schema import create_tables
    create_tables(db)
    create_hcm_tables(db)  # façade after core
    # No cross-collision; both sets coexist.
    have = set(db.table_names())
    assert "employees" in have  # core
    assert "vacancies" in have  # façade
