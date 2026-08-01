"""The final, verified lead record. See docs/architecture.md section 5 (LEADS table).

Assembled by the Executor from `parse_enquiry` + `lookup_jurisdiction_rule` +
`score_lead` outputs; `dedupe_hash` is computed from extracted contacts
(same algorithm as `write_record`) so the Verifier can judge the record
*before* any lead row is inserted. Persistence via `write_record` happens
only after Verifier pass (docs/architecture.md section 10).
"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.extraction import ExtractedFields


class LeadRecord(BaseModel):
    extracted: ExtractedFields
    jurisdiction_rule: dict
    score: int
    score_breakdown: dict
    dedupe_hash: str
