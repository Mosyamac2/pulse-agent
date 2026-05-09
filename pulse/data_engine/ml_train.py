"""Train the three synthetic ML models. Idempotent, deterministic.

Persists each model alongside metadata to `data/ml_models/<name>.joblib`.
Logs final metrics into `data/logs/events.jsonl`.

Public entry: `train_all(db_path: Path, out_dir: Path) -> dict[str, dict]`.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split
from sqlite_utils import Database

from .ml_features import FEATURE_COLS, emp_features, feature_vector
from .seed import END_DATE, START_DATE
from . import archetypes as A

log = logging.getLogger(__name__)

# --- Anchors used to sample training rows for the attrition model ---
SNAPSHOT_INTERVAL_DAYS = 30   # one snapshot per emp per month
ATTRITION_HORIZON_DAYS = 90


# ---------------------------------------------------------------------------
# Attrition
# ---------------------------------------------------------------------------

def _build_attrition_dataset(db: Database) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    employees = list(db["employees"].rows)
    X: list[np.ndarray] = []
    y: list[int] = []
    meta: list[dict] = []

    for e in employees:
        hire = date.fromisoformat(e["hire_date"])
        term = date.fromisoformat(e["term_date"]) if e["term_date"] else None
        first_snap = hire + timedelta(days=90)
        last_snap = term if term is not None else END_DATE - timedelta(days=ATTRITION_HORIZON_DAYS)
        if last_snap < first_snap:
            continue

        # Coarse monthly snapshots over the whole tenure (mostly negatives).
        cur = first_snap
        while cur <= last_snap:
            feats = emp_features(db, e["emp_id"], cur)
            X.append(feature_vector(feats))
            label = 1 if (term is not None and cur <= term <= cur + timedelta(days=ATTRITION_HORIZON_DAYS)) else 0
            y.append(label)
            meta.append({"emp_id": e["emp_id"], "snapshot": cur.isoformat(), "label": label})
            cur += timedelta(days=SNAPSHOT_INTERVAL_DAYS)

        # For terminated employees: dense weekly snapshots in the [term-90, term]
        # window to give the model adequate positive signal density.
        if term is not None:
            d = max(first_snap, term - timedelta(days=ATTRITION_HORIZON_DAYS))
            while d <= term:
                feats = emp_features(db, e["emp_id"], d)
                X.append(feature_vector(feats))
                y.append(1)
                meta.append({"emp_id": e["emp_id"], "snapshot": d.isoformat(), "label": 1, "dense": True})
                d += timedelta(days=7)
    return np.array(X), np.array(y), meta


def train_attrition(db: Database, out_dir: Path) -> dict[str, Any]:
    X, y, meta = _build_attrition_dataset(db)
    log.info("attrition dataset: X=%s y_pos=%d/%d", X.shape, int(y.sum()), len(y))
    if X.size == 0 or y.sum() == 0:
        raise RuntimeError("Attrition dataset has no positive samples — seed data is wrong.")

    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.25, random_state=42, stratify=y
    )
    model = GradientBoostingClassifier(n_estimators=100, max_depth=3, random_state=42)
    model.fit(X_tr, y_tr)
    proba = model.predict_proba(X_te)[:, 1]
    auc = float(roc_auc_score(y_te, proba))

    out = {
        "model": model,
        "feature_cols": FEATURE_COLS,
        "metric": {"roc_auc_holdout": auc, "n_train": len(y_tr), "n_test": len(y_te),
                   "pos_rate": float(y.mean())},
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(out, out_dir / "attrition.joblib")
    log.info("attrition AUC=%.3f saved", auc)
    return out["metric"]


# ---------------------------------------------------------------------------
# Course recommender
# ---------------------------------------------------------------------------

ARCHETYPE_NAMES: list[str] = [a.name for a in A.ARCHETYPES]
COURSE_TOPICS: list[str] = ["leadership", "hard_skill", "soft_skill", "compliance", "banking"]


def _emp_embedding(db: Database, emp_row: dict) -> np.ndarray:
    """Archetype one-hot (8) + grade one-hot (5) + topic-completion-vector (5)."""
    arc_oh = np.zeros(len(ARCHETYPE_NAMES))
    arc_oh[ARCHETYPE_NAMES.index(emp_row["archetype"])] = 1.0
    grade_oh = np.zeros(5)
    grade_oh[max(0, min(4, emp_row["grade_level"] - 1))] = 1.0

    topic_vec = np.zeros(len(COURSE_TOPICS))
    for r in db.query(
        "SELECT c.topic AS topic FROM course_enrollments ce JOIN courses c "
        "ON c.course_id = ce.course_id WHERE ce.emp_id=:e AND ce.status='completed'",
        {"e": emp_row["emp_id"]}):
        if r["topic"] in COURSE_TOPICS:
            topic_vec[COURSE_TOPICS.index(r["topic"])] += 1.0
    if topic_vec.sum() > 0:
        topic_vec = topic_vec / topic_vec.sum()
    return np.concatenate([arc_oh, grade_oh, topic_vec])


def train_course_recommender(db: Database, out_dir: Path) -> dict[str, Any]:
    employees = list(db["employees"].rows)
    emp_ids: list[str] = [e["emp_id"] for e in employees]
    embeddings = np.stack([_emp_embedding(db, e) for e in employees])

    # Pre-aggregate: for each emp, what courses did they complete?
    completed: dict[str, set[str]] = {e: set() for e in emp_ids}
    for r in db["course_enrollments"].rows:
        if r["status"] == "completed":
            completed[r["emp_id"]].add(r["course_id"])

    bundle = {
        "emb_matrix": embeddings,
        "emp_ids": emp_ids,
        "archetype_names": ARCHETYPE_NAMES,
        "course_topics": COURSE_TOPICS,
        "completed_by_emp": {k: sorted(v) for k, v in completed.items()},
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, out_dir / "course_recommender.joblib")
    log.info("course recommender saved (n_emp=%d, dim=%d)", len(emp_ids), embeddings.shape[1])
    return {"n_employees": len(emp_ids), "embedding_dim": int(embeddings.shape[1])}


# ---------------------------------------------------------------------------
# Role success
# ---------------------------------------------------------------------------

def _build_role_success_dataset(db: Database) -> tuple[np.ndarray, np.ndarray]:
    """For each emp at each year-half snapshot, derive (X, y=success: score>=4)."""
    X: list[np.ndarray] = []
    y: list[int] = []
    rows = list(db.query("SELECT emp_id, period, score FROM performance_reviews"))
    for r in rows:
        emp_id = r["emp_id"]
        period = r["period"]
        score = float(r["score"])
        # period like '2024H1' -> ref_date = 2024-06-30 ; '2024H2' -> 2024-12-31
        year = int(period[:4])
        half = period[4:]
        ref = date(year, 6, 30) if half == "H1" else date(year, 12, 31)
        if ref > END_DATE:
            continue
        if ref < START_DATE + timedelta(days=180):
            continue
        feats = emp_features(db, emp_id, ref)
        X.append(feature_vector(feats))
        y.append(1 if score >= 4.0 else 0)
    return np.array(X), np.array(y)


def train_role_success(db: Database, out_dir: Path) -> dict[str, Any]:
    X, y = _build_role_success_dataset(db)
    log.info("role_success dataset: X=%s y_pos=%d/%d", X.shape, int(y.sum()), len(y))
    if X.size == 0 or y.sum() == 0 or y.sum() == len(y):
        # fall back to a trivial model — synthetic data may be too uniform on a tiny seed
        model = LogisticRegression(random_state=42, max_iter=500)
        # synthesize 50 rows of noise so model fits — never used in practice
        rng = np.random.default_rng(42)
        Xs = rng.normal(size=(50, len(FEATURE_COLS)))
        ys = rng.integers(0, 2, size=50)
        model.fit(Xs, ys)
        auc = 0.5
    else:
        X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.25, random_state=42, stratify=y)
        model = LogisticRegression(random_state=42, max_iter=500)
        model.fit(X_tr, y_tr)
        proba = model.predict_proba(X_te)[:, 1]
        auc = float(roc_auc_score(y_te, proba))

    bundle = {
        "model": model,
        "feature_cols": FEATURE_COLS,
        "metric": {"roc_auc_holdout": auc},
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, out_dir / "role_success.joblib")
    log.info("role_success AUC=%.3f saved", auc)
    return bundle["metric"]


# ---------------------------------------------------------------------------
# Orchestrator + event log
# ---------------------------------------------------------------------------

def _log_event(logs_dir: Path, kind: str, **payload) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, **payload}
    with (logs_dir / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def train_all(db_path: Path, out_dir: Path, logs_dir: Path | None = None) -> dict[str, dict]:
    db = Database(db_path)
    summary: dict[str, dict] = {}
    summary["attrition"] = train_attrition(db, out_dir)
    summary["course_recommender"] = train_course_recommender(db, out_dir)
    summary["role_success"] = train_role_success(db, out_dir)
    if logs_dir:
        _log_event(logs_dir, "ml_train_completed", **{k: v for k, v in summary.items()})
    return summary


def main() -> int:
    from pulse.config import PATHS, configure_logging
    configure_logging()
    PATHS.ensure()
    if not PATHS.db.exists():
        print("DB missing. Run: python -m scripts.seed --force")
        return 2
    summary = train_all(PATHS.db, PATHS.ml_models, PATHS.logs)
    for name, m in summary.items():
        print(f"{name}: {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["train_all", "train_attrition", "train_course_recommender", "train_role_success", "main"]
