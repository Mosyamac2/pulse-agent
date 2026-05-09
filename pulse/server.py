"""FastAPI surface — one process, one app, one router.

Endpoints:
  GET  /                         → web UI (static index.html)
  GET  /health                   → status JSON
  POST /api/chat                 → chat turn, returns {message_id, answer}
  POST /api/chat/stream          → same turn as SSE (status/tool_call/text/done)
  POST /api/feedback             → record like/dislike + optional comment
  GET  /api/history?limit=N      → last N chat turns
  GET  /api/employees/{emp_id}   → debug: full row from `employees`
  GET  /api/evolution            → status (Phase 8 will add POST /api/evolution)
  GET  /api/consciousness        → status (Phase 9)
"""
from __future__ import annotations

import json
import logging
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


@app.get("/", response_class=HTMLResponse)
def index() -> Any:
    idx = WEB_DIR / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return HTMLResponse(
        "<html><body style='font-family:system-ui;padding:2rem'>"
        f"<h1>Пульс {read_version()}</h1>"
        "<p>UI отсутствует. Файл web/index.html не найден.</p>"
        "</body></html>"
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
