"""FastAPI app factory. See docs/architecture.md sections 3 and 13.

Same-origin (single container) needs no CORS. Split frontend/backend hosts
(e.g. two Render services) require `CORS_ORIGINS` so browser preflight
`OPTIONS` requests are answered by `CORSMiddleware` instead of 405.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import routes_harness, routes_health, routes_leads, routes_runs
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger, run_id_ctx
from app.db.session import init_db

logger = get_logger(__name__)

# Populated by the Docker build's client stage (see root Dockerfile); absent
# in local `uvicorn --reload` dev, where the Vite dev server is used instead.
_STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Lead Enquiry Agent",
        description="Two-role agent system with an independent verifier gate.",
    )

    # Creates tables on boot if they don't exist yet; see db/session.py's
    # init_db() docstring for why no migration tool is used for this trial.
    init_db()

    # Must be registered before routers so preflight OPTIONS is handled here
    # rather than falling through to route matching (which returns 405).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(routes_health.router)
    app.include_router(routes_runs.router)
    app.include_router(routes_leads.router)
    app.include_router(routes_harness.router)

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Last-resort net: docs/contracts.md section 7's failure-mode table
        requires domain errors to reach the client as "structured JSON error
        bodies ... never a bare unhandled 500". `Pipeline.run()` itself
        already never raises (its own contract), so in practice this only
        ever fires for something outside the pipeline proper -- e.g. the
        `get_adapter()` dependency raising because `OPENROUTER_API_KEY`
        isn't configured -- but the contract is about the HTTP boundary, not
        about which layer happens to raise."""
        logger.error(
            "unhandled exception reached the API boundary",
            exc_info=True,
            extra={
                "event": "unhandled_exception",
                "path": request.url.path,
                "run_id": run_id_ctx.get(),
            },
        )
        return JSONResponse(status_code=500, content={"detail": str(exc) or exc.__class__.__name__})

    if _STATIC_DIR.is_dir():
        # `html=True` serves index.html for unmatched paths, which is what a
        # single-page app with client-side tab state needs.
        app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")

    return app


app = create_app()
