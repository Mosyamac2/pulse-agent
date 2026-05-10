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


# ---------------------------------------------------------------------------
# Phase E2 — dock + slide-up overlay + "open in Pulse" handoff
# ---------------------------------------------------------------------------

class TestDock:
    def test_shell_has_dock(self, client: TestClient):
        body = client.get("/").text
        assert 'id="dock"' in body
        assert 'id="dock-input"' in body
        assert 'id="dock-send"' in body

    def test_shell_has_overlay(self, client: TestClient):
        body = client.get("/").text
        assert 'id="overlay"' in body
        assert 'id="overlay-open-pulse"' in body
        assert 'id="overlay-close"' in body

    def test_shell_streams_via_chat_endpoint(self, client: TestClient):
        # The dock JS calls /api/chat/stream with [Контекст вкладки: …] prefix.
        # We just verify the shell HTML references that endpoint.
        body = client.get("/").text
        assert "/api/chat/stream" in body
        assert "Контекст вкладки" in body

    def test_shell_hides_dock_on_pulse_tab(self, client: TestClient):
        body = client.get("/").text
        # CSS rule that hides dock when activeTab==pulse.
        assert 'data-active-tab="pulse"' in body
        assert 'body[data-active-tab="pulse"] .dock' in body

    def test_panel_feedback_button_present(self, client: TestClient):
        """Phase J: per-tab feedback button posts to /api/feedback/general
        with `[panel-<tab>]` prefix → evolution loop sees it as panel class."""
        body = client.get("/").text
        assert 'id="panel-feedback"' in body
        assert "/api/feedback/general" in body
        assert "[panel-" in body  # the JS prefix template
