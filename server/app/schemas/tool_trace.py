"""Executor output: the record of what actually happened, one entry per
executed plan step. See docs/architecture.md section 7.

The verifier compares `Plan.steps` (what was intended) against
`ToolCallTrace.calls` (what actually ran) to detect plan deviation.
"""

from __future__ import annotations

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
    latency_ms: float


class ToolCallTrace(BaseModel):
    calls: list[ToolCall]

    # TODO(agent/executor): populate via Executor.run_plan(); this class is a
    # pure data container, no behaviour belongs here.
