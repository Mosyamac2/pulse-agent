"""Atomic version bump across VERSION + pyproject.toml + README.md badge +
docs/ARCHITECTURE.md header (P9 — release artifacts kept in sync).

Semver-ish: `MAJOR.MINOR.PATCH[-rc.N]`. RC bumps reset to `-rc.0` on the
following minor/major.

Public API:
  - `parse(s) -> Version`
  - `bump(level: 'rc' | 'patch' | 'minor' | 'major', changelog_line: str|None = None) -> Version`
  - `current() -> Version`
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import PATHS

VERSION_RX = re.compile(r"^(?P<maj>\d+)\.(?P<min>\d+)\.(?P<pat>\d+)(?:-rc\.(?P<rc>\d+))?$")


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    rc: int | None = None

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return f"{base}-rc.{self.rc}" if self.rc is not None else base

    @property
    def badge(self) -> str:
        # shields.io rule: dashes inside the value must be doubled.
        return str(self).replace("-", "--")


def parse(s: str) -> Version:
    s = s.strip()
    m = VERSION_RX.match(s)
    if not m:
        raise ValueError(f"unparseable version: {s!r}")
    rc = int(m.group("rc")) if m.group("rc") is not None else None
    return Version(int(m.group("maj")), int(m.group("min")), int(m.group("pat")), rc)


def current() -> Version:
    return parse(PATHS.version_file.read_text(encoding="utf-8"))


def _next(level: str, v: Version) -> Version:
    if level == "rc":
        if v.rc is None:
            raise ValueError("can only bump rc on a version that already has -rc.N")
        return Version(v.major, v.minor, v.patch, v.rc + 1)
    if level == "patch":
        return Version(v.major, v.minor, v.patch + 1, None)
    if level == "minor":
        return Version(v.major, v.minor + 1, 0, None)
    if level == "major":
        return Version(v.major + 1, 0, 0, None)
    raise ValueError(f"unknown level: {level}")


# ---------------------------------------------------------------------------
# Sync writers
# ---------------------------------------------------------------------------

_PYPROJECT_RX = re.compile(r'^(version\s*=\s*)"[^"]+"$', re.MULTILINE)
_README_BADGE_RX = re.compile(r"version-[^)\s\]]+-blue")


def _sync_pyproject(new_version: str) -> None:
    p = PATHS.repo / "pyproject.toml"
    if not p.exists():
        return
    txt = p.read_text(encoding="utf-8")
    new_txt, n = _PYPROJECT_RX.subn(rf'\1"{new_version}"', txt, count=1)
    if n != 1:
        raise RuntimeError("pyproject.toml: failed to find a single `version = \"...\"` line")
    p.write_text(new_txt, encoding="utf-8")


def _sync_readme(new_version: Version, changelog_line: str | None) -> None:
    p = PATHS.repo / "README.md"
    if not p.exists():
        return
    txt = p.read_text(encoding="utf-8")
    new_txt, n = _README_BADGE_RX.subn(f"version-{new_version.badge}-blue", txt, count=1)
    if n != 1:
        raise RuntimeError("README.md: shields.io badge not found")
    # prepend changelog line under '## Changelog'
    if changelog_line:
        marker = "## Changelog\n"
        idx = new_txt.find(marker)
        if idx != -1:
            insert_at = idx + len(marker)
            line = f"\n- `v{new_version}` — {changelog_line}\n"
            new_txt = new_txt[:insert_at] + line + new_txt[insert_at:]
    p.write_text(new_txt, encoding="utf-8")


def _sync_arch(new_version: Version) -> None:
    p = PATHS.architecture_doc
    if not p.exists():
        return
    txt = p.read_text(encoding="utf-8")
    lines = txt.splitlines()
    if not lines:
        return
    lines[0] = re.sub(r"v\d+\.\d+\.\d+(-rc\.\d+)?", f"v{new_version}", lines[0])
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def bump(level: str, changelog_line: str | None = None) -> Version:
    cur = current()
    nxt = _next(level, cur)
    PATHS.version_file.write_text(str(nxt) + "\n", encoding="utf-8")
    _sync_pyproject(str(nxt))
    _sync_readme(nxt, changelog_line)
    _sync_arch(nxt)
    return nxt


def write_explicit(version: Version, changelog_line: str | None = None) -> Version:
    PATHS.version_file.write_text(str(version) + "\n", encoding="utf-8")
    _sync_pyproject(str(version))
    _sync_readme(version, changelog_line)
    _sync_arch(version)
    return version


def assert_in_sync() -> None:
    v = str(current())
    pp = (PATHS.repo / "pyproject.toml").read_text(encoding="utf-8")
    if f'version = "{v}"' not in pp:
        raise AssertionError(f"pyproject.toml drifted from VERSION ({v})")
    rd = (PATHS.repo / "README.md").read_text(encoding="utf-8")
    badge = current().badge
    if f"version-{badge}-blue" not in rd:
        raise AssertionError(f"README.md badge drifted from VERSION ({v})")
    arch = PATHS.architecture_doc.read_text(encoding="utf-8")
    if f"v{v}" not in arch.splitlines()[0]:
        raise AssertionError(f"docs/ARCHITECTURE.md header drifted from VERSION ({v})")


__all__ = ["Version", "parse", "current", "bump", "write_explicit", "assert_in_sync"]
