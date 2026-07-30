"""GET /api/harness/summary, GET /api/harness/runs. See docs/architecture.md
sections 3 and 11.

The harness itself runs out-of-band as a CLI script (`evaluation/harness.py`),
never triggered from here — these routes only read what it already persisted.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db

router = APIRouter(prefix="/api/harness", tags=["harness"])


@router.get("/summary")
def harness_summary(db: Session = Depends(get_db)):
    """TODO(api/routes_harness): implement via
    db/repository.get_latest_harness_batch()."""
    raise HTTPException(status_code=501, detail="Not implemented yet.")


@router.get("/runs")
def harness_runs(db: Session = Depends(get_db)):
    """TODO(api/routes_harness): return the 45 individual runs belonging to
    the latest harness batch, for UI drill-down."""
    raise HTTPException(status_code=501, detail="Not implemented yet.")
