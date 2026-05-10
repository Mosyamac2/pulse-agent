"""Thin GitPython wrapper for the evolution loop.

Operations we need:
  * commit_all_with_msg(msg) — add everything and commit
  * create_annotated_tag(tag_name, msg) — author/committer ident is forced
    to the project convention via custom_environment so a systemd-launched
    process without global git config can still produce a tag (this used
    to crash with "empty ident name"; see backlog #6).
  * rollback_workdir() — `git checkout -- .` + `git clean -fd`
  * current_branch()
  * staged_diff_text() / unstaged_diff_text()
  * is_protected_path(path) — checked against PROTECTED_PATHS only;
    the broad `pulse/*.py` block was lifted in v1.0.0.

We deliberately do NOT push; remote is out of scope for v0.1.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from git import Actor, GitCommandError, Repo

from .config import PATHS, PROTECTED_PATHS

# Author/committer used by Pulse-driven commits and tags. Matches the
# convention visible in `git log` for prior releases. We force this rather
# than relying on global git config because the systemd unit runs as a
# user without ~/.gitconfig.
PULSE_GIT_NAME = "Pulse Builder"
PULSE_GIT_EMAIL = "pulse@local"

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
    """Return True if the given repo-relative path is in the immune core.

    v1.0.0 narrowed this to an explicit allowlist (`PROTECTED_PATHS`):
    the constitution, the safety prompt, and the DB schema. Other Python
    files in `pulse/` are now editable by the evolution loop, gated only
    by self-test and commit-review.
    """
    return path in PROTECTED_PATHS


def protected_paths_in_changes() -> list[str]:
    return [p for p in changed_paths() if is_protected_path(p)]


# ---------------------------------------------------------------------------
# Mutating ops
# ---------------------------------------------------------------------------

def commit_all_with_msg(message: str) -> str:
    r = repo()
    r.git.add("-A")
    actor = Actor(PULSE_GIT_NAME, PULSE_GIT_EMAIL)
    r.index.commit(message, author=actor, committer=actor)
    return r.head.commit.hexsha


def create_annotated_tag(tag_name: str, msg: str) -> None:
    """Create an annotated tag with Pulse Builder ident forced via env.

    `git tag -m` reads ident from config or environment; on a systemd
    user without ~/.gitconfig it bails with "empty ident name". We wrap
    the call in `custom_environment` so the four GIT_*_NAME / GIT_*_EMAIL
    vars are present for this invocation only (no config mutation).
    """
    r = repo()
    env = {
        "GIT_AUTHOR_NAME": PULSE_GIT_NAME,
        "GIT_AUTHOR_EMAIL": PULSE_GIT_EMAIL,
        "GIT_COMMITTER_NAME": PULSE_GIT_NAME,
        "GIT_COMMITTER_EMAIL": PULSE_GIT_EMAIL,
    }
    with r.git.custom_environment(**env):
        r.create_tag(tag_name, message=msg)


def push_to_origin_with_tags(branch: str = "master") -> dict[str, Any]:
    """Push branch + tags to origin using `PULSE_GITHUB_PAT` from env.

    Used by the evolution loop (since v1.5.0) so a self-evolved commit
    propagates to GitHub without manual intervention. Behaviour:

      * If `PULSE_GITHUB_PAT` is unset → return {pushed: False,
        reason: 'no_pat'}. Cycle continues — push is best-effort.
      * If origin is not an https URL → returns no_https reason.
      * On success → {pushed: True}.
      * On any GitCommandError → {pushed: False, reason: <stderr with
        the PAT redacted>}.

    The PAT is injected into the URL only for this single git invocation
    via `git -c http.extraheader=...` — ideally we would use that, but
    the simpler `https://x-access-token:PAT@github.com/...` form
    matches what the user uses interactively and works through the
    xray HTTP proxy without extra config.
    """
    import os
    pat = os.environ.get("PULSE_GITHUB_PAT", "").strip()
    if not pat:
        return {"pushed": False, "reason": "no_pat"}

    r = repo()
    try:
        remote_url = r.remotes.origin.url
    except Exception as ex:  # noqa: BLE001
        return {"pushed": False, "reason": f"no_origin: {ex}"}

    if not remote_url.startswith("https://"):
        return {"pushed": False, "reason": f"unsupported_remote: {remote_url}"}

    # Strip any embedded creds, then inject our PAT for this push only.
    rest = remote_url[len("https://"):]
    if "@" in rest:
        rest = rest.split("@", 1)[1]
    push_url = f"https://x-access-token:{pat}@{rest}"

    env = {
        "GIT_AUTHOR_NAME": PULSE_GIT_NAME,
        "GIT_AUTHOR_EMAIL": PULSE_GIT_EMAIL,
        "GIT_COMMITTER_NAME": PULSE_GIT_NAME,
        "GIT_COMMITTER_EMAIL": PULSE_GIT_EMAIL,
    }
    try:
        with r.git.custom_environment(**env):
            r.git.push(push_url, branch, "--follow-tags")
    except GitCommandError as ex:
        clean = str(ex).replace(pat, "***REDACTED***")
        log.warning("push_to_origin failed: %s", clean[:300])
        return {"pushed": False, "reason": clean[:300]}

    log.info("push_to_origin: %s pushed with tags", branch)
    return {"pushed": True}


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
    "push_to_origin_with_tags",
    "rollback_workdir",
]
