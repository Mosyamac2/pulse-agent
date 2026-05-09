"""FastAPI app — thin REST surface. Phase 0 stub.

In later phases this gains /api/chat, /api/feedback, /api/history, /api/evolution,
/api/employees/* and a static UI. For now it exposes /health and / so we can
verify the process boots without the SDK installed.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from .config import PATHS, SETTINGS, configure_logging, read_version

configure_logging()
log = logging.getLogger(__name__)

app = FastAPI(title="Pulse", version=read_version())


@app.get("/health")
def health() -> JSONResponse:
    db_present = PATHS.db.exists()
    return JSONResponse(
        {
            "status": "ok",
            "version": read_version(),
            "oauth_token_present": SETTINGS.oauth_token_present,
            "db_present": db_present,
        }
    )


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return (
        "<html><body style='font-family:system-ui;padding:2rem'>"
        f"<h1>Пульс {read_version()}</h1>"
        "<p>Skeleton. UI поднимется в Phase 5.</p>"
        "</body></html>"
    )


def _check_preconditions() -> int:
    """Return non-zero exit code if the process should refuse to start."""
    PATHS.ensure()
    # In Phase 0 the DB doesn't have to exist yet — only later phases require it.
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
