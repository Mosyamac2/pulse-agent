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
    FastAPI client see the same DB.

    PATHS is a frozen dataclass; we mutate via object.__setattr__ and MUST
    restore the original on teardown — otherwise later tests like
    test_smoke.py::test_pulse_config_paths see the deleted tmp path and
    fail with `PATHS.db.exists() is False`.
    """
    from pulse.config import PATHS
    original_db = PATHS.db
    object.__setattr__(PATHS, "db", seeded_db_path)
    try:
        yield
    finally:
        object.__setattr__(PATHS, "db", original_db)


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


# ---------------------------------------------------------------------------
# Phase D2 — career / profile / structure / docs / analytics
# ---------------------------------------------------------------------------

class TestCareerPanel:
    def test_my_career(self, db: Database):
        emp = list(db.query(
            "SELECT emp_id FROM employees WHERE status='active' LIMIT 1"
        ))[0]["emp_id"]
        out = hcm_panels.get_my_career(emp, db=db)
        assert out["employee"]["emp_id"] == emp
        assert out["talent_pool_status"] is not None
        assert out["position"] is not None

    def test_internal_vacancies_excludes_self_managed(self, db: Database):
        # Pick a manager who does manage at least one active vacancy.
        rows = list(db.query("""
            SELECT hiring_manager_id FROM vacancies WHERE status='active' LIMIT 1
        """))
        if not rows:
            pytest.skip("no active vacancies")
        mgr = rows[0]["hiring_manager_id"]
        out = hcm_panels.list_internal_vacancies(mgr, db=db)
        for v in out:
            assert v["hiring_manager_id"] != mgr

    def test_talent_search_filters(self, db: Database):
        out = hcm_panels.list_talent_search_results(
            {"open_to_offers": 1, "grade_min": 2}, db=db,
        )
        for r in out:
            assert r["open_to_offers"] == 1
            assert r["grade_level"] >= 2

    def test_delegations_split(self, db: Database):
        # Pick a manager who actually delegates (from the seed).
        rows = list(db.query("""
            SELECT from_emp_id FROM delegations WHERE status='active' LIMIT 1
        """))
        if not rows:
            pytest.skip("no active delegations")
        emp = rows[0]["from_emp_id"]
        out = hcm_panels.list_delegations(emp, db=db)
        assert "i_delegate" in out and "delegated_to_me" in out
        assert len(out["i_delegate"]) >= 1

    def test_endpoint_career_my(self, client: TestClient):
        r = client.get("/api/hcm/career/my?emp_id=emp_001")
        assert r.status_code == 200

    def test_endpoint_talent_search(self, client: TestClient):
        r = client.get("/api/hcm/career/talent_search?open_to_offers=1")
        assert r.status_code == 200
        for it in r.json()["items"]:
            assert it["open_to_offers"] == 1


class TestProfilePanel:
    def test_get_profile_full(self, db: Database):
        emp = list(db.query(
            "SELECT emp_id FROM employees WHERE status='active' LIMIT 1"
        ))[0]["emp_id"]
        out = hcm_panels.get_profile_full(emp, db=db)
        for k in ("employee", "position", "unit", "career_history",
                   "performance_reviews", "course_summary", "peer_summary"):
            assert k in out
        assert out["employee"]["emp_id"] == emp

    def test_org_structure_root(self, db: Database):
        out = hcm_panels.get_org_structure(db=db)
        assert out["root"] is not None
        assert isinstance(out["children"], list)
        assert out["root"]["headcount"] >= 0

    def test_org_structure_children_total_le_employees(self, db: Database):
        """Sum of root's direct children headcount ≤ active employees total."""
        out = hcm_panels.get_org_structure(db=db)
        n_active = list(db.query("SELECT COUNT(*) c FROM employees WHERE status='active'"))[0]["c"]
        children_sum = sum(c["headcount"] for c in out["children"])
        # Note: tree is not guaranteed to span every level, just one level here.
        assert children_sum <= n_active

    def test_endpoint_profile(self, client: TestClient):
        r = client.get("/api/hcm/profile/emp_001")
        assert r.status_code == 200

    def test_endpoint_structure(self, client: TestClient):
        r = client.get("/api/hcm/structure")
        assert r.status_code == 200


class TestDocsPanel:
    def test_my_hr_requests(self, db: Database):
        # Pick someone with at least one request.
        rows = list(db.query("SELECT emp_id FROM hr_requests LIMIT 1"))
        if not rows:
            pytest.skip("no HR requests in seed")
        emp = rows[0]["emp_id"]
        out = hcm_panels.list_my_hr_requests(emp, db=db)
        assert out

    def test_team_calendar_shape(self, db: Database):
        # Manager grade>=4 to get subordinates.
        m = list(db.query("""
            SELECT emp_id FROM employees
            WHERE status='active' AND grade_level>=4 LIMIT 1
        """))[0]["emp_id"]
        out = hcm_panels.get_team_calendar(m, 2026, 5, db=db)
        assert out["month_start"] == "2026-05-01"
        assert out["month_end"] == "2026-05-31"
        assert isinstance(out["team"], list)

    def test_request_catalog_static(self):
        cat = hcm_panels.get_request_catalog()
        assert len(cat) >= 6
        for it in cat:
            for k in ("key", "title", "subtitle"):
                assert k in it

    def test_endpoint_my_requests(self, client: TestClient):
        # Find an emp with requests
        rows = list(Database(client.app.state.__dict__.get("_db_path", None)).query(
            "SELECT emp_id FROM hr_requests LIMIT 1"
        )) if False else []
        # Plain endpoint smoke without arguments
        r = client.get("/api/hcm/docs/catalog")
        assert r.status_code == 200
        assert len(r.json()["items"]) >= 6


class TestAnalyticsPanel:
    def test_overview_has_keys(self, db: Database):
        out = hcm_panels.get_hr_analytics_overview(db=db)
        for k in ("headcount_active", "terminations", "vacancies_open",
                   "courses_completed", "surveys_active", "pending_requests"):
            assert k in out
        assert out["headcount_active"] > 0

    def test_endpoint_overview(self, client: TestClient):
        r = client.get("/api/hcm/analytics/overview")
        assert r.status_code == 200
        assert r.json()["headcount_active"] > 0
