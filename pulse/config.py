"""Single-source-of-truth for paths and runtime settings.

All other modules read constants from here. Do not duplicate path logic elsewhere.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from repo root if present. Idempotent.
_REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT_DEFAULT / ".env", override=False)


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


@dataclass(frozen=True)
class Paths:
    repo: Path
    data: Path
    logs: Path
    state: Path
    memory: Path
    knowledge: Path
    ml_models: Path
    synthetic: Path
    db: Path
    prompts: Path
    skills: Path
    bible: Path
    version_file: Path
    architecture_doc: Path

    def ensure(self) -> None:
        for p in [self.data, self.logs, self.state, self.memory, self.knowledge,
                  self.ml_models, self.synthetic, self.prompts, self.skills]:
            p.mkdir(parents=True, exist_ok=True)


def _build_paths() -> Paths:
    repo = _env_path("PULSE_REPO_DIR", _REPO_ROOT_DEFAULT)
    data = _env_path("PULSE_DATA_DIR", repo / "data")
    return Paths(
        repo=repo,
        data=data,
        logs=data / "logs",
        state=data / "state",
        memory=data / "memory",
        knowledge=data / "memory" / "knowledge",
        ml_models=data / "ml_models",
        synthetic=data / "synthetic",
        db=data / "sber_hr.db",
        prompts=repo / "prompts",
        skills=repo / "skills",
        bible=repo / "BIBLE.md",
        version_file=repo / "VERSION",
        architecture_doc=repo / "docs" / "ARCHITECTURE.md",
    )


PATHS: Paths = _build_paths()


@dataclass(frozen=True)
class Settings:
    host: str = field(default_factory=lambda: os.environ.get("PULSE_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("PULSE_PORT", 8080))
    evolution_interval_h: int = field(default_factory=lambda: _env_int("PULSE_EVOLUTION_INTERVAL_HOURS", 12))
    downvote_threshold: int = field(default_factory=lambda: _env_int("PULSE_DOWNVOTE_THRESHOLD", 2))
    daily_tick_interval_h: int = field(default_factory=lambda: _env_int("PULSE_DAILY_TICK_INTERVAL_HOURS", 24))
    budget_daily_usd: float = field(default_factory=lambda: _env_float("PULSE_BUDGET_DAILY_USD", 20.0))
    log_level: str = field(default_factory=lambda: os.environ.get("PULSE_LOG_LEVEL", "INFO").upper())
    oauth_token_present: bool = field(default_factory=lambda: bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")))


SETTINGS: Settings = Settings()


# Protected paths — kept tight on purpose. Lifted the broad pulse/*.py block
# in v1.0.0 (BIBLE Principle 13). Only the immune-system core stays armoured:
# the constitution, the safety prompt, and the DB schema (which any uncoordinated
# change would corrupt the synthetic dataset).
PROTECTED_PATHS: tuple[str, ...] = (
    "BIBLE.md",
    "prompts/SAFETY.md",
    "pulse/data_engine/schema.py",
)


def configure_logging() -> None:
    """Idempotent root logger config — INFO to stdout, RotatingFileHandler to data/logs/pulse.log."""
    root = logging.getLogger()
    if getattr(root, "_pulse_configured", False):
        return
    PATHS.ensure()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s — %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    from logging.handlers import RotatingFileHandler
    fh = RotatingFileHandler(PATHS.logs / "pulse.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root.handlers = [sh, fh]
    root.setLevel(SETTINGS.log_level)
    root._pulse_configured = True  # type: ignore[attr-defined]


def read_version() -> str:
    return PATHS.version_file.read_text(encoding="utf-8").strip()


__all__ = [
    "PATHS",
    "SETTINGS",
    "Paths",
    "Settings",
    "PROTECTED_PATHS",
    "configure_logging",
    "read_version",
]
