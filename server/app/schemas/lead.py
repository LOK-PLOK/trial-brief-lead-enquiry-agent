"""API response shape for `GET /api/leads`. See docs/architecture.md section 5
(LEADS table) and docs/contracts.md section 7, which lists this endpoint's
response model as "(TODO: not yet typed as a response model)" -- this is that
type, added now that the route itself is implemented (Phase 3).

Deliberately named `LeadOut`, not `Lead`, to avoid colliding with the ORM
`app.db.models.Lead` class it's built from -- the two are imported together
in `api/mappers.py`. The frontend's mirrored TypeScript type (`client/src/api/
types.ts`) is named `Lead`; this is the same shape under a different, Python-
side name to avoid the naming collision that doesn't exist in TypeScript's
separate `import type` namespace.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class LeadOut(BaseModel):
    id: str
    dedupe_hash: str
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    country: str | None = None
    budget_band: str | None = None
    asset_interest: str | None = None
    urgency: str | None = None
    score: int | None = None
    score_breakdown: dict | None = None
    jurisdiction_rule: dict | None = None
    status: str  # "accepted" | "quarantined"
    source_run_id: str | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
