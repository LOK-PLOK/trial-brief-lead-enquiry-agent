"""The Planner's structured output. See docs/architecture.md section 6.

`Plan` is what the Planner LLM call must return, validated against this
schema. It is purely descriptive: producing a `Plan` does not execute
anything (see development-rules.md: "Planner never executes tools").
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ToolName(str, Enum):
    """Must stay in sync with the tools registered in server/app/tools/registry.py.

    TODO(schemas): consider generating this enum from the registry at import
    time instead of hand-maintaining it, so the two can never drift.
    """

    PARSE_ENQUIRY = "parse_enquiry"
    LOOKUP_JURISDICTION_RULE = "lookup_jurisdiction_rule"
    SCORE_LEAD = "score_lead"
    WRITE_RECORD = "write_record"


class PlanStep(BaseModel):
    step: int = Field(..., ge=1, description="1-indexed position in the plan.")
    tool: ToolName
    args: dict = Field(default_factory=dict, description="Arguments for this tool call.")
    rationale: str = Field(
        ...,
        min_length=1,
        description="Short justification for this step; read by the verifier's "
        "plan-deviation check, not executed.",
    )


class Plan(BaseModel):
    steps: list[PlanStep]

    # TODO(agent/planner): enforce the deterministic, non-LLM policy guardrail
    # described in docs/architecture.md section 6 / section 15 here (e.g. a
    # model_validator asserting `lookup_jurisdiction_rule` and `score_lead`
    # each appear exactly once). This is intentionally left unimplemented in
    # the scaffold since it encodes a business rule, not a structural type.
