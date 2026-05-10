"""Phase C1 — HCM seed: vacancies + candidates determinism, FK integrity, distributions.

Phase C2 tests (goals, KR, learning_feed, talent_pool_status, delegations,
hr_requests, surveys_meta) will be appended to this file.

The full seed run is several seconds; we share a class-scoped reseeded DB
where reasonable, and use isolated tmp DBs for FK integrity assertions.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import numpy as np
import pytest
from sqlite_utils import Database

from pulse.data_engine.hcm_schema import create_hcm_tables
from pulse.data_engine.hcm_seed import gen_candidates, gen_vacancies
from pulse.data_engine.seed import END_DATE, seed


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def seeded_db(tmp_path_factory) -> Database:
    """One full seed run shared across the module — fast tests downstream."""
    p = tmp_path_factory.mktemp("hcm_seed") / "sber_hr.db"
    seed(p, force=True)
    return Database(p)


@pytest.fixture
def employees_fixture() -> list[dict]:
    """Minimal employee list for unit tests of generators (no DB)."""
    return [
        {"emp_id": f"emp_{i:03d}", "full_name": f"Иван{i} Петров",
         "status": "active", "grade_level": (i % 4) + 1,
         "position_id": f"pos_{(i % 5) + 1:03d}", "unit_id": "unit_root",
         "archetype": "newbie_enthusiast"}
        for i in range(1, 31)
    ]


@pytest.fixture
def positions_fixture() -> list[dict]:
    return [
        {"position_id": f"pos_{i:03d}", "title": f"Pos {i}",
         "type": ["IT", "sales", "analytics", "ops", "support"][i % 5],
         "grade_level": (i % 4) + 1}
        for i in range(1, 11)
    ]


@pytest.fixture
def units_fixture() -> list[dict]:
    return [
        {"unit_id": "unit_root", "name": "Корень", "level": 0},
        {"unit_id": "unit_a", "name": "A", "level": 1},
        {"unit_id": "unit_b", "name": "B", "level": 1},
    ]


# ---------------------------------------------------------------------------
# vacancies — shape and distributions
# ---------------------------------------------------------------------------

class TestVacancies:
    def test_count_in_expected_band(self, seeded_db: Database):
        n = seeded_db["vacancies"].count
        assert 18 <= n <= 28, f"vacancies count {n} outside expected band 18-28"

    def test_status_distribution_matches_targets(self, seeded_db: Database):
        rows = list(seeded_db.query("SELECT status, COUNT(*) c FROM vacancies GROUP BY status"))
        by_status = {r["status"]: r["c"] for r in rows}
        # Plan §C: ~5 active, ~3 in_review, ~2 paused, ~10 closed, ~3 draft.
        assert by_status.get("active", 0) == 5
        assert by_status.get("in_review", 0) == 3
        assert by_status.get("paused", 0) == 2
        assert by_status.get("closed", 0) == 10
        assert by_status.get("draft", 0) == 3

    def test_fk_position_unit_manager(self, seeded_db: Database):
        # Every vacancy.position_id must exist in positions.
        orphans = list(seeded_db.query(
            "SELECT v.vacancy_id FROM vacancies v "
            "LEFT JOIN positions p USING(position_id) WHERE p.position_id IS NULL"
        ))
        assert orphans == []
        # Every unit_id exists in units.
        orphans = list(seeded_db.query(
            "SELECT v.vacancy_id FROM vacancies v "
            "LEFT JOIN units u USING(unit_id) WHERE u.unit_id IS NULL"
        ))
        assert orphans == []
        # Every hiring_manager_id is an active employee with grade_level>=3.
        rows = list(seeded_db.query(
            "SELECT v.vacancy_id, e.status, e.grade_level FROM vacancies v "
            "LEFT JOIN employees e ON e.emp_id = v.hiring_manager_id"
        ))
        for r in rows:
            assert r["status"] == "active", f"vacancy {r['vacancy_id']} manager not active"
            assert r["grade_level"] >= 3, f"vacancy {r['vacancy_id']} manager grade {r['grade_level']} < 3"

    def test_recruiter_can_be_null_for_draft_or_in_review(self, seeded_db: Database):
        rows = list(seeded_db.query(
            "SELECT status, recruiter_id FROM vacancies WHERE recruiter_id IS NULL"
        ))
        # Plan §F2 — «без рекрутера» is a real bucket on the screen, only
        # makes sense for early statuses.
        for r in rows:
            assert r["status"] in ("draft", "in_review"), (
                f"NULL recruiter on status={r['status']}"
            )

    def test_dates_in_window(self, seeded_db: Database):
        rows = list(seeded_db.query("SELECT opened_date, target_close_date, closed_date FROM vacancies"))
        end_iso = END_DATE.isoformat()
        for r in rows:
            assert r["opened_date"] <= end_iso, f"opened_date in future: {r}"
            if r["closed_date"]:
                assert r["closed_date"] <= end_iso, f"closed_date in future: {r}"
                assert r["opened_date"] <= r["closed_date"], f"closed before opened: {r}"

    def test_closed_vacancies_within_last_90_days(self, seeded_db: Database):
        from datetime import date, timedelta
        cutoff = (END_DATE - timedelta(days=95)).isoformat()
        rows = list(seeded_db.query(
            "SELECT closed_date FROM vacancies WHERE status='closed'"
        ))
        for r in rows:
            assert r["closed_date"] >= cutoff, (
                f"closed vacancy older than 95 days: closed={r['closed_date']}"
            )

    def test_internal_only_share_in_band(self, seeded_db: Database):
        rows = list(seeded_db.query(
            "SELECT AVG(is_internal_only) AS pct FROM vacancies"
        ))
        pct = float(rows[0]["pct"])
        # Plan §C: ≈30%; widen test band to absorb small-N noise (n≤25).
        assert 0.10 <= pct <= 0.55, f"is_internal_only avg={pct} outside [0.10, 0.55]"


# ---------------------------------------------------------------------------
# candidates — shape and distributions
# ---------------------------------------------------------------------------

class TestCandidates:
    def test_at_least_one(self, seeded_db: Database):
        assert seeded_db["candidates"].count > 20

    def test_fk_vacancy(self, seeded_db: Database):
        orphans = list(seeded_db.query(
            "SELECT c.candidate_id FROM candidates c "
            "LEFT JOIN vacancies v USING(vacancy_id) WHERE v.vacancy_id IS NULL"
        ))
        assert orphans == []

    def test_internal_emp_id_only_for_internal_source(self, seeded_db: Database):
        rows = list(seeded_db.query(
            "SELECT source, internal_emp_id FROM candidates"
        ))
        for r in rows:
            if r["source"] == "internal":
                assert r["internal_emp_id"] is not None, "internal source missing internal_emp_id"
            else:
                assert r["internal_emp_id"] is None, (
                    f"non-internal source has internal_emp_id: {r}"
                )

    def test_internal_emp_exists_and_active(self, seeded_db: Database):
        rows = list(seeded_db.query(
            "SELECT c.candidate_id, e.status FROM candidates c "
            "LEFT JOIN employees e ON e.emp_id = c.internal_emp_id "
            "WHERE c.internal_emp_id IS NOT NULL"
        ))
        for r in rows:
            assert r["status"] == "active", (
                f"internal candidate points to non-active emp: {r}"
            )

    def test_closed_vacancies_have_hired_or_all_rejected(self, seeded_db: Database):
        # Each closed vacancy: ≥1 hired XOR all rejected.
        v_rows = list(seeded_db.query(
            "SELECT vacancy_id FROM vacancies WHERE status='closed'"
        ))
        for v in v_rows:
            cands = list(seeded_db.query(
                "SELECT funnel_stage FROM candidates WHERE vacancy_id = :v",
                {"v": v["vacancy_id"]}
            ))
            stages = Counter(c["funnel_stage"] for c in cands)
            hired = stages.get("hired", 0)
            others = sum(c for s, c in stages.items() if s != "hired")
            if hired == 0:
                # closed-without-hire: every candidate must be rejected
                assert all(c["funnel_stage"] == "rejected" for c in cands), (
                    f"closed vacancy {v['vacancy_id']}: zero hired but some still in pipeline"
                )
            else:
                assert hired == 1, (
                    f"closed vacancy {v['vacancy_id']}: hired count {hired} != 1"
                )

    def test_no_active_in_review_paused_have_hired(self, seeded_db: Database):
        for s in ("active", "in_review", "paused"):
            rows = list(seeded_db.query(
                "SELECT COUNT(*) c FROM candidates ca "
                "JOIN vacancies va USING(vacancy_id) "
                "WHERE va.status = :s AND ca.funnel_stage = 'hired'",
                {"s": s},
            ))
            assert rows[0]["c"] == 0, (
                f"premature hired on status={s}: {rows[0]['c']} candidates"
            )

    def test_score_band(self, seeded_db: Database):
        rows = list(seeded_db.query(
            "SELECT MIN(score) lo, MAX(score) hi FROM candidates"
        ))
        assert 0.0 <= rows[0]["lo"] <= 100.0
        assert 0.0 <= rows[0]["hi"] <= 100.0


# ---------------------------------------------------------------------------
# determinism — same RNG seed → same row counts and same first IDs
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_two_seeds_give_same_counts(self, tmp_path: Path):
        a = tmp_path / "a.db"
        b = tmp_path / "b.db"
        seed(a, force=True)
        seed(b, force=True)
        for t in ("vacancies", "candidates"):
            ca = Database(a)[t].count
            cb = Database(b)[t].count
            assert ca == cb, f"non-deterministic count for {t}: {ca} vs {cb}"

    def test_unit_generator_pure(self, employees_fixture, positions_fixture, units_fixture):
        # Pure-function determinism: same rng → same output (no DB roundtrip).
        rng1 = np.random.default_rng(123)
        rng2 = np.random.default_rng(123)
        v1 = gen_vacancies(rng1, employees_fixture, positions_fixture, units_fixture, END_DATE)
        v2 = gen_vacancies(rng2, employees_fixture, positions_fixture, units_fixture, END_DATE)
        assert v1 == v2
