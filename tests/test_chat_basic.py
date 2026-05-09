"""Chat-loop and FastAPI plumbing.

Live SDK calls are out of scope (no OAuth token in CI). We monkeypatch
`pulse.chat.handle_chat` to a stub and exercise:

* `build_system_prompt()` produces a non-empty assembly that includes BIBLE.md.
* POST /api/chat returns the stubbed answer + a fresh message_id.
* POST /api/feedback writes to `feedback.jsonl`.
* GET /api/history reads back the chat log.
* GET /api/employees/{emp_id} hits the seeded DB.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pulse.data_engine.seed import seed


@pytest.fixture
def app_with_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Spin up an isolated repo layout with a fresh DB and rebound PATHS."""
    db_path = tmp_path / "sber_hr.db"
    seed(db_path, force=True)

    from pulse.config import PATHS
    object.__setattr__(PATHS, "data", tmp_path)
    object.__setattr__(PATHS, "db", db_path)
    object.__setattr__(PATHS, "memory", tmp_path / "memory")
    object.__setattr__(PATHS, "knowledge", tmp_path / "memory" / "knowledge")
    object.__setattr__(PATHS, "logs", tmp_path / "logs")
    object.__setattr__(PATHS, "state", tmp_path / "state")
    object.__setattr__(PATHS, "ml_models", tmp_path / "ml_models")
    PATHS.ensure()

    from pulse import server, chat

    async def fake_handle_chat(question, history=None, model="sonnet"):
        msg_id = chat._new_message_id()
        chat.log_chat(question, "fake answer for: " + question, msg_id,
                      {"model": model, "tool_calls": [], "history_len": 0})
        return {"message_id": msg_id, "answer": "fake answer for: " + question, "meta": {}}

    monkeypatch.setattr(chat, "handle_chat", fake_handle_chat, raising=True)
    monkeypatch.setattr(server, "handle_chat", fake_handle_chat, raising=False)

    return TestClient(server.app)


def test_build_system_prompt_smoke(tmp_path, monkeypatch):
    """The system prompt should include BIBLE.md and SYSTEM.md content."""
    from pulse.chat import build_system_prompt
    sp = build_system_prompt()
    assert "Конституция Пульса" in sp
    assert "Я — Пульс" in sp
    assert "Data Sources Registry" in sp


def test_health(app_with_db: TestClient):
    r = app_with_db.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db_present"] is True


def test_chat_endpoint(app_with_db: TestClient):
    r = app_with_db.post("/api/chat", json={"question": "Привет"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "answer" in body
    assert re.match(r"^msg_\d{4}-\d{2}-\d{2}_[0-9a-f]{6}$", body["message_id"])


def test_feedback_endpoint(app_with_db: TestClient, tmp_path: Path):
    chat_resp = app_with_db.post("/api/chat", json={"question": "test"}).json()
    msg_id = chat_resp["message_id"]
    r = app_with_db.post("/api/feedback", json={
        "message_id": msg_id, "verdict": "down", "comment": "слишком общо",
    })
    assert r.status_code == 200
    from pulse.config import PATHS
    log_path = PATHS.logs / "feedback.jsonl"
    line = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
    rec = json.loads(line)
    assert rec["message_id"] == msg_id
    assert rec["verdict"] == "down"
    assert rec["comment"] == "слишком общо"


def test_feedback_validation(app_with_db: TestClient):
    r = app_with_db.post("/api/feedback", json={"message_id": "x", "verdict": "maybe"})
    assert r.status_code == 422


def test_history_endpoint(app_with_db: TestClient):
    app_with_db.post("/api/chat", json={"question": "первый"})
    app_with_db.post("/api/chat", json={"question": "второй"})
    r = app_with_db.get("/api/history?limit=5").json()
    assert len(r["items"]) >= 2
    assert r["items"][-1]["question"] == "второй"


def test_employee_endpoint(app_with_db: TestClient):
    r = app_with_db.get("/api/employees/emp_001")
    assert r.status_code == 200
    assert r.json()["emp_id"] == "emp_001"


def test_employee_not_found(app_with_db: TestClient):
    r = app_with_db.get("/api/employees/emp_999")
    assert r.status_code == 404
