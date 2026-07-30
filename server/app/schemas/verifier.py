"""The independent Verifier's structured output. See docs/architecture.md section 9.

Must catch two distinct failure classes per the brief: fabrication and plan
deviation. `reason` is required and must be non-empty whenever `passed` is
False (see the commented-out validator below).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class VerifierDecision(BaseModel):
    passed: bool = Field(..., alias="pass")
    confidence: float = Field(..., ge=0.0, le=1.0)
    fabrication_detected: bool
    fabricated_fields: list[str] = Field(default_factory=list)
    plan_deviation_detected: bool
    deviation_details: str | None = None
    reason: str = ""

    model_config = {"populate_by_name": True}

    # TODO(agent/verifier): add a model_validator enforcing that `reason` is
    # non-empty whenever `passed` is False. Left out of the scaffold because
    # it's a business rule on the verifier's contract, not a structural type,
    # but it should live here (schemas), not scattered across call sites.
