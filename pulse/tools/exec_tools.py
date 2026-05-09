"""`run_python_analysis` — sandboxed Python execution for one-shot analysis.

For questions where no pre-built mart fits and writing a permanent tool
would be overkill, the agent can submit a short Python snippet that runs
against pre-loaded pandas DataFrames over the synthetic DB.

Isolation:

  * **Subprocess** via `multiprocessing.Process` — code runs in a child
    process. Hard kill on timeout (`Process.terminate()` then `.kill()`).
  * **Read-only DB** — opened with `sqlite3.connect("file:...?mode=ro",
    uri=True)` so even an `UPDATE`/`DELETE` from the snippet bounces.
  * **Restricted builtins** — no `open`, `__import__`, `exec`, `compile`,
    `eval`, `input`, `breakpoint`. Pandas/numpy are pre-imported and
    available; nothing else.
  * **Captured stdout** — anything `print(...)`'d is returned to the
    caller. Last expression is also returned (Jupyter-style).
  * **Output cap** — stdout is truncated at OUTPUT_CHARS_CAP so a runaway
    `print(df_employees)` cannot exhaust memory.

Pre-loaded names available to the snippet:

  df_employees   — employees ⨯ positions ⨯ units flat view (active+terminated)
  df_activity    — activity_daily (last 90 days)
  df_digital     — digital_patterns_daily (last 90 days)
  df_wearables   — wearables_daily (last 90 days)
  df_collab      — collab_edges
  df_peer        — peer_feedback (last 90 days)
  pd, np         — pandas, numpy

Convention: the snippet should `print(...)` its findings or leave a
result as the last expression. Examples:

  >>> df_activity.groupby("emp_id")["tasks_done"].mean().nlargest(5)
  >>> print(df_employees[df_employees.archetype == "burnout_risk"][["emp_id", "full_name"]])
"""
from __future__ import annotations

import asyncio
import io
import multiprocessing
import textwrap
import traceback
from contextlib import redirect_stdout
from typing import Any

from claude_agent_sdk import tool

from ..config import PATHS

DEFAULT_TIMEOUT_S = 15
MAX_TIMEOUT_S = 60
OUTPUT_CHARS_CAP = 8000


SAFE_BUILTINS_DENYLIST = frozenset({
    "open", "__import__", "exec", "compile", "eval", "input",
    "breakpoint", "exit", "quit", "help",
})


def _build_safe_builtins() -> dict[str, Any]:
    import builtins as _b
    safe = {k: v for k, v in vars(_b).items()
             if not k.startswith("_") and k not in SAFE_BUILTINS_DENYLIST}
    # Re-allow dunder names that are needed for normal evaluation.
    safe["__name__"] = "__sandbox__"
    safe["__doc__"] = None
    return safe


def _load_dataframes(db_path: str) -> dict[str, Any]:
    """Open a *read-only* SQLite connection and load common dataframes."""
    import sqlite3
    import pandas as pd

    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        df_employees = pd.read_sql_query(
            """
            SELECT e.*, p.title AS position_title, p.type AS position_type,
                   u.name AS unit_name
            FROM employees e
            LEFT JOIN positions p ON p.position_id = e.position_id
            LEFT JOIN units u ON u.unit_id = e.unit_id
            """,
            conn,
        )
        # Last 90 days windows so the sandbox doesn't have to load 24 months.
        df_activity = pd.read_sql_query(
            "SELECT * FROM activity_daily WHERE date >= "
            "(SELECT date(MAX(date), '-90 day') FROM activity_daily)",
            conn,
        )
        df_digital = pd.read_sql_query(
            "SELECT * FROM digital_patterns_daily WHERE date >= "
            "(SELECT date(MAX(date), '-90 day') FROM digital_patterns_daily)",
            conn,
        )
        df_wearables = pd.read_sql_query(
            "SELECT * FROM wearables_daily WHERE date >= "
            "(SELECT date(MAX(date), '-90 day') FROM wearables_daily)",
            conn,
        )
        df_collab = pd.read_sql_query("SELECT * FROM collab_edges", conn)
        df_peer = pd.read_sql_query(
            "SELECT * FROM peer_feedback WHERE ts >= "
            "(SELECT date(MAX(ts), '-90 day') FROM peer_feedback)",
            conn,
        )
    finally:
        conn.close()

    import numpy as np
    return {
        "df_employees": df_employees,
        "df_activity": df_activity,
        "df_digital": df_digital,
        "df_wearables": df_wearables,
        "df_collab": df_collab,
        "df_peer": df_peer,
        "pd": pd,
        "np": np,
    }


