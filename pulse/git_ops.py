"""Thin GitPython wrapper for the evolution loop.

Operations we need:
  * commit_all_with_msg(msg) — add everything and commit
  * create_annotated_tag(tag_name, msg)
  * rollback_workdir() — `git checkout -- .` + `git clean -fd`
  * current_branch()
  * staged_diff_text() / unstaged_diff_text()
  * is_protected_path(path) — checked against PROTECTED_PATHS plus pulse/*.py

We deliberately do NOT push; remote is out of scope for v0.1.
"""
from __future__ import annotations

import fnmatch
import logging
from pathlib import Path

from git import GitCommandError, Repo

from .config import PATHS, PROTECTED_PATHS

log = logging.getLogger(__name__)


def repo() -> Repo:
    return Repo(str(PATHS.repo))


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------

def current_branch() -> str:
    try:
        return repo().active_branch.name
    except TypeError:
        return "DETACHED"


def status_short() -> str:
    return repo().git.status("--short")


def changed_paths(*, include_untracked: bool = True) -> list[str]:
    r = repo()
    paths: set[str] = set()
    paths.update([item.a_path for item in r.index.diff(None)])  # unstaged
    paths.update([item.a_path for item in r.index.diff("HEAD")])  # staged vs HEAD
    if include_untracked:
        paths.update(r.untracked_files)
    return sorted(p for p in paths if p)


def diff_text(*, staged: bool = False) -> str:
    r = repo()
    if staged:
        return r.git.diff("--cached")
    return r.git.diff()


def diff_with_head(*, max_chars: int = 40_000) -> str:
    r = repo()
    txt = r.git.diff("HEAD")
    if len(txt) > max_chars:
        txt = txt[:max_chars] + "\n\n[diff truncated]"
    return txt


def is_protected_path(path: str) -> bool:
    if path in PROTECTED_PATHS:
        return True
    # In v0.1 every Python file in pulse/ is protected.
    if fnmatch.fnmatch(path, "pulse/*.py") or fnmatch.fnmatch(path, "pulse/**/*.py"):
        return True
    # Also data_engine/ subpaths
    if fnmatch.fnmatch(path, "pulse/data_engine/*.py"):
        return True
    return False


def protected_paths_in_changes() -> list[str]:
    return [p for p in changed_paths() if is_protected_path(p)]


# ---------------------------------------------------------------------------
# Mutating ops
# ---------------------------------------------------------------------------

def commit_all_with_msg(message: str) -> str:
    r = repo()
    r.git.add("-A")
    r.index.commit(message)
    return r.head.commit.hexsha


def create_annotated_tag(tag_name: str, msg: str) -> None:
    r = repo()
    r.create_tag(tag_name, message=msg)


def rollback_workdir() -> None:
    """Revert worktree changes. Used after a failed evolution self-test."""
    r = repo()
    try:
        r.git.checkout("--", ".")
    except GitCommandError as ex:
        log.warning("checkout failed: %s", ex)
    try:
        r.git.clean("-fd")
    except GitCommandError as ex:
        log.warning("clean failed: %s", ex)


__all__ = [
    "repo",
    "current_branch",
    "status_short",
    "changed_paths",
    "diff_text",
    "diff_with_head",
    "is_protected_path",
    "protected_paths_in_changes",
    "commit_all_with_msg",
    "create_annotated_tag",
    "rollback_workdir",
]
