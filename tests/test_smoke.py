"""Phase-0 smoke test: imports + boot-time sanity, without hitting the SDK."""
from __future__ import annotations

import importlib
import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_version_file_exists_and_parses():
    raw = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert re.match(r"^\d+\.\d+\.\d+(-rc\.\d+)?$", raw), raw


def test_pyproject_version_in_sync():
    txt = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    raw = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert f'version = "{raw}"' in txt, "pyproject.toml version drifted from VERSION"


def test_readme_badge_in_sync():
    txt = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    raw = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    badge = raw.replace("-", "--")
    assert badge in txt, f"README badge missing version {badge}"


def test_arch_doc_header_in_sync():
    txt = (REPO_ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    raw = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert raw in txt.splitlines()[0], "docs/ARCHITECTURE.md header drifted from VERSION"


def test_protected_paths_listed():
    bible = (REPO_ROOT / "BIBLE.md").read_text(encoding="utf-8")
    for p in ["BIBLE.md", "pulse/safety.py", "prompts/SAFETY.md", "pulse/data_engine/schema.py"]:
        assert p in bible


def test_pulse_imports():
    import pulse
    assert pulse.__version__


def test_pulse_config_paths():
    from pulse.config import PATHS, SETTINGS, read_version
    assert PATHS.repo.exists()
    assert PATHS.bible.exists()
    assert PATHS.version_file.exists()
    assert read_version() == (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert SETTINGS.host
    assert SETTINGS.port > 0


def test_no_anthropic_api_key_in_env_example():
    txt = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    # The example must NOT define ANTHROPIC_API_KEY=<value>; it should warn against it.
    assert not re.search(r"^ANTHROPIC_API_KEY\s*=\s*\S", txt, flags=re.M)


def test_server_module_boots():
    from pulse import server
    # FastAPI app present, /health returns dict.
    assert server.app is not None
    from fastapi.testclient import TestClient
    client = TestClient(server.app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
