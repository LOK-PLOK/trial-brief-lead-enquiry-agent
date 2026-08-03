"""`score_lead`: deterministic scoring function over the extracted fields.
See docs/architecture.md section 8.

Pure function — no LLM call, no I/O — so any run-to-run variance the
evaluation harness measures on identical input (docs/architecture.md
section 11) can never be attributed to this tool.

Scoring rules (fully documented here, not just in code comments, since this
is a fictional business rule this trial invented rather than a real
underwriting model):

- `budget` (0-40 points): the enquirer's stated/inferred official Trial A
  budget band (docs/WCC_Trial_A_Enquiry_Samples.md).
    D (>= USD 250,000) -> 40, C (USD 50,000-249,999) -> 30,
    B (USD 10,000-49,999) -> 20, A (< USD 10,000) -> 10, Unknown -> 0.
  Four evenly-spaced tiers replace the prior three-tier low/medium/high
  table one-for-one on enum migration -- same 0-40 range, same "higher
  band -> higher points, Unknown -> 0" shape, no scoring redesign.
- `urgency` (0-30 points): how time-sensitive the enquiry reads, using the
  official Trial A urgency band.
    Immediate -> 30, Within three months -> 15, Exploratory -> 5,
    Unknown -> 0. A direct one-for-one rename of the prior
    high/medium/low points (Immediate=high, Within three months=medium,
    Exploratory=low) -- same point values, same formula.
- `jurisdiction_risk` (0-20 points): how favorable the enquirer's
  jurisdiction is for processing this lead without extra compliance
  overhead (higher is better -- a *low*-risk jurisdiction earns more
  points, despite sharing its key name with the Executor's canonical
  `final_record` field, docs/contracts.md section 2's `ExecutionResult`
  example).
    restricted -> 0 (flagged for manual compliance review; contributes
      nothing positive to the score).
    not restricted, disclaimer required -> 15 (the common case).
    not restricted, no disclaimer required -> 20 (the most favorable case).

`score` is the plain sum of the three components above (maximum 90); no
other weighting, adjustment, or randomness is applied.
"""

from __future__ import annotations

import time

from pydantic import BaseModel

from app.schemas.extraction import BudgetBand, ExtractedFields, Urgency
from app.tools.base import Tool, ToolResult
from app.tools.lookup_jurisdiction_rule import JurisdictionRule

_BUDGET_POINTS: dict[BudgetBand, int] = {
    BudgetBand.D: 40,
    BudgetBand.C: 30,
    BudgetBand.B: 20,
    BudgetBand.A: 10,
    BudgetBand.UNKNOWN: 0,
}

_URGENCY_POINTS: dict[Urgency, int] = {
    Urgency.IMMEDIATE: 30,
    Urgency.WITHIN_THREE_MONTHS: 15,
    Urgency.EXPLORATORY: 5,
    Urgency.UNKNOWN: 0,
}

_JURISDICTION_RISK_RESTRICTED = 0
_JURISDICTION_RISK_WITH_DISCLAIMER = 15
_JURISDICTION_RISK_NO_DISCLAIMER = 20


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
        `Tool.execute()` -- this only needs to compute the score, per the
        module docstring's fully documented, pure, deterministic rules."""
        started = time.perf_counter()
        breakdown = {
            "budget": _BUDGET_POINTS[args.extracted.budget_band],
            "urgency": _URGENCY_POINTS[args.extracted.urgency],
            "jurisdiction_risk": _jurisdiction_risk_points(args.jurisdiction_rule),
        }
        result = ScoreLeadResult(score=sum(breakdown.values()), breakdown=breakdown)
        return ToolResult(
            success=True,
            data=result.model_dump(mode="json"),
            error=None,
            latency_ms=(time.perf_counter() - started) * 1000,
        )


def _jurisdiction_risk_points(rule: JurisdictionRule) -> int:
    if rule.restricted:
        return _JURISDICTION_RISK_RESTRICTED
    if rule.requires_disclaimer:
        return _JURISDICTION_RISK_WITH_DISCLAIMER
    return _JURISDICTION_RISK_NO_DISCLAIMER
