"""JIRA / Confluence tools (synthetic)."""
from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool
from sqlite_utils import Database

from ..config import PATHS


def _db() -> Database:
    return Database(PATHS.db)


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


@tool(
    "query_jira",
    "Поиск JIRA-задач сотрудника. Параметры: emp_id (обязательно), since (YYYY-MM-DD, опц.), "
    "until (YYYY-MM-DD, опц.), status (in_progress / resolved / closed, опц.). Лимит — 50.",
    {"emp_id": str, "since": str, "until": str, "status": str},
)
async def query_jira(args: dict[str, Any]) -> dict[str, Any]:
    emp_id = args.get("emp_id", "").strip()
    since = args.get("since") or ""
    until = args.get("until") or ""
    status = args.get("status") or ""
    if not emp_id:
        return {"content": [{"type": "text", "text": "emp_id обязателен."}], "is_error": True}

    where = ["emp_id = :e"]
    params: dict[str, Any] = {"e": emp_id}
    if since:
        where.append("ts_created >= :s")
        params["s"] = since
    if until:
        where.append("ts_created <= :u")
        params["u"] = until + "T23:59:59"
    if status:
        where.append("status = :st")
        params["st"] = status

    sql = ("SELECT issue_key, status, type, priority, ts_created, ts_resolved, summary "
           "FROM jira_issues WHERE " + " AND ".join(where) + " ORDER BY ts_created DESC LIMIT 50")
    rows = list(_db().query(sql, params))
    if not rows:
        return _ok(f"JIRA-задач не найдено для {emp_id} с указанными фильтрами.")

    # aggregates
    by_status: dict[str, int] = {}
    by_prio: dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        by_prio[r["priority"]] = by_prio.get(r["priority"], 0) + 1
    summary = (
        f"Найдено {len(rows)} JIRA-задач для {emp_id}. "
        f"По статусу: {dict(by_status)}; по приоритету: {dict(by_prio)}."
    )
    body = "\n".join(
        f"  {r['issue_key']} [{r['status']} / {r['priority']} / {r['type']}] "
        f"{r['ts_created'][:10]}: {r['summary']}"
        for r in rows[:15]
    )
    return _ok(f"{summary}\nПоследние 15:\n{body}")


@tool(
    "query_confluence",
    "Поиск страниц Confluence сотрудника. Параметры: emp_id (обязательно), since (YYYY-MM-DD, опц.). Лимит — 30.",
    {"emp_id": str, "since": str},
)
async def query_confluence(args: dict[str, Any]) -> dict[str, Any]:
    emp_id = args.get("emp_id", "").strip()
    since = args.get("since") or ""
    if not emp_id:
        return {"content": [{"type": "text", "text": "emp_id обязателен."}], "is_error": True}

    where = ["emp_id = :e"]
    params: dict[str, Any] = {"e": emp_id}
    if since:
        where.append("ts_created >= :s")
        params["s"] = since
    sql = ("SELECT page_id, ts_created, length_chars, title FROM confluence_pages WHERE "
           + " AND ".join(where) + " ORDER BY ts_created DESC LIMIT 30")
    rows = list(_db().query(sql, params))
    if not rows:
        return _ok(f"Страниц Confluence не найдено для {emp_id}.")
    body = "\n".join(f"  {r['page_id']} ({r['ts_created'][:10]}, {r['length_chars']}ch): {r['title']}"
                      for r in rows[:15])
    return _ok(f"Найдено {len(rows)} страниц Confluence для {emp_id}.\nПоследние 15:\n{body}")


__all__ = ["query_jira", "query_confluence"]
