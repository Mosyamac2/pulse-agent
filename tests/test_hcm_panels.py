"""Phase D1 — HCM panels read-only API smoke + contract tests.

Reuses the seeded DB. Each test only runs the smallest needed query;
panels are pure Python over sqlite-utils so 4 dozen tests stay <1s.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlite_utils import Database

from pulse.data_engine.seed import seed
from pulse import hcm_panels


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def seeded_db_path(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("hcm_panels") / "sber_hr.db"
    seed(p, force=True)
    return p


@pytest.fixture(scope="module")
def db(seeded_db_path: Path) -> Database:
    return Database(seeded_db_path)


@pytest.fixture(scope="module")
def patched_paths(seeded_db_path: Path):
    """Point pulse.config.PATHS.db at the seeded fixture so panels and the
    FastAPI client see the same DB."""
    from pulse.config import PATHS
    object.__setattr__(PATHS, "db", seeded_db_path)
    yield


@pytest.fixture(scope="module")
def client(patched_paths) -> TestClient:
    from pulse.server import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# Recruit
# ---------------------------------------------------------------------------

class TestRecruitPanel:
    def test_summary_keys(self, db: Database, patched_paths):
        s = hcm_panels.get_recruit_summary(db=db)
        for k in ("active_count", "in_review_count", "draft_count",
                   "paused_count", "closed_count", "candidates_in_pipeline",
                   "avg_time_to_close_days"):
            assert k in s
        assert s["active_count"] == 5  # plan §C1 distribution
        assert s["closed_count"] == 10
        assert s["avg_time_to_close_days"] > 0

    def test_list_active_default(self, db: Database):
        rows = hcm_panels.list_active_vacancies(db=db, status="active")
        assert len(rows) == 5
        for r in rows:
            assert r["status"] == "active"
            assert r["hiring_manager_name"]
            assert r["days_open"] >= 0
            assert "candidates_count" in r

    def test_vacancy_detail_funnel(self, db: Database):
        # Pick a closed vacancy to ensure funnel has hired/rejected.
        rows = list(db.query("SELECT vacancy_id FROM vacancies WHERE status='closed' LIMIT 1"))
        v = hcm_panels.get_vacancy_detail(rows[0]["vacancy_id"], db=db)
        assert v is not None
        assert v["candidates_count"] > 0
        # closed: must have at least one of {hired, rejected}
        assert any(s in v["funnel"] for s in ("hired", "rejected"))

    def test_vacancy_detail_404(self, db: Database):
        assert hcm_panels.get_vacancy_detail("vac_does_not_exist", db=db) is None

    def test_endpoint_summary(self, client: TestClient):
        r = client.get("/api/hcm/recruit/summary")
        assert r.status_code == 200
        body = r.json()
        assert body["active_count"] == 5

    def test_endpoint_vacancies_filter(self, client: TestClient):
        r = client.get("/api/hcm/recruit/vacancies?status=closed")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 10
        for it in items:
            assert it["status"] == "closed"

    def test_endpoint_vacancy_detail_404(self, client: TestClient):
        r = client.get("/api/hcm/recruit/vacancies/vac_zzz")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

class TestGoalsPanel:
    def test_summary_company_default_period(self, db: Database):
        s = hcm_panels.get_goals_summary(db=db)
        assert s["scope"] == "company"
        assert s["period"] == "2026-Q2"
        assert s["goals_total"] > 50

    def test_summary_employee(self, db: Database):
        emp = list(db.query(
            "SELECT emp_id FROM employees WHERE status='active' "
            "AND archetype='star_perfectionist' LIMIT 1"
        ))[0]["emp_id"]
        s = hcm_panels.get_goals_summary(emp_id=emp, db=db)
        assert s["scope"] == "employee"
        assert s["emp_id"] == emp
        assert s["goals_total"] > 0

    def test_my_goals_attaches_krs(self, db: Database):
        emp = list(db.query(
            "SELECT emp_id FROM employees WHERE status='active' LIMIT 1"
        ))[0]["emp_id"]
        rows = hcm_panels.list_my_goals(emp, db=db)
        assert rows
        # Weights summing to 1.0 ± epsilon (also tested in test_hcm_seed).
        s = sum(g["weight"] for g in rows)
        assert 0.95 <= s <= 1.05
        # Each goal has a key_results list (possibly empty)
        for g in rows:
            assert "key_results" in g
            assert isinstance(g["key_results"], list)

    def test_team_goals_proxies_subordinates(self, db: Database):
        # Pick a manager with grade>=4 so the proxy "lower grade in same unit" yields rows.
        mgrs = list(db.query("""
            SELECT emp_id, unit_id FROM employees
            WHERE status='active' AND grade_level >= 4 LIMIT 1
        """))
        if not mgrs:
            pytest.skip("no grade>=4 manager available")
        rows = hcm_panels.list_team_goals(mgrs[0]["emp_id"], db=db)
        # Every row has the four count fields (possibly zero).
        for r in rows:
            for k in ("goals_total", "in_progress", "done", "proposed"):
                assert k in r

    def test_endpoint_goals_summary(self, client: TestClient):
        r = client.get("/api/hcm/goals/summary")
        assert r.status_code == 200
        assert r.json()["scope"] == "company"


# ---------------------------------------------------------------------------
# Learning
# ---------------------------------------------------------------------------

class TestLearningPanel:
    def test_feed_for_active_emp(self, db: Database):
        emp = list(db.query(
            "SELECT emp_id FROM employees WHERE status='active' LIMIT 1"
        ))[0]["emp_id"]
        rows = hcm_panels.get_learning_feed(emp, db=db)
        assert rows
        for r in rows:
            assert r["emp_id"] == emp
            assert r["recommended_reason"]

    def test_my_courses_basic(self, db: Database):
        emp = list(db.query(
            "SELECT emp_id FROM course_enrollments LIMIT 1"
        ))[0]["emp_id"]
        rows = hcm_panels.get_my_courses(emp, db=db)
        for r in rows:
            assert r["emp_id"] == emp
            assert r["title"]

    def test_my_courses_status_filter(self, db: Database):
        rows = hcm_panels.get_my_courses("emp_001", status="completed", db=db)
        for r in rows:
            assert r["status"] == "completed"

    def test_endpoint_feed(self, client: TestClient):
        r = client.get("/api/hcm/learning/feed?emp_id=emp_001")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Assess
# ---------------------------------------------------------------------------

class TestAssessPanel:
    def test_campaigns_split(self, db: Database):
        out = hcm_panels.get_assessment_campaigns(db=db)
        assert "active" in out and "completed" in out
        assert out["total"] == len(out["active"]) + len(out["completed"])

    def test_my_assessment(self, db: Database):
        # Pick someone with at least one performance_review.
        emp = list(db.query(
            "SELECT emp_id FROM performance_reviews LIMIT 1"
        ))[0]["emp_id"]
        out = hcm_panels.get_my_assessment(emp, db=db)
        assert out["emp_id"] == emp
        assert out["period"] is not None
        assert "peer_summary" in out

    def test_endpoint_campaigns(self, client: TestClient):
        r = client.get("/api/hcm/assess/campaigns")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body["completed"], list)
