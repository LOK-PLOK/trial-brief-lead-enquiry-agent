"""GET /api/leads. See docs/architecture.md section 3."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db

router = APIRouter(prefix="/api/leads", tags=["leads"])


@router.get("")
def list_leads(status: str | None = None, db: Session = Depends(get_db)):
    """TODO(api/routes_leads): implement via db/repository.list_leads();
    `status` filters to "accepted" | "quarantined" when provided."""
    raise HTTPException(status_code=501, detail="Not implemented yet.")
