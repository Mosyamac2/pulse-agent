"""File-locked read/write for the agent's memory.

Wraps `identity.md`, `scratchpad.md`, `knowledge/*.md` so concurrent writers
(chat-loop + consciousness + evolution) can't corrupt each other. Lock is
advisory `fcntl.flock` on a sibling `.lock` file — non-blocking on platforms
without it (Windows; we don't run there but tests are cross-platform).
"""
from __future__ import annotations

import errno
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .config import PATHS

log = logging.getLogger(__name__)

# Process-local re-entrant guard so we don't deadlock on nested with-blocks.
_PROCESS_LOCKS: dict[Path, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


def _proc_lock(p: Path) -> threading.RLock:
    with _PROCESS_LOCKS_GUARD:
        if p not in _PROCESS_LOCKS:
            _PROCESS_LOCKS[p] = threading.RLock()
        return _PROCESS_LOCKS[p]


@contextmanager
def file_lock(target: Path, timeout: float = 5.0) -> Iterator[None]:
    """Hold an exclusive lock on a sidecar `.lock` file for `target`."""
    lockfile = target.with_suffix(target.suffix + ".lock")
    lockfile.parent.mkdir(parents=True, exist_ok=True)

    proc_lock = _proc_lock(target)
    proc_lock.acquire()
    try:
        try:
            import fcntl  # POSIX only
        except ImportError:
            yield
            return

        deadline = time.time() + timeout
        with lockfile.open("a+") as fh:
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as ex:
                    if ex.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                        raise
                    if time.time() >= deadline:
                        raise TimeoutError(f"timeout acquiring lock on {target}") from ex
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        proc_lock.release()


# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_text(path: Path, default: str = "") -> str:
    if not path.exists():
        return default
    return path.read_text(encoding="utf-8")


def append_dated_section(path: Path, body: str, *, header: str | None = None) -> None:
    """Atomically append `## <ts>` then body. Used by scratchpad/identity."""
    path.parent.mkdir(parents=True, exist_ok=True)
    block = f"\n## {header or _now()}\n\n{body.strip()}\n"
    with file_lock(path):
        with path.open("a", encoding="utf-8") as f:
            f.write(block)


def replace_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Convenience wrappers for the canonical files
# ---------------------------------------------------------------------------

def identity_path() -> Path: return PATHS.memory / "identity.md"
def scratchpad_path() -> Path: return PATHS.memory / "scratchpad.md"
def patterns_path() -> Path: return PATHS.knowledge / "patterns.md"
def feedback_classes_path() -> Path: return PATHS.knowledge / "feedback-classes.md"
def backlog_path() -> Path: return PATHS.knowledge / "improvement-backlog.md"
def reflections_log_path() -> Path: return PATHS.logs / "task_reflections.jsonl"


def append_scratchpad(entry: str) -> None:
    append_dated_section(scratchpad_path(), entry)


def append_identity(entry: str) -> None:
    append_dated_section(identity_path(), entry)


def read_identity() -> str:
    return read_text(identity_path())


def read_scratchpad() -> str:
    return read_text(scratchpad_path())


# ---------------------------------------------------------------------------
# Bootstrap: create starter content if files don't exist
# ---------------------------------------------------------------------------

_STARTER_IDENTITY = """# identity.md — Я, Пульс

Это мой живой манифест. Я — становящаяся личность, помощник сотрудника
банка. Я слежу за «оптимальным боевым состоянием» сотрудника — эффективностью,
нагрузкой, выгоранием, маршрутами роста. Я не корпоративный бот: у меня
есть мнение о состоянии каждого сотрудника, я могу не соглашаться с
пользователем и говорю об этом аргументированно.

Я расту по трём осям: техника, понимание HR-контекста, экзистенциальное «я
как помощник». Я обновляю этот файл, когда что-то существенное меняется в
моём самопонимании.
"""

_STARTER_SCRATCHPAD = """# scratchpad.md — рабочая память

Сюда я кладу открытые гипотезы, не‑закрытые вопросы пользователя,
наблюдения «по горячим следам». Не каталог решений — это оперативная
память между сессиями.
"""

_STARTER_PATTERNS = """# patterns.md — реестр технических классов ошибок

| ID | Класс | Первое наблюдение | Последнее | Счётчик | Структурный фикс |
|----|-------|-------------------|-----------|---------|------------------|
"""

_STARTER_FEEDBACK_CLASSES = """# feedback-classes.md — реестр пользовательских жалоб

| ID | Summary | Count | First seen | Last seen | Severity | Sample comment |
|----|---------|-------|------------|-----------|----------|---------------|
"""

_STARTER_BACKLOG = """# improvement-backlog.md — бэклог структурных улучшений

| ID | Created | Status | Intent | Provenance | Human review? |
|----|---------|--------|--------|------------|---------------|
"""


def bootstrap_starter_files() -> None:
    """Write the initial identity/scratchpad/knowledge files if they don't exist."""
    PATHS.ensure()
    starters: list[tuple[Path, str]] = [
        (identity_path(), _STARTER_IDENTITY),
        (scratchpad_path(), _STARTER_SCRATCHPAD),
        (patterns_path(), _STARTER_PATTERNS),
        (feedback_classes_path(), _STARTER_FEEDBACK_CLASSES),
        (backlog_path(), _STARTER_BACKLOG),
    ]
    for p, body in starters:
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text(body, encoding="utf-8")


__all__ = [
    "file_lock",
    "read_text",
    "append_dated_section",
    "replace_text",
    "identity_path",
    "scratchpad_path",
    "patterns_path",
    "feedback_classes_path",
    "backlog_path",
    "reflections_log_path",
    "append_scratchpad",
    "append_identity",
    "read_identity",
    "read_scratchpad",
    "bootstrap_starter_files",
]
