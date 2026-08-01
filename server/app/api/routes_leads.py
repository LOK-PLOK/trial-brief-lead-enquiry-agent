"""GET /api/leads. See docs/architecture.md section 3."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.mappers import lead_to_out
from app.db import repository
from app.schemas.lead import LeadOut

router = APIRouter(prefix="/api/leads", tags=["leads"])

_VALID_STATUSES: tuple[Literal["accepted"], Literal["quarantined"]] = ("accepted", "quarantined")


@router.get("", response_model=list[LeadOut])
def list_leads(status: str | None = Query(default=None), db: Session = Depends(get_db)) -> list[LeadOut]:
    """`status`, when provided, must be one of `accepted | quarantined`
    (docs/contracts.md section 7's validation rule for this endpoint) --
    anything else is a `422`, consistent with how FastAPI already reports
    every other malformed-request case on this API."""
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {list(_VALID_STATUSES)}, got {status!r}.",
        )
    return [lead_to_out(lead) for lead in repository.list_leads(db, status=status)]
