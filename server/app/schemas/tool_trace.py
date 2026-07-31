"""Executor output: the record of what actually happened, one entry per
executed plan step. See docs/architecture.md section 7.

The verifier compares `Plan.steps` (what was intended) against
`ToolCallTrace.calls` (what actually ran) to detect plan deviation.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.plan import ToolName


class ToolCallStatus:
    SUCCESS = "success"
    ERROR = "error"


class ToolCall(BaseModel):
    step: int
    tool: ToolName
    args: dict
    status: str  # one of ToolCallStatus
    result: dict | None = None
    error: str | None = None
    # Structured (per-field) validation errors from `ToolValidationError.errors`
    # -- populated only when `status=error` was caused by a validation
    # failure (malformed/hallucinated args, or a malformed successful
    # result), `None` otherwise. Additive field: `error` alone already
    # carries a human-readable message for every failure kind, this adds the
    # machine-readable detail specifically for validation failures, per
    # agent/executor.py's explicit requirement to record validation errors
    # as structured data ("structured outputs everywhere").
    validation_errors: list[dict] | None = None
    latency_ms: float
    # Wall-clock bounds of this call, alongside the already-present
    # `latency_ms` -- needed to reconstruct an absolute timeline across
    # steps/stages (docs/architecture.md section 12), not just each step's
    # own duration.
    started_at: datetime
    finished_at: datetime


class ToolCallTrace(BaseModel):
    calls: list[ToolCall]

    # Populated by agent/executor.py's run_plan(); this class remains a pure
    # data container, no behaviour belongs here.
