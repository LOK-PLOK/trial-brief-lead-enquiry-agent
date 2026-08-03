"""The independent Verifier's structured output. See docs/architecture.md section 9.

Must catch two distinct failure classes per the brief: fabrication and plan
deviation. `reason` is required and must be non-empty whenever `passed` is
False (see the commented-out validator below).

Extracted-field verification uses structured per-field labels
(SUPPORTED / CONTRADICTED / INSUFFICIENT_EVIDENCE). A deterministic
contradiction gate in `agent/verifier.py` ensures only CONTRADICTED
extracted fields may appear in `fabricated_fields`.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ExtractedFieldLabel(str, Enum):
    """Per-field support classification for enquiry-extracted values.

    Only CONTRADICTED may become a fabrication claim. SUPPORTED and
    INSUFFICIENT_EVIDENCE must never quarantine a run for that field.
    """

    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ExtractedFieldVerdict(BaseModel):
    """Structured LLM judgment for one extracted field.

    The Verifier must classify — not re-extract. `label` is the only
    authoritative LLM signal for whether an extracted field may be
    treated as fabricated; free-text `reason` alone is never enough.
    """

    field: str
    label: ExtractedFieldLabel
    note: str = ""


class VerifierDecision(BaseModel):
    passed: bool = Field(..., alias="pass")
    confidence: float = Field(..., ge=0.0, le=1.0)
    fabrication_detected: bool
    fabricated_fields: list[str] = Field(default_factory=list)
    # Structured extracted-field classifications. Default empty for backward
    # compatibility with older fixtures / adapters; the contradiction gate
    # still applies deterministic closed-enum checks when this is empty.
    extracted_field_verdicts: list[ExtractedFieldVerdict] = Field(default_factory=list)
    plan_deviation_detected: bool
    deviation_details: str | None = None
    reason: str = ""

    model_config = {"populate_by_name": True}

    # TODO(agent/verifier): add a model_validator enforcing that `reason` is
    # non-empty whenever `passed` is False. Intentional follow-up — business
    # rule on the verifier contract; should live here (schemas), not scattered
    # across call sites.