def _child_run(code: str, db_path: str, conn) -> None:
    """Worker for the multiprocessing.Process. Sends one dict via `conn`."""
    try:
        ns = _load_dataframes(db_path)
        ns["__builtins__"] = _build_safe_builtins()
        buf = io.StringIO()
        last_expr = None

        # Try to evaluate the last statement as an expression (Jupyter style).
        try:
            import ast
            tree = ast.parse(code, mode="exec")
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                head_src = ast.unparse(ast.Module(body=tree.body[:-1], type_ignores=[]))
                tail_src = ast.unparse(tree.body[-1])
                with redirect_stdout(buf):
                    if head_src.strip():
                        exec(compile(head_src, "<sandbox>", "exec"), ns, ns)  # noqa: S102
                    last_expr = eval(compile(tail_src, "<sandbox>", "eval"), ns, ns)  # noqa: S307
            else:
                with redirect_stdout(buf):
                    exec(compile(code, "<sandbox>", "exec"), ns, ns)  # noqa: S102
        except SyntaxError as ex:
            conn.send({
                "ok": False,
                "stdout": "",
                "result_repr": "",
                "error": f"SyntaxError: {ex}",
            })
            return

        stdout = buf.getvalue()
        if len(stdout) > OUTPUT_CHARS_CAP:
            stdout = stdout[: OUTPUT_CHARS_CAP - 1] + "…"
        result_repr = ""
        if last_expr is not None:
            try:
                result_repr = repr(last_expr)
            except Exception as ex:  # noqa: BLE001
                result_repr = f"<unrepr-able: {type(ex).__name__}>"
            if len(result_repr) > OUTPUT_CHARS_CAP:
                result_repr = result_repr[: OUTPUT_CHARS_CAP - 1] + "…"
        conn.send({
            "ok": True,
            "stdout": stdout,
            "result_repr": result_repr,
            "error": "",
        })
    except Exception as ex:  # noqa: BLE001
        tb = traceback.format_exception_only(type(ex), ex)
        conn.send({
            "ok": False,
            "stdout": "",
            "result_repr": "",
            "error": "".join(tb).strip(),
        })


def _run_sandboxed(code: str, db_path: str, timeout_s: int) -> dict[str, Any]:
    """Run `code` in a child process with hard timeout. Synchronous."""
    parent_conn, child_conn = multiprocessing.Pipe(duplex=False)
    proc = multiprocessing.Process(
        target=_child_run, args=(code, db_path, child_conn), daemon=True
    )
    proc.start()
    proc.join(timeout=timeout_s)
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=2)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=2)
        return {"ok": False, "stdout": "", "result_repr": "",
                "error": f"timeout after {timeout_s}s — kill"}
    if not parent_conn.poll(timeout=0.5):
        return {"ok": False, "stdout": "", "result_repr": "",
                "error": f"child exited (code={proc.exitcode}) without sending a result"}
    return parent_conn.recv()


def _format_result(result: dict[str, Any]) -> str:
    parts: list[str] = []
    if not result["ok"]:
        parts.append(f"❌ Ошибка: {result['error']}")
    else:
        parts.append("✅ Ok.")
    if result["stdout"]:
        parts.append("\n— stdout —\n" + result["stdout"].rstrip())
    if result.get("result_repr"):
        parts.append("\n— значение последнего выражения —\n" + result["result_repr"])
    if not result["stdout"] and not result.get("result_repr") and result["ok"]:
        parts.append("\n(скрипт ничего не вывел и не оставил результата выражением)")
    return "\n".join(parts)


# ---------------------------------------------------------------------------

@tool(
    "run_python_analysis",
    "Выполнить короткий Python-скрипт в песочнице с уже загруженными pandas "
    "DataFrame'ами над синтетической БД. Доступны: df_employees, df_activity, "
    "df_digital, df_wearables, df_collab, df_peer (последние 90 дней), pd, np. "
    "БД read-only. Без open/import/exec — только pandas/numpy. Используй для "
    "разовых аналитических вопросов, для которых нет витрины. Если запрос "
    "повторяется — вместо exec'а делай витрину через эволюцию. Параметры: "
    "code (str, обязателен), timeout_s (int, default 15, max 60).",
    {"code": str, "timeout_s": int},
)
async def run_python_analysis(args: dict[str, Any]) -> dict[str, Any]:
    code = (args.get("code") or "").strip()
    if not code:
        return {"content": [{"type": "text",
                              "text": "code обязателен."}], "is_error": True}
    timeout_s = max(1, min(MAX_TIMEOUT_S, int(args.get("timeout_s") or DEFAULT_TIMEOUT_S)))
    if not PATHS.db.exists():
        return {"content": [{"type": "text",
                              "text": "БД отсутствует — seed первым."}], "is_error": True}
    # Run the blocking sandbox in a thread so we don't block the event loop.
    result = await asyncio.to_thread(_run_sandboxed, code, str(PATHS.db), timeout_s)
    return {
        "content": [{"type": "text", "text": _format_result(result)}],
        "is_error": not result["ok"],
    }


__all__ = ["run_python_analysis"]
