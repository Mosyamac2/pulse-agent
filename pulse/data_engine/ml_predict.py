"""Inference for the three models. Lazy load on first call; cached process-wide.

Designed to be wrapped by `pulse/tools/ml_tools.py` (Claude Agent SDK tools).
Returns plain Python types — never numpy — so JSON serialization is trivial.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sqlite_utils import Database

from ..config import PATHS
from .ml_features import FEATURE_COLS, emp_features, factor_explanation, feature_vector

log = logging.getLogger(__name__)

_CACHE: dict[str, Any] = {}


def _load(name: str) -> Any:
    if name in _CACHE:
        return _CACHE[name]
    path = PATHS.ml_models / f"{name}.joblib"
    if not path.exists():
        raise FileNotFoundError(f"Model {name} not trained yet. Run pulse.data_engine.ml_train.")
    bundle = joblib.load(path)
    _CACHE[name] = bundle
    return bundle


def invalidate_cache() -> None:
    _CACHE.clear()


def _open_db() -> Database:
    return Database(PATHS.db)


# ---------------------------------------------------------------------------
# Attrition
# ---------------------------------------------------------------------------

def predict_attrition_for_emp(emp_id: str, ref_date: date | None = None) -> dict[str, Any]:
    """Return P(termination in next 90d) and top-3 contributing factors."""
    db = _open_db()
    bundle = _load("attrition")
    if ref_date is None:
        # use latest activity_daily date
        rows = list(db.query("SELECT MAX(date) AS d FROM activity_daily"))
        ref_date = date.fromisoformat(rows[0]["d"]) if rows and rows[0]["d"] else date(2026, 5, 9)

    feats = emp_features(db, emp_id, ref_date)
    x = feature_vector(feats).reshape(1, -1)
    proba = float(bundle["model"].predict_proba(x)[0, 1])
    factors = factor_explanation(bundle["model"], feats, top_k=3)
    return {
        "emp_id": emp_id,
        "ref_date": ref_date.isoformat(),
        "probability": round(proba, 4),
        "factors": [{"feature": k, "weighted": round(v, 4)} for k, v in factors],
        "model_auc": bundle["metric"]["roc_auc_holdout"],
    }


# ---------------------------------------------------------------------------
# Course recommender
# ---------------------------------------------------------------------------

def recommend_courses_for_emp(emp_id: str, top_k: int = 5) -> dict[str, Any]:
    """kNN over employee embeddings: aggregate neighbours' completed courses."""
    db = _open_db()
    bundle = _load("course_recommender")
    emp_ids: list[str] = bundle["emp_ids"]
    if emp_id not in emp_ids:
        return {"emp_id": emp_id, "recommendations": [], "note": "unknown emp_id"}
    idx = emp_ids.index(emp_id)
    emb = bundle["emb_matrix"]
    me = emb[idx]
    # cosine similarity (handle zero vectors safely)
    norms = np.linalg.norm(emb, axis=1) + 1e-9
    sims = (emb @ me) / (norms * (np.linalg.norm(me) + 1e-9))
    sims[idx] = -1.0  # exclude self
    nearest = np.argsort(sims)[-5:][::-1]

    completed_by = bundle["completed_by_emp"]
    own = set(completed_by.get(emp_id, []))

    counts: dict[str, dict[str, Any]] = {}
    for j in nearest:
        peer = emp_ids[int(j)]
        for cid in completed_by.get(peer, []):
            if cid in own:
                continue
            d = counts.setdefault(cid, {"course_id": cid, "vote_count": 0, "via_peers": []})
            d["vote_count"] += 1
            d["via_peers"].append(peer)

    courses_idx = {c["course_id"]: c for c in db["courses"].rows}
    ranked = sorted(counts.values(), key=lambda x: x["vote_count"], reverse=True)[:top_k]
    for r in ranked:
        c = courses_idx.get(r["course_id"])
        if c:
            r["title"] = c["title"]
            r["topic"] = c["topic"]
            r["duration_h"] = c["duration_h"]

    return {
        "emp_id": emp_id,
        "neighbours": [emp_ids[int(j)] for j in nearest],
        "recommendations": ranked,
    }


# ---------------------------------------------------------------------------
# Role success
# ---------------------------------------------------------------------------

def predict_role_success(emp_id: str, position_id: str, ref_date: date | None = None) -> dict[str, Any]:
    db = _open_db()
    bundle = _load("role_success")
    if ref_date is None:
        ref_date = date.fromisoformat(list(db.query("SELECT MAX(date) AS d FROM activity_daily"))[0]["d"])
    feats = emp_features(db, emp_id, ref_date)
    # position context: similarity_to_unit lookup, grade gap
    pos_rows = list(db.query("SELECT * FROM positions WHERE position_id=:p", {"p": position_id}))
    if not pos_rows:
        return {"emp_id": emp_id, "position_id": position_id, "probability": 0.0,
                "note": "unknown position"}
    pos = pos_rows[0]
    x = feature_vector(feats).reshape(1, -1)
    proba = float(bundle["model"].predict_proba(x)[0, 1])
    # adjust for grade gap heuristically (model didn't see position_id directly)
    emp_rows = list(db.query("SELECT grade_level FROM employees WHERE emp_id=:e", {"e": emp_id}))
    grade_gap = pos["grade_level"] - (emp_rows[0]["grade_level"] if emp_rows else pos["grade_level"])
    if grade_gap > 1:
        proba *= 0.7 ** (grade_gap - 1)
    return {
        "emp_id": emp_id,
        "position_id": position_id,
        "probability": round(proba, 4),
        "grade_gap": int(grade_gap),
        "note": "success = perf_score >= 4 in 6 months",
    }


__all__ = [
    "predict_attrition_for_emp",
    "recommend_courses_for_emp",
    "predict_role_success",
    "invalidate_cache",
]
