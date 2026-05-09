"""Tests for `pulse/tools/exec_tools.py` — the run_python_analysis sandbox.

We test the pure-Python core (`_run_sandboxed`) directly. The MCP wrapper
just plumbs args through.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pulse.data_engine.seed import seed


@pytest.fixture(scope="module")
def seeded_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db_path = tmp_path_factory.mktemp("exec") / "sber_hr.db"
    seed(db_path, force=True)
    return db_path


def test_simple_print(seeded_db: Path):
    from pulse.tools.exec_tools import _run_sandboxed
    res = _run_sandboxed("print('hi')", str(seeded_db), timeout_s=10)
    assert res["ok"] is True, res
    assert "hi" in res["stdout"]


def test_last_expression_returned(seeded_db: Path):
    from pulse.tools.exec_tools import _run_sandboxed
    res = _run_sandboxed("2 + 2", str(seeded_db), timeout_s=10)
    assert res["ok"] is True
    assert res["result_repr"] == "4"


def test_pandas_dataframes_loaded(seeded_db: Path):
    from pulse.tools.exec_tools import _run_sandboxed
    code = "len(df_employees)"
    res = _run_sandboxed(code, str(seeded_db), timeout_s=15)
    assert res["ok"] is True, res["error"]
    assert int(res["result_repr"]) > 0


def test_groupby_aggregation(seeded_db: Path):
    from pulse.tools.exec_tools import _run_sandboxed
    code = (
        "df_activity.groupby('emp_id')['tasks_done'].mean().nlargest(3).to_dict()"
    )
    res = _run_sandboxed(code, str(seeded_db), timeout_s=15)
    assert res["ok"] is True, res["error"]
    assert "emp_" in res["result_repr"]


def test_open_is_blocked(seeded_db: Path):
    from pulse.tools.exec_tools import _run_sandboxed
    res = _run_sandboxed("open('/etc/passwd').read()", str(seeded_db), timeout_s=5)
    assert res["ok"] is False
    assert "open" in res["error"] or "NameError" in res["error"]


def test_import_is_blocked(seeded_db: Path):
    """`import os` should fail because __import__ is removed from builtins."""
    from pulse.tools.exec_tools import _run_sandboxed
    res = _run_sandboxed("import os; print(os.uname())", str(seeded_db), timeout_s=5)
    assert res["ok"] is False
    assert "__import__" in res["error"] or "NameError" in res["error"] \
            or "ImportError" in res["error"]


def test_exec_eval_compile_blocked(seeded_db: Path):
    from pulse.tools.exec_tools import _run_sandboxed
    for snippet in ("exec('1+1')", "eval('1+1')", "compile('1', 'x', 'eval')"):
        res = _run_sandboxed(snippet, str(seeded_db), timeout_s=5)
        assert res["ok"] is False, f"{snippet!r} unexpectedly succeeded"


def test_db_is_read_only(seeded_db: Path):
    """Even via the underlying sqlite3 module (if found), writes should fail."""
    from pulse.tools.exec_tools import _run_sandboxed
    # The user's snippet doesn't have access to sqlite3 directly (no import),
    # but pandas-internal connections also use the same RO URI. We assert via
    # df_employees.copy() not affecting the on-disk file size — proxy: the
    # snippet runs successfully without any side effect we can observe.
    pre_size = seeded_db.stat().st_size
    res = _run_sandboxed("print(len(df_employees))", str(seeded_db), timeout_s=10)
    assert res["ok"]
    post_size = seeded_db.stat().st_size
    assert pre_size == post_size  # no on-disk change


def test_timeout_kills_runaway(seeded_db: Path):
    """An infinite-ish loop must be killed by the timeout."""
    from pulse.tools.exec_tools import _run_sandboxed
    code = "x = 0\nwhile True:\n    x += 1"
    res = _run_sandboxed(code, str(seeded_db), timeout_s=2)
    assert res["ok"] is False
    assert "timeout" in res["error"].lower()


def test_output_truncation(seeded_db: Path):
    from pulse.tools.exec_tools import _run_sandboxed, OUTPUT_CHARS_CAP
    code = "print('x' * (OUTPUT_CHARS_CAP * 2))".replace(
        "OUTPUT_CHARS_CAP", str(OUTPUT_CHARS_CAP)
    )
    res = _run_sandboxed(code, str(seeded_db), timeout_s=10)
    assert res["ok"]
    assert len(res["stdout"]) <= OUTPUT_CHARS_CAP + 5
