"""Smoke tests for in-process MCP tools.

We exercise each tool's underlying coroutine handler directly. The SDK
`@tool` decorator returns an `SdkMcpTool` whose `.handler` attribute is the
async function — call it with a dict and assert the response shape.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from sqlite_utils import Database

from pulse.data_engine.seed import seed
from pulse.data_engine.ml_train import train_all
from pulse.data_engine import ml_predict
from pulse.tools import (
    CHAT_TOOLS,
    EVOLUTION_TOOLS,
    chat_allowed_tools,
    evolution_allowed_tools,
)
from pulse.tools.data_tools import (
    get_collab_neighbors,
    get_employee_metrics,
    get_employee_profile,
    list_employees,
)
from pulse.tools.feedback_tools import get_recent_feedback
from pulse.tools.jira_tools import query_confluence, query_jira
from pulse.tools.knowledge_tools import knowledge_list, knowledge_read, knowledge_write
from pulse.tools.memory_tools import update_identity, update_scratchpad
from pulse.tools.ml_tools import predict_attrition, predict_role_success, recommend_courses
from pulse.tools.self_tools import repo_list, repo_read


@pytest.fixture(scope="module")
def runtime_data(tmp_path_factory):
    """Seed DB + train models in an isolated tmp dir; rebind PATHS."""
    base = tmp_path_factory.mktemp("tools")
    db_path = base / "sber_hr.db"
    seed(db_path, force=True)
    out_dir = base / "ml_models"
    train_all(db_path, out_dir, base / "logs")
    from pulse.config import PATHS
    object.__setattr__(PATHS, "repo", base)
    object.__setattr__(PATHS, "db", db_path)
    object.__setattr__(PATHS, "data", base)
    object.__setattr__(PATHS, "ml_models", out_dir)
    object.__setattr__(PATHS, "memory", base / "memory")
    object.__setattr__(PATHS, "knowledge", base / "memory" / "knowledge")
    object.__setattr__(PATHS, "logs", base / "logs")
    object.__setattr__(PATHS, "state", base / "state")
    PATHS.ensure()
    ml_predict.invalidate_cache()
    return {"base": base, "db": db_path}


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _text(resp) -> str:
    return resp["content"][0]["text"]


# --- data_tools ---

def test_employee_profile(runtime_data):
    db = Database(runtime_data["db"])
    emp = list(db.query("SELECT emp_id, full_name FROM employees LIMIT 1"))[0]
    resp = _run(get_employee_profile.handler({"emp_id": emp["emp_id"]}))
    assert emp["full_name"] in _text(resp)


def test_employee_profile_unknown(runtime_data):
    resp = _run(get_employee_profile.handler({"emp_id": "emp_999"}))
    assert resp.get("is_error")


def test_employee_metrics(runtime_data):
    db = Database(runtime_data["db"])
    emp_id = list(db.query("SELECT emp_id FROM employees WHERE status='active' LIMIT 1"))[0]["emp_id"]
    resp = _run(get_employee_metrics.handler({"emp_id": emp_id, "last_days": 30}))
    text = _text(resp)
    assert "Метрики" in text
    assert "stress" in text


def test_list_employees(runtime_data):
    resp = _run(list_employees.handler({"unit_id": "unit_it_back", "limit": 10}))
    assert "сотрудник" in _text(resp).lower()


def test_collab_neighbors(runtime_data):
    db = Database(runtime_data["db"])
    emp_id = list(db.query("SELECT emp_id FROM employees LIMIT 1"))[0]["emp_id"]
    resp = _run(get_collab_neighbors.handler({"emp_id": emp_id, "min_weight": 0.0}))
    assert _text(resp)


# --- ml_tools ---

def test_predict_attrition(runtime_data):
    db = Database(runtime_data["db"])
    emp_id = list(db.query("SELECT emp_id FROM employees WHERE status='active' LIMIT 1"))[0]["emp_id"]
    resp = _run(predict_attrition.handler({"emp_id": emp_id}))
    text = _text(resp)
    assert "P(увольнение" in text


def test_recommend_courses(runtime_data):
    db = Database(runtime_data["db"])
    emp_id = list(db.query("SELECT emp_id FROM employees LIMIT 1"))[0]["emp_id"]
    resp = _run(recommend_courses.handler({"emp_id": emp_id}))
    assert _text(resp)


def test_predict_role_success(runtime_data):
    db = Database(runtime_data["db"])
    emp_id = list(db.query("SELECT emp_id FROM employees LIMIT 1"))[0]["emp_id"]
    pos_id = list(db.query("SELECT position_id FROM positions LIMIT 1"))[0]["position_id"]
    resp = _run(predict_role_success.handler({"emp_id": emp_id, "position_id": pos_id}))
    assert "P(успех" in _text(resp)


# --- jira_tools ---

def test_query_jira(runtime_data):
    db = Database(runtime_data["db"])
    rows = list(db.query("SELECT emp_id FROM jira_issues LIMIT 1"))
    if not rows:
        return  # data variant: no IT employees
    resp = _run(query_jira.handler({"emp_id": rows[0]["emp_id"]}))
    assert _text(resp)


# --- memory_tools ---

def test_update_scratchpad(runtime_data):
    resp = _run(update_scratchpad.handler({"entry": "Проверка записи."}))
    assert "scratchpad" in _text(resp)
    from pulse.config import PATHS
    assert (PATHS.memory / "scratchpad.md").read_text(encoding="utf-8")


def test_update_identity(runtime_data):
    resp = _run(update_identity.handler({"entry": "Я — Пульс."}))
    assert "identity" in _text(resp)


# --- knowledge_tools ---

def test_knowledge_roundtrip(runtime_data):
    _run(knowledge_write.handler({"topic": "test-topic", "content": "# Hello"}))
    resp = _run(knowledge_read.handler({"topic": "test-topic"}))
    assert "# Hello" in _text(resp)
    listed = _run(knowledge_list.handler({}))
    assert "test-topic.md" in _text(listed)


def test_knowledge_unsafe_topic(runtime_data):
    resp = _run(knowledge_write.handler({"topic": "../etc", "content": "x"}))
    assert resp.get("is_error")


# --- feedback_tools ---

def test_get_recent_feedback_empty(runtime_data):
    resp = _run(get_recent_feedback.handler({"n": 5}))
    text = _text(resp)
    # may or may not exist on first run; both cases acceptable.
    assert text


# --- self_tools ---

def test_repo_list(runtime_data):
    resp = _run(repo_list.handler({"glob": "*.md"}))
    assert _text(resp)


def test_repo_read_traversal_blocked(runtime_data):
    resp = _run(repo_read.handler({"path": "../../etc/passwd"}))
    assert resp.get("is_error")


# --- registry ---

def test_chat_tools_registered():
    names = {t.name for t in CHAT_TOOLS}
    assert "get_employee_profile" in names
    assert "predict_attrition" in names
    # feedback / self tools NOT in chat surface
    assert "get_recent_feedback" not in names
    assert "repo_read" not in names


def test_evolution_tools_registered():
    names = {t.name for t in EVOLUTION_TOOLS}
    assert "get_recent_feedback" in names
    assert "repo_read" in names


def test_allowed_tool_format():
    for s in chat_allowed_tools():
        assert s.startswith("mcp__pulse-tools__")
    for s in evolution_allowed_tools():
        assert s.startswith("mcp__pulse-tools__")
