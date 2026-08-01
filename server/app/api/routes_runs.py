"""POST /api/runs, GET /api/runs, GET /api/runs/{id}. See docs/architecture.md
section 3.

This route module deliberately duplicates no pipeline/persistence logic: it
only validates the request, calls `Pipeline.run(...)` / `db/repository.py`,
and maps the result to a response model (via `api/mappers.py`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agent.orchestrator import Pipeline
from app.api.deps import get_adapter, get_app_settings, get_db
from app.api.mappers import run_to_result, run_to_summary
from app.core.config import Settings
from app.db import repository
from app.llm.base import ModelAdapter
from app.schemas.run import RunResult, RunSummary

router = APIRouter(prefix="/api/runs", tags=["runs"])


class CreateRunRequest(BaseModel):
    enquiry_text: str


@router.post("", response_model=RunResult)
def create_run(
    request: CreateRunRequest,
    db: Session = Depends(get_db),
    adapter: ModelAdapter = Depends(get_adapter),
    settings: Settings = Depends(get_app_settings),
) -> RunResult:
    """Runs the full pipeline synchronously for one enquiry.

    No domain-error handling lives here: `Pipeline.run()` is contractually
    specified (its own docstring, docs/contracts.md) to never raise -- every
    Planner/Executor/Verifier stage failure is already converted into a
    structured `RunResult(final_status="error", ...)` before it gets back
    here, so there is no `ToolExecutionError`/`LLMProviderError` branch for
    this route to catch. The request is persisted on the same request-scoped
    `db` session this route already has, rather than `Pipeline.run()`
    opening a second internal one.
    """
    return Pipeline(adapter=adapter, settings=settings).run(request.enquiry_text, db=db)


@router.get("", response_model=list[RunSummary])
def list_runs(db: Session = Depends(get_db)) -> list[RunSummary]:
    return [run_to_summary(run) for run in repository.list_runs(db)]


@router.get("/{run_id}", response_model=RunResult)
def get_run(run_id: str, db: Session = Depends(get_db)) -> RunResult:
    run = repository.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"No run found with id {run_id!r}.")
    return run_to_result(run)
