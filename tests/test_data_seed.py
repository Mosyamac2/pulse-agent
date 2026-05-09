"""Tests for the synthetic-data engine.

Runs the full seed in a temp DB. Slow-ish (~5–10s) but guards a critical
contract: the data the agent will reason over must satisfy these invariants.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import networkx as nx
import pytest
from sqlite_utils import Database

from pulse.data_engine.seed import seed, END_DATE, START_DATE


@pytest.fixture(scope="module")
def seeded_db(tmp_path_factory) -> Database:
    db_path = tmp_path_factory.mktemp("seed") / "sber_hr.db"
    seed(db_path, force=True)
    return Database(db_path)


def test_employees_count(seeded_db: Database):
    assert seeded_db["employees"].count == 100


def test_terminations_count(seeded_db: Database):
    rows = list(seeded_db.query("SELECT COUNT(*) AS n FROM employees WHERE status='terminated'"))
    assert rows[0]["n"] == 8


def test_archetypes_distribution(seeded_db: Database):
    rows = {r["archetype"]: r["n"] for r in seeded_db.query(
        "SELECT archetype, COUNT(*) AS n FROM employees GROUP BY archetype")}
    expected = {
        "newbie_enthusiast": 15, "tired_midfielder": 25, "star_perfectionist": 10,
        "quiet_rear_guard": 15, "drifting_veteran": 10, "toxic_high_performer": 5,
        "isolated_newbie": 10, "overwhelmed_manager": 10,
    }
    assert rows == expected


def test_units_and_positions(seeded_db: Database):
    assert seeded_db["units"].count == 12
    assert seeded_db["positions"].count == 20


def test_terminated_have_declining_tasks(seeded_db: Database):
    """For each terminated emp, mean tasks_done in last 30d before term must be lower
    than mean tasks_done 90-60d before term. Allows 1 false negative across 8."""
    failures = 0
    for row in seeded_db.query("SELECT emp_id, term_date FROM employees WHERE status='terminated'"):
        td = date.fromisoformat(row["term_date"])
        last30_start = (td - timedelta(days=30)).isoformat()
        last30_end = td.isoformat()
        prev_start = (td - timedelta(days=90)).isoformat()
        prev_end = (td - timedelta(days=60)).isoformat()
        last30 = list(seeded_db.query(
            "SELECT AVG(tasks_done) AS m FROM activity_daily WHERE emp_id=:e AND date>=:s AND date<=:e2",
            {"e": row["emp_id"], "s": last30_start, "e2": last30_end}))
        prev = list(seeded_db.query(
            "SELECT AVG(tasks_done) AS m FROM activity_daily WHERE emp_id=:e AND date>=:s AND date<=:e2",
            {"e": row["emp_id"], "s": prev_start, "e2": prev_end}))
        last_m = last30[0]["m"]
        prev_m = prev[0]["m"]
        if last_m is None or prev_m is None or last_m >= prev_m:
            failures += 1
    assert failures <= 1, f"too many terminated employees without declining trend: {failures}/8"


def test_collab_graph_connected(seeded_db: Database):
    g = nx.Graph()
    g.add_nodes_from([r["emp_id"] for r in seeded_db["employees"].rows])
    for r in seeded_db["collab_edges"].rows:
        g.add_edge(r["emp_a"], r["emp_b"], weight=r["weight"])
    components = list(nx.connected_components(g))
    assert len(components) == 1, f"expected single connected component, got {len(components)}"


def test_unit_aggregates_reasonable(seeded_db: Database):
    """Mean stress_index in last 90d differs across units (signal isn't flat)."""
    last_start = (END_DATE - timedelta(days=90)).isoformat()
    rows = list(seeded_db.query(
        """
        SELECT e.unit_id, AVG(w.stress_index) AS m
        FROM wearables_daily w
        JOIN employees e ON e.emp_id = w.emp_id
        WHERE w.date >= :s
        GROUP BY e.unit_id
        """, {"s": last_start}))
    means = [r["m"] for r in rows if r["m"] is not None]
    assert len(means) >= 5
    spread = max(means) - min(means)
    assert spread > 0.05, f"unit stress means too flat: spread={spread}"


def test_dates_within_window(seeded_db: Database):
    rows = list(seeded_db.query("SELECT MIN(date) AS lo, MAX(date) AS hi FROM activity_daily"))
    assert rows[0]["lo"] >= START_DATE.isoformat()
    assert rows[0]["hi"] <= END_DATE.isoformat()


def test_synthetic_snapshots_present(seeded_db: Database, tmp_path_factory):
    snap_dir = Path(seeded_db.conn.execute("PRAGMA database_list").fetchone()[2]).parent / "synthetic"
    for fname in ["employees.json", "units.json", "positions.json", "archetypes.json"]:
        assert (snap_dir / fname).exists(), fname
