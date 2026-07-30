"""POST /api/runs, GET /api/runs, GET /api/runs/{id}. See docs/architecture.md
section 3.

TODO(api/routes_runs): once agent/orchestrator.py's Pipeline is implemented,
wire these up. This route module must not duplicate any pipeline/persistence
logic — it should only: validate the request, call `Pipeline.run(...)`, and
return the resulting `RunResult`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_adapter, get_app_settings, get_db
from app.core.config import Settings
from app.llm.base import ModelAdapter
from app.schemas.run import RunResult, RunSummary

router = APIRouter(prefix="/api/runs", tags=["runs"])


class CreateRunRequest:
    # TODO(api/routes_runs): replace with a proper Pydantic request model,
    # e.g. `class CreateRunRequest(BaseModel): enquiry_text: str`.
    pass


@router.post("", response_model=RunResult)
def create_run(
    db: Session = Depends(get_db),
    adapter: ModelAdapter = Depends(get_adapter),
    settings: Settings = Depends(get_app_settings),
) -> RunResult:
    """TODO(api/routes_runs): build a `Pipeline(adapter, settings)`, call
    `.run(enquiry_text)`, return the result. Map `ToolExecutionError` /
    schema validation errors to structured error responses rather than
    letting them bubble up as unhandled 500s."""
    raise HTTPException(status_code=501, detail="Not implemented yet.")


@router.get("", response_model=list[RunSummary])
def list_runs(db: Session = Depends(get_db)) -> list[RunSummary]:
    """TODO(api/routes_runs): implement via db/repository.list_runs()."""
    raise HTTPException(status_code=501, detail="Not implemented yet.")


@router.get("/{run_id}", response_model=RunResult)
def get_run(run_id: str, db: Session = Depends(get_db)) -> RunResult:
    """TODO(api/routes_runs): implement via db/repository.get_run(); 404 if missing."""
    raise HTTPException(status_code=501, detail="Not implemented yet.")
