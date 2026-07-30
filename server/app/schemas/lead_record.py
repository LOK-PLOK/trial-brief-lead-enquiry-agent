"""The final, verified lead record. See docs/architecture.md section 5 (LEADS table).

Assembled by the Executor from `parse_enquiry` + `lookup_jurisdiction_rule` +
`score_lead` + `write_record` tool outputs; this is what the Verifier checks
for fabrication and what gets persisted to the `leads` table on success.
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

    # TODO(agent/executor): decide final shape once score_lead/write_record
    # are implemented; this is an illustrative placeholder aggregate per the
    # architecture doc, not a final contract.
