"""FastAPI surface — one process, one app, one router.

Endpoints:
  GET  /                         → web UI (static index.html)
  GET  /dashboard                → CEO dashboard (since v1.7.0)
  GET  /health                   → status JSON
  POST /api/chat                 → chat turn, returns {message_id, answer}
  POST /api/chat/stream          → same turn as SSE (status/tool_call/text/done)
  POST /api/feedback             → record like/dislike + optional comment
  POST /api/feedback/general     → free-form note for Pulse (since v1.6.0)
                                    — goes through alignment check before
                                    entering the evolution cycle
  GET  /api/history?limit=N      → last N chat turns
  GET  /api/employees/{emp_id}   → debug: full row from `employees`
  GET  /api/evolution            → status (Phase 8 will add POST /api/evolution)
  GET  /api/consciousness        → status (Phase 9)
  GET  /api/dashboard/*          → CEO dashboard aggregations (since v1.7.0)
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import PATHS, SETTINGS, configure_logging, read_version

configure_logging()
log = logging.getLogger(__name__)

app = FastAPI(title="Pulse", version=read_version())

# ---------------------------------------------------------------------------
# Static UI
# ---------------------------------------------------------------------------

WEB_DIR = PATHS.repo / "web"
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


_NOCACHE = {"Cache-Control": "no-cache, no-store, must-revalidate",
             "Pragma": "no-cache", "Expires": "0"}


@app.get("/", response_class=HTMLResponse)
def index() -> Any:
    idx = WEB_DIR / "index.html"
    # Block browser caching: every release potentially changes the JS that
    # talks to /api/chat/stream. A stale index.html silently breaks features
    # like multi-turn history (we hit this with v0.2.1).
    if idx.exists():
        return FileResponse(str(idx), headers=_NOCACHE)
    return HTMLResponse(
        "<html><body style='font-family:system-ui;padding:2rem'>"
        f"<h1>Пульс {read_version()}</h1>"
        "<p>UI отсутствует. Файл web/index.html не найден.</p>"
        "</body></html>",
        headers=_NOCACHE,
    )


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page() -> Any:
    """CEO dashboard (v1.7.0). 30-day window. Editorial morning-brief aesthetic.

    Drill-down links use `/?q=…` — the chat UI on `/` reads the query and
    pre-fills the input box.
    """
    p = WEB_DIR / "dashboard.html"
    if p.exists():
        return FileResponse(str(p), headers=_NOCACHE)
    return HTMLResponse(
        "<html><body style='font-family:system-ui;padding:2rem'>"
        f"<h1>Пульс {read_version()}</h1>"
        "<p>Дашборд отсутствует. Файл web/dashboard.html не найден.</p>"
        "</body></html>",
        headers=_NOCACHE,
    )


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "version": read_version(),
        "oauth_token_present": SETTINGS.oauth_token_present,
        "db_present": PATHS.db.exists(),
        "ui_present": (WEB_DIR / "index.html").exists(),
    })


# ---------------------------------------------------------------------------
# /api/chat
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)
    history: list[dict[str, str]] | None = None
    model: str | None = None  # "sonnet" | "opus" — default sonnet


@app.post("/api/chat")
async def api_chat(req: ChatRequest) -> JSONResponse:
    from .chat import handle_chat
    if not PATHS.db.exists():
        raise HTTPException(503, "DB missing — run `python -m scripts.seed --force` first.")
    out = await handle_chat(req.question, history=req.history, model=req.model or "sonnet")
    return JSONResponse(out)


@app.post("/api/chat/stream")
async def api_chat_stream(req: ChatRequest) -> StreamingResponse:
    """Same turn as /api/chat, served as Server-Sent Events.

    Lets the UI render tool calls and intermediate text as they arrive
    instead of staring at "думаю…" for minutes. Each event is one SSE
    frame: `data: <json>\\n\\n`.
    """
    from .chat import stream_chat_events
    if not PATHS.db.exists():
        raise HTTPException(503, "DB missing — run `python -m scripts.seed --force` first.")

    async def sse() -> Any:
        try:
            async for ev in stream_chat_events(req.question, history=req.history,
                                                model=req.model or "sonnet"):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as ex:  # last-resort guard — stream_chat_events normally yields error events itself
            log.exception("chat stream crashed")
            payload = json.dumps({"type": "error", "message": f"{type(ex).__name__}: {ex}"},
                                  ensure_ascii=False)
            yield f"data: {payload}\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        # Disable proxy buffering so events flush in real time.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


# ---------------------------------------------------------------------------
# /api/feedback
# ---------------------------------------------------------------------------

class FeedbackRequest(BaseModel):
    message_id: str = Field(..., min_length=3)
    verdict: str = Field(..., pattern="^(up|down)$")
    comment: str | None = None


@app.post("/api/feedback")
def api_feedback(req: FeedbackRequest) -> JSONResponse:
    PATHS.ensure()
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "message_id": req.message_id,
        "verdict": req.verdict,
        "comment": (req.comment or "").strip() or None,
    }
    with (PATHS.logs / "feedback.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return JSONResponse({"ok": True, "recorded_ts": rec["ts"]})


# ---------------------------------------------------------------------------
# /api/feedback/general — free-form note to Pulse (v1.6.0)
# ---------------------------------------------------------------------------

class GeneralFeedbackRequest(BaseModel):
    text: str = Field(..., min_length=4, max_length=4000)
    contact: str | None = Field(default=None, max_length=200)


@app.post("/api/feedback/general")
def api_feedback_general(req: GeneralFeedbackRequest) -> JSONResponse:
    """Append a free-form note to data/logs/general_feedback.jsonl.

    Unlike /api/feedback (which is tied to a specific message_id), these
    are open suggestions about Pulse itself — desired behaviour, missing
    capabilities, tone notes. Each entry is processed by the next
    evolution_cycle: an explicit alignment check (Opus call against
    BIBLE/SYSTEM/backlog/memory) decides whether to fold the note into
    the cycle as a synthesized dislike-class signal, or to log it as a
    rejected suggestion with reasoning. See pulse/evolution.py
    `evaluate_general_alignment`.
    """
    PATHS.ensure()
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "empty note")
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "id": "gen_" + secrets.token_hex(4),
        "text": text,
        "contact": (req.contact or "").strip() or None,
        "evaluated": False,
    }
    with (PATHS.logs / "general_feedback.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log.info("general feedback recorded: id=%s text_len=%d", rec["id"], len(text))
    return JSONResponse({"ok": True, "id": rec["id"], "ts": rec["ts"]})


# ---------------------------------------------------------------------------
# /api/history
# ---------------------------------------------------------------------------

@app.get("/api/history")
def api_history(limit: int = 30) -> JSONResponse:
    p = PATHS.logs / "chat.jsonl"
    if not p.exists():
        return JSONResponse({"items": []})
    limit = max(1, min(200, limit))
    lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    items: list[dict[str, Any]] = []
    for ln in lines[-limit:]:
        try:
            items.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return JSONResponse({"items": items})


# ---------------------------------------------------------------------------
# /api/employees/{emp_id} (debug)
# ---------------------------------------------------------------------------

@app.get("/api/employees/index")
def api_employees_index() -> JSONResponse:
    """Lightweight {emp_id, full_name} list for client-side mention detection.

    Declared BEFORE /api/employees/{emp_id} so FastAPI's path matcher
    doesn't capture "index" as a value of {emp_id}.
    """
    if not PATHS.db.exists():
        raise HTTPException(503, "DB missing — run seed first.")
    from .dashboard import get_employee_index
    return JSONResponse({"items": get_employee_index()})


@app.get("/api/employees/{emp_id}")
def api_employee(emp_id: str) -> JSONResponse:
    if not PATHS.db.exists():
        raise HTTPException(503, "DB missing — run seed first.")
    from sqlite_utils import Database
    db = Database(PATHS.db)
    rows = list(db.query("SELECT * FROM employees WHERE emp_id = :e", {"e": emp_id}))
    if not rows:
        raise HTTPException(404, f"emp_id {emp_id} not found")
    return JSONResponse(rows[0])


# ---------------------------------------------------------------------------
# /api/evolution and /api/consciousness — status stubs
# Implementations land in Phase 8 / Phase 9.
# ---------------------------------------------------------------------------

@app.get("/api/evolution")
def api_evolution_status() -> JSONResponse:
    from .state import load_state
    state = load_state()
    return JSONResponse({"evolution": state.get("evolution", {}),
                          "ml": state.get("ml", {})})


class EvolutionStartRequest(BaseModel):
    force: bool = False
    sdk_apply: bool = True


@app.post("/api/evolution")
async def api_evolution_start(req: EvolutionStartRequest) -> JSONResponse:
    from .evolution import evolution_cycle
    result = await evolution_cycle(force=req.force, sdk_apply=req.sdk_apply)
    return JSONResponse({
        "triggered": result.triggered,
        "skipped_reason": result.skipped_reason,
        "self_test_ok": result.self_test_ok,
        "committed": result.committed,
        "version": result.version,
        "notes": result.notes,
        "plan_intent": result.plan.intent if result.plan else None,
        "class_addressed": result.plan.class_addressed if result.plan else None,
    })


@app.get("/api/consciousness")
def api_consciousness_status() -> JSONResponse:
    from . import consciousness
    from .state import load_state
    state = load_state()
    return JSONResponse({
        "consciousness": state.get("consciousness", {}),
        "thread_alive": consciousness.is_alive(),
    })


class DeepReviewRequest(BaseModel):
    confirm: bool = False


@app.post("/api/deep_self_review")
async def api_deep_self_review(req: DeepReviewRequest) -> JSONResponse:
    if not req.confirm:
        raise HTTPException(400, "send {confirm: true} to run a heavy Opus call")
    from .deep_self_review import deep_self_review
    out = await deep_self_review()
    return JSONResponse({"ok": True, "ts": out["ts"]})


# ---------------------------------------------------------------------------
# /api/dashboard/* — CEO dashboard aggregations (v1.7.0)
# Thin FastAPI wrappers around pulse.dashboard pure functions. Default
# window=30 days (CEO rhythm). All endpoints are GET, no side effects.
# ---------------------------------------------------------------------------

def _require_db() -> None:
    if not PATHS.db.exists():
        raise HTTPException(503, "DB missing — run `python -m scripts.seed --force` first.")


@app.get("/api/dashboard/kpi")
def api_dashboard_kpi(window: int = 30) -> JSONResponse:
    _require_db()
    from .dashboard import get_kpi_strip
    return JSONResponse(get_kpi_strip(window=window))


@app.get("/api/dashboard/heatmap")
def api_dashboard_heatmap(window: int = 30) -> JSONResponse:
    _require_db()
    from .dashboard import get_workforce_heatmap
    return JSONResponse(get_workforce_heatmap(window=window))


@app.get("/api/dashboard/at_risk")
def api_dashboard_at_risk(window: int = 30, n: int = 7) -> JSONResponse:
    _require_db()
    from .dashboard import get_at_risk_top
    return JSONResponse({"items": get_at_risk_top(n=n, window=window)})


@app.get("/api/dashboard/archetypes")
def api_dashboard_archetypes(window: int = 30) -> JSONResponse:
    _require_db()
    from .dashboard import get_archetype_scatter
    return JSONResponse(get_archetype_scatter(window=window))


@app.get("/api/dashboard/trust_timeline")
def api_dashboard_trust_timeline(window: int = 30) -> JSONResponse:
    from .dashboard import get_trust_timeline
    return JSONResponse(get_trust_timeline(window=window))


@app.get("/api/dashboard/evolution_log")
def api_dashboard_evolution_log(n: int = 10) -> JSONResponse:
    from .dashboard import get_evolution_log
    return JSONResponse({"items": get_evolution_log(n=n)})


@app.get("/api/dashboard/rejected")
def api_dashboard_rejected(n: int = 5) -> JSONResponse:
    from .dashboard import get_rejected_suggestions
    return JSONResponse({"items": get_rejected_suggestions(n=n)})


@app.get("/api/dashboard/cost")
def api_dashboard_cost(window: int = 30) -> JSONResponse:
    from .dashboard import get_cost_breakdown
    return JSONResponse(get_cost_breakdown(window=window))


# ---------------------------------------------------------------------------
# /api/sidebar/* — chat sidebar feed (v1.9.0)
# ---------------------------------------------------------------------------

@app.get("/api/sidebar/archetypes")
def api_sidebar_archetypes() -> JSONResponse:
    _require_db()
    from .dashboard import get_archetype_counts
    return JSONResponse({"items": get_archetype_counts()})


@app.get("/api/sidebar/departments")
def api_sidebar_departments() -> JSONResponse:
    _require_db()
    from .dashboard import get_department_counts
    return JSONResponse({"items": get_department_counts()})


@app.get("/api/sidebar/recent_threads")
def api_sidebar_recent(n: int = 10) -> JSONResponse:
    from .dashboard import get_recent_threads
    return JSONResponse({"items": get_recent_threads(n=max(1, min(50, n)))})


# ---------------------------------------------------------------------------
# /api/employees/{emp_id}/* — hover-card + sparkline (v1.9.0)
# /api/employees/index and /api/employees/{emp_id} (debug) live earlier in
# the file, declared BEFORE these so {emp_id} doesn't shadow `index`.
# ---------------------------------------------------------------------------

@app.get("/api/employees/{emp_id}/card")
def api_employee_card(emp_id: str, window: int = 30) -> JSONResponse:
    _require_db()
    from .employee_card import get_employee_card
    out = get_employee_card(emp_id, window=window)
    if out is None:
        raise HTTPException(404, f"emp_id {emp_id} not found")
    return JSONResponse(out)


@app.get("/api/employees/{emp_id}/sparkline")
def api_employee_sparkline(emp_id: str, metric: str, window: int = 30) -> JSONResponse:
    _require_db()
    from .employee_card import get_sparkline
    out = get_sparkline(emp_id, metric, window=window)
    if out is None:
        raise HTTPException(400, f"unknown metric: {metric}")
    return JSONResponse(out)


# ---------------------------------------------------------------------------
# /api/hcm/* — façade panels (P14, v2.3.0+). Phase D1 surface:
# recruit / goals / learning / assess. Read-only, GET-only — any «action»
# from the UI routes back into chat with tab_context.
# ---------------------------------------------------------------------------

@app.get("/api/hcm/recruit/summary")
def api_hcm_recruit_summary(window: int = 30) -> JSONResponse:
    _require_db()
    from .hcm_panels import get_recruit_summary
    return JSONResponse(get_recruit_summary(window=window))


@app.get("/api/hcm/recruit/vacancies")
def api_hcm_recruit_vacancies(status: str | None = "active") -> JSONResponse:
    _require_db()
    from .hcm_panels import list_active_vacancies
    return JSONResponse({"items": list_active_vacancies(status=status)})


@app.get("/api/hcm/recruit/vacancies/{vacancy_id}")
def api_hcm_recruit_vacancy_detail(vacancy_id: str) -> JSONResponse:
    _require_db()
    from .hcm_panels import get_vacancy_detail
    out = get_vacancy_detail(vacancy_id)
    if out is None:
        raise HTTPException(404, f"vacancy {vacancy_id} not found")
    return JSONResponse(out)


@app.get("/api/hcm/goals/summary")
def api_hcm_goals_summary(emp_id: str | None = None,
                            period: str | None = None) -> JSONResponse:
    _require_db()
    from .hcm_panels import get_goals_summary
    return JSONResponse(get_goals_summary(emp_id=emp_id, period=period))


@app.get("/api/hcm/goals/my")
def api_hcm_goals_my(emp_id: str, period: str | None = None) -> JSONResponse:
    _require_db()
    from .hcm_panels import list_my_goals
    return JSONResponse({"items": list_my_goals(emp_id, period=period)})


@app.get("/api/hcm/goals/team")
def api_hcm_goals_team(manager_emp_id: str,
                         period: str | None = None) -> JSONResponse:
    _require_db()
    from .hcm_panels import list_team_goals
    return JSONResponse({"items": list_team_goals(manager_emp_id, period=period)})


@app.get("/api/hcm/learning/feed")
def api_hcm_learning_feed(emp_id: str, limit: int = 20) -> JSONResponse:
    _require_db()
    from .hcm_panels import get_learning_feed
    return JSONResponse({"items": get_learning_feed(emp_id, limit=limit)})


@app.get("/api/hcm/learning/my_courses")
def api_hcm_learning_courses(emp_id: str, status: str | None = None) -> JSONResponse:
    _require_db()
    from .hcm_panels import get_my_courses
    return JSONResponse({"items": get_my_courses(emp_id, status=status)})


@app.get("/api/hcm/assess/campaigns")
def api_hcm_assess_campaigns() -> JSONResponse:
    _require_db()
    from .hcm_panels import get_assessment_campaigns
    return JSONResponse(get_assessment_campaigns())


@app.get("/api/hcm/assess/my")
def api_hcm_assess_my(emp_id: str, period: str | None = None) -> JSONResponse:
    _require_db()
    from .hcm_panels import get_my_assessment
    return JSONResponse(get_my_assessment(emp_id, period=period))


# --- Phase D2: career / profile / structure / docs / analytics ---

@app.get("/api/hcm/career/my")
def api_hcm_career_my(emp_id: str) -> JSONResponse:
    _require_db()
    from .hcm_panels import get_my_career
    out = get_my_career(emp_id)
    if not out:
        raise HTTPException(404, f"emp {emp_id} not found")
    return JSONResponse(out)


@app.get("/api/hcm/career/internal_vacancies")
def api_hcm_career_internal(emp_id: str) -> JSONResponse:
    _require_db()
    from .hcm_panels import list_internal_vacancies
    return JSONResponse({"items": list_internal_vacancies(emp_id)})


@app.get("/api/hcm/career/talent_search")
def api_hcm_career_search(position_title: str | None = None,
                            grade_min: int | None = None,
                            grade_max: int | None = None,
                            unit_id: str | None = None,
                            open_to_offers: int | None = None,
                            min_recommended_by_count: int | None = None) -> JSONResponse:
    _require_db()
    from .hcm_panels import list_talent_search_results
    q = {
        "position_title":           position_title,
        "grade_min":                grade_min,
        "grade_max":                grade_max,
        "unit_id":                  unit_id,
        "open_to_offers":           open_to_offers,
        "min_recommended_by_count": min_recommended_by_count,
    }
    return JSONResponse({"items": list_talent_search_results(q)})


@app.get("/api/hcm/career/delegations")
def api_hcm_career_delegations(emp_id: str) -> JSONResponse:
    _require_db()
    from .hcm_panels import list_delegations
    return JSONResponse(list_delegations(emp_id))


@app.get("/api/hcm/profile/{emp_id}")
def api_hcm_profile(emp_id: str) -> JSONResponse:
    _require_db()
    from .hcm_panels import get_profile_full
    out = get_profile_full(emp_id)
    if not out:
        raise HTTPException(404, f"emp {emp_id} not found")
    return JSONResponse(out)


@app.get("/api/hcm/structure")
def api_hcm_structure(unit_id: str | None = None) -> JSONResponse:
    _require_db()
    from .hcm_panels import get_org_structure
    return JSONResponse(get_org_structure(unit_id))


@app.get("/api/hcm/docs/my_requests")
def api_hcm_docs_my_requests(emp_id: str) -> JSONResponse:
    _require_db()
    from .hcm_panels import list_my_hr_requests
    return JSONResponse({"items": list_my_hr_requests(emp_id)})


@app.get("/api/hcm/docs/team_calendar")
def api_hcm_docs_team_calendar(manager_emp_id: str, year: int, month: int) -> JSONResponse:
    _require_db()
    from .hcm_panels import get_team_calendar
    return JSONResponse(get_team_calendar(manager_emp_id, year, month))


@app.get("/api/hcm/docs/catalog")
def api_hcm_docs_catalog() -> JSONResponse:
    from .hcm_panels import get_request_catalog
    return JSONResponse({"items": get_request_catalog()})


@app.get("/api/hcm/analytics/overview")
def api_hcm_analytics_overview(window: int = 30) -> JSONResponse:
    _require_db()
    from .hcm_panels import get_hr_analytics_overview
    return JSONResponse(get_hr_analytics_overview(window=window))


@app.on_event("startup")
def _start_background_loops() -> None:
    """Kick off the consciousness loop on app start. Idempotent."""
    from . import consciousness
    consciousness.start()


@app.on_event("shutdown")
def _stop_background_loops() -> None:
    from . import consciousness
    consciousness.stop()


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------

def _check_preconditions() -> int:
    PATHS.ensure()
    if not PATHS.db.exists():
        log.warning("DB missing at %s — run scripts/seed.py first.", PATHS.db)
    if not SETTINGS.oauth_token_present:
        log.warning("CLAUDE_CODE_OAUTH_TOKEN not set — chat endpoints will fail.")
    return 0


def main() -> None:
    import uvicorn
    code = _check_preconditions()
    if code != 0:
        raise SystemExit(code)
    log.info("Pulse %s starting on %s:%d", read_version(), SETTINGS.host, SETTINGS.port)
    uvicorn.run(app, host=SETTINGS.host, port=SETTINGS.port, log_config=None)


if __name__ == "__main__":
    main()
