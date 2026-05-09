"""Tool registry. Build the in-process MCP server for Pulse.

Two surfaces:
* `chat_tools()` — read-only HR data + ML + memory/knowledge updates. No
  feedback log, no self-introspection. This is what the chat-loop sees.
* `evolution_tools()` — adds feedback log reader and self-introspection
  helpers. Used by the evolution session (in addition to built-in
  Read/Edit/Write/Glob/Grep that ship with the SDK).
"""
from __future__ import annotations

from claude_agent_sdk import create_sdk_mcp_server

from .data_tools import (
    get_collab_neighbors,
    get_employee_metrics,
    get_employee_profile,
    list_employees,
)
from .feedback_tools import get_recent_feedback
from .jira_tools import query_confluence, query_jira
from .knowledge_tools import knowledge_list, knowledge_read, knowledge_write
from .memory_tools import update_identity, update_scratchpad
from .ml_tools import predict_attrition, predict_role_success, recommend_courses
from .self_tools import repo_list, repo_read

CHAT_TOOLS = [
    get_employee_profile,
    get_employee_metrics,
    list_employees,
    get_collab_neighbors,
    query_jira,
    query_confluence,
    predict_attrition,
    recommend_courses,
    predict_role_success,
    update_scratchpad,
    update_identity,
    knowledge_read,
    knowledge_write,
    knowledge_list,
]

EVOLUTION_TOOLS = CHAT_TOOLS + [
    get_recent_feedback,
    repo_read,
    repo_list,
]


def build_chat_server():
    return create_sdk_mcp_server(name="pulse-tools", version="0.1.0", tools=CHAT_TOOLS)


def build_evolution_server():
    return create_sdk_mcp_server(name="pulse-tools", version="0.1.0", tools=EVOLUTION_TOOLS)


# `mcp__<server>__<tool>` is the wire format. We expose helpers so callers
# don't have to remember the prefix.
def chat_allowed_tools() -> list[str]:
    return [f"mcp__pulse-tools__{t.name}" for t in CHAT_TOOLS]


def evolution_allowed_tools() -> list[str]:
    return [f"mcp__pulse-tools__{t.name}" for t in EVOLUTION_TOOLS]


__all__ = [
    "CHAT_TOOLS",
    "EVOLUTION_TOOLS",
    "build_chat_server",
    "build_evolution_server",
    "chat_allowed_tools",
    "evolution_allowed_tools",
]
