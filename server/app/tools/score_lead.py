"""`score_lead`: deterministic scoring function over the extracted fields.
See docs/architecture.md section 8.

Pure function — no LLM call, no I/O. Should be the easiest tool to
unit-test exhaustively.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.extraction import ExtractedFields
from app.tools.base import Tool, ToolResult
from app.tools.lookup_jurisdiction_rule import JurisdictionRule


class ScoreLeadArgs(BaseModel):
    extracted: ExtractedFields
    jurisdiction_rule: JurisdictionRule


class ScoreLeadResult(BaseModel):
    score: int
    breakdown: dict[str, int]


class ScoreLeadTool(Tool):
    name = "score_lead"
    description = "Deterministic scoring function over the extracted fields and jurisdiction rule."
    args_schema = ScoreLeadArgs
    result_schema = ScoreLeadResult

    def run(self, args: ScoreLeadArgs) -> ToolResult:
        """`args` is already validated against `ScoreLeadArgs` by
        `Tool.execute()` — this only needs to compute the score.

        TODO(tools/score_lead): implement a pure, deterministic weighted
        score (e.g. budget_band weight + urgency weight +/- jurisdiction risk
        adjustment). Must be a pure function of `args` — no randomness, no
        LLM calls — so run-to-run variance in the harness (section 11)
        cannot be attributed to this tool."""
        raise NotImplementedError
