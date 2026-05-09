"""Tests for `pulse/data_engine/marts.py` — pure SQL aggregations.

We seed an isolated DB with `data_engine.seed.seed(force=True)` (the same
fixture used elsewhere) and exercise every mart on it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pulse.data_engine.seed import seed


@pytest.fixture(scope="module")
def seeded_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db_path = tmp_path_factory.mktemp("marts") / "sber_hr.db"
    seed(db_path, force=True)
    return db_path


@pytest.fixture
def db(seeded_db: Path):
    from sqlite_utils import Database
    return Database(seeded_db)


def test_metric_registry_has_all_columns():
    from pulse.data_engine.marts import METRIC_REGISTRY, list_metric_names
    names = list_metric_names()
    assert "tasks_done" in names
    assert "focus_score" in names
    assert "stress_index" in names
    assert "peer_sentiment" in names
    for name, spec in METRIC_REGISTRY.items():
        for k in ("table", "col", "direction", "label", "scale"):
            assert k in spec, f"{name} missing {k}"


def test_metric_meta_unknown_raises():
    from pulse.data_engine.marts import metric_meta
    with pytest.raises(ValueError, match="Unknown metric"):
        metric_meta("nonsense")


def test_top_employees_by_metric_returns_ranked_rows(db):
    from pulse.data_engine.marts import top_employees_by_metric
    rows = top_employees_by_metric("focus_score", last_days=30, n=5, db=db)
    assert 1 <= len(rows) <= 5
    # Descending by value
    values = [r["value"] for r in rows]
    assert values == sorted(values, reverse=True)
    # All rows have full enrichment
    for r in rows:
        assert r["emp_id"]
        assert r["full_name"]
        assert r["value"] is not None
        assert r["n_days"] >= 5  # HAVING clause


def test_top_employees_ascending_inverts_order(db):
    from pulse.data_engine.marts import top_employees_by_metric
    desc = top_employees_by_metric("focus_score", last_days=30, n=5, db=db)
    asc = top_employees_by_metric("focus_score", last_days=30, n=5,
                                    ascending=True, db=db)
    if desc and asc:
        # The top of the desc list should have a higher value than the top of asc.
        assert desc[0]["value"] >= asc[0]["value"]


def test_metric_distribution_quartile_ordering(db):
    from pulse.data_engine.marts import metric_distribution
    d = metric_distribution("stress_index", last_days=30, db=db)
    assert d["n_employees"] >= 1
    assert d["min"] <= d["p25"] <= d["p50"] <= d["p75"] <= d["max"]
    assert d["scale"] == "0–1"


def test_aggregate_metric_by_unit(db):
    from pulse.data_engine.marts import aggregate_metric_by
    rows = aggregate_metric_by("focus_score", group_by="unit",
                                  last_days=30, db=db)
    assert len(rows) >= 1
    for r in rows:
        assert r["group_label"] is not None or r["group_id"] is not None
        assert r["n_employees"] >= 1


def test_aggregate_metric_by_invalid_group_raises(db):
    from pulse.data_engine.marts import aggregate_metric_by
    with pytest.raises(ValueError, match="Unknown group_by"):
        aggregate_metric_by("focus_score", group_by="planet", db=db)


def test_top_collab_connectors_both_modes(db):
    from pulse.data_engine.marts import top_collab_connectors
    by_weight = top_collab_connectors(by="weight_sum", n=5, db=db)
    by_degree = top_collab_connectors(by="degree", n=5, db=db)
    assert len(by_weight) >= 1
    assert len(by_degree) >= 1
    for r in by_weight + by_degree:
        assert r["degree"] >= 1
        assert r["weight_sum"] is not None


def test_efficiency_ranking_is_explainable(db):
    from pulse.data_engine.marts import efficiency_ranking
    rows = efficiency_ranking(last_days=30, n=10, db=db)
    assert 1 <= len(rows) <= 10
    for r in rows:
        # Score is approximately reproducible from the rounded inputs we
        # expose. Tolerance allows for double-rounding drift between the
        # SQL ROUND(score, 3) and recomputing from already-rounded fields.
        expected = (r["tasks_per_day"] / max(r["hours_per_day"], 4.0)) \
                    * (0.5 + r["focus_avg"])
        assert abs(r["score"] - expected) < 5e-3, (
            f"score={r['score']} expected≈{expected} for {r['emp_id']}"
        )
    # Descending order
    scores = [r["score"] for r in rows]
    assert scores == sorted(scores, reverse=True)
