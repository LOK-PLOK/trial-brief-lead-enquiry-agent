"""FastAPI app factory. See docs/architecture.md sections 3 and 13.

Single-origin deployment: this process serves both the JSON API (under
`/api/*`) and, when present, the built React static assets — so no CORS
configuration is needed in production (confirmed single-container direction).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import routes_harness, routes_health, routes_leads, routes_runs
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import init_db

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

    # TODO(app/main): decide whether table creation belongs here at all once
    # Alembic migrations are wired up (see db/migrations/README.md) — for now
    # this keeps local/dev boot simple.
    init_db()

    app.include_router(routes_health.router)
    app.include_router(routes_runs.router)
    app.include_router(routes_leads.router)
    app.include_router(routes_harness.router)

    if _STATIC_DIR.is_dir():
        # `html=True` serves index.html for unmatched paths, which is what a
        # single-page app with client-side tab state needs.
        app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")

    return app


app = create_app()
