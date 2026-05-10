"""Smoke tests for pulse.employee_card — hover-card + sparkline aggregations."""
from __future__ import annotations

from pathlib import Path

import pytest

from pulse.data_engine.seed import seed


@pytest.fixture(scope="module")
def seeded_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db_path = tmp_path_factory.mktemp("emp_card") / "sber_hr.db"
    seed(db_path, force=True)
    return db_path


@pytest.fixture
def db(seeded_db: Path):
    from sqlite_utils import Database
    return Database(seeded_db)


def test_card_returns_passport_for_known_emp(db):
    from pulse.employee_card import get_employee_card
    out = get_employee_card("emp_001", window=30, db=db)
    assert out is not None
    assert out["emp_id"] == "emp_001"
    assert out["full_name"]
    assert out["archetype"]
    assert out["unit_name"] is None or isinstance(out["unit_name"], str)
    assert isinstance(out["metrics"], list)
    assert len(out["metrics"]) == 4  # stress / sleep / focus / sentiment
    for m in out["metrics"]:
        assert -3.0 <= m["severity"] <= 3.0
        assert m["key"] in {"stress_index","sleep_h","focus_score","peer_sentiment"}
    assert isinstance(out["at_risk_flags"], list)
    assert isinstance(out["burnout_flags"], list)
    # tenure may be None if hire_date missing, otherwise a positive float
    if out["tenure_years"] is not None:
        assert out["tenure_years"] >= 0


def test_card_unknown_emp_returns_none(db):
    from pulse.employee_card import get_employee_card
    assert get_employee_card("emp_does_not_exist", db=db) is None


def test_sparkline_returns_30_points_when_data_present(db):
    from pulse.employee_card import get_sparkline
    out = get_sparkline("emp_001", "stress_index", window=30, db=db)
    assert out is not None
    assert out["metric"] == "stress_index"
    assert out["direction"] == "lower_is_better"
    assert isinstance(out["values"], list)
    if out["values"]:
        # At least some non-null values
        non_null = [v for v in out["values"] if v is not None]
        assert non_null
        assert out["min"] <= out["max"]
        assert out["min"] <= out["mean"] <= out["max"]


def test_sparkline_resolves_aliases(db):
    """Header label 'стресс' should resolve to stress_index."""
    from pulse.employee_card import get_sparkline
    out = get_sparkline("emp_001", "стресс", window=30, db=db)
    assert out is not None
    assert out["metric"] == "stress_index"


def test_sparkline_unknown_metric_returns_none(db):
    from pulse.employee_card import get_sparkline
    assert get_sparkline("emp_001", "made_up_metric", db=db) is None


def test_resolve_metric_coverage():
    from pulse.employee_card import resolve_metric
    assert resolve_metric("стресс") == "stress_index"
    assert resolve_metric("focus_score") == "focus_score"
    assert resolve_metric("Sleep, h/day") == "sleep_h"
    assert resolve_metric("гарбидж") is None
