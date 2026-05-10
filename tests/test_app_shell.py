"""Phase E1 — new app.html shell + /chat re-route.

Covers the routing decisions documented in BIBLE/ARCHITECTURE for v2.4.0:
  GET /          → web/app.html (new shell with module rail)
  GET /chat      → web/index.html (legacy direct chat UI, used by iframe)
  GET /dashboard → web/dashboard.html (CEO dashboard, unchanged)

Pure HTML smoke checks — no DB needed for routing tests.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client() -> TestClient:
    from pulse.server import app
    return TestClient(app)


class TestRouting:
    def test_root_serves_app_shell(self, client: TestClient):
        r = client.get("/")
        assert r.status_code == 200
        body = r.text
        # Hallmarks of the new shell:
        assert 'data-tab="pulse"' in body
        assert "Module rail" in body or "rail-btn" in body
        # The legacy iframe target is referenced
        assert 'src="/chat"' in body

    def test_chat_serves_legacy_index(self, client: TestClient):
        r = client.get("/chat")
        assert r.status_code == 200
        body = r.text
        # Legacy chat hallmarks (sidebar + thread)
        assert "sb-brand" in body or "id=\"thread\"" in body or "app-shell" in body

    def test_dashboard_unchanged(self, client: TestClient):
        r = client.get("/dashboard")
        assert r.status_code == 200

    def test_app_html_lists_all_nine_tabs(self, client: TestClient):
        r = client.get("/")
        body = r.text
        for tab in ("pulse", "profile", "recruit", "goals", "learning",
                    "assess", "career", "analytics", "docs", "comms"):
            assert f'data-tab="{tab}"' in body, f"tab pane {tab} missing"

    def test_app_html_has_rail_buttons(self, client: TestClient):
        r = client.get("/")
        body = r.text
        # Each module rail button has data-tab; check we have 10 (9 tabs + 1 brand link).
        # data-tab attribute appears in rail-btn buttons exactly 10 times
        # (including the section panes — we count rail-btn occurrences instead).
        assert body.count('class="rail-btn"') == 10

    def test_no_cache_headers(self, client: TestClient):
        r = client.get("/")
        cc = r.headers.get("cache-control", "")
        assert "no-cache" in cc.lower()


class TestQueryForwarding:
    """Server still forwards ?q= to the response — the actual transcoding
    of /?q=... to iframe `/chat?q=...` happens client-side in app.html JS,
    so we just confirm the page is served fine when q is present."""

    def test_root_with_q_still_serves_shell(self, client: TestClient):
        r = client.get("/?q=hello&tab=goals")
        assert r.status_code == 200
        assert 'data-tab="pulse"' in r.text  # shell content unchanged
