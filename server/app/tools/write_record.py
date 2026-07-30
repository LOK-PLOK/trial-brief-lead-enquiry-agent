"""`write_record`: persists the record, rejecting duplicates on a normalized
hash of email and phone. See docs/architecture.md section 8.

Deterministic — no LLM call. Delegates hashing to services/dedupe.py so that
logic is independently unit-testable and reusable.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.extraction import ExtractedFields
from app.tools.base import Tool, ToolResult
from app.tools.lookup_jurisdiction_rule import JurisdictionRule
from app.tools.score_lead import ScoreLeadResult


class WriteRecordArgs(BaseModel):
    extracted: ExtractedFields
    jurisdiction_rule: JurisdictionRule
    score: ScoreLeadResult


class WriteRecordResult(BaseModel):
    lead_id: str
    dedupe_hash: str


class WriteRecordTool(Tool):
    name = "write_record"
    description = "Persists the record, rejecting duplicates on a normalized hash of email and phone."
    args_schema = WriteRecordArgs
    result_schema = WriteRecordResult

    def run(self, args: WriteRecordArgs) -> ToolResult:
        """TODO(tools/write_record):
        1. Compute `dedupe_hash` via services/dedupe.py from
           `args.extracted.email` / `args.extracted.phone`.
        2. Check db/repository.py for an existing lead with that hash;
           if found, return ToolResult(success=False, error="duplicate").
        3. Otherwise insert via db/repository.py and return
           ToolResult(success=True, data={...}).
        """
        raise NotImplementedError
