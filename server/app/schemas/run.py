"""Top-level run objects: what a single pipeline execution produces, and what
the FastAPI layer returns to the client. See docs/architecture.md sections 3
(FastAPI Architecture) and 5 (RUNS table).

`RunResult` mirrors the `runs` table closely by design (see db/models.py) so
that the API response model and the persisted row don't drift apart.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.lead_record import LeadRecord
from app.schemas.plan import Plan
from app.schemas.tool_trace import ToolCallTrace
from app.schemas.verifier import VerifierDecision


class LlmCallUsage(BaseModel):
    """Normalized token/cost/latency accounting for a single LLM call.

    Produced by the model adapter layer (see llm/base.py) so every stage
    (planner, parse_enquiry, verifier, repair) reports usage the same way
    regardless of provider.
    """

    stage: str  # "planner" | "parse_enquiry" | "verifier" | "repair_planner" | "repair_verifier"
    model_provider: str
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: float


class RunResult(BaseModel):
    id: str
    enquiry_text: str
    enquiry_id: str | None = None
    repeat_index: int | None = None
    is_adversarial: bool = False

    plan: Plan | None = None
    plan_schema_valid: bool = False

    tool_call_trace: ToolCallTrace | None = None
    final_record: LeadRecord | None = None

    verifier_decision: VerifierDecision | None = None

    repair_attempted: bool = False
    repair_succeeded: bool | None = None

    final_status: str = "error"  # "completed" | "quarantined" | "error"

    # Stage / harness failure details (null on successful completed runs).
    error_type: str | None = None
    error_message: str | None = None
    traceback: str | None = None

    llm_calls: list[LlmCallUsage] = []
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_latency_ms: float = 0.0

    created_at: datetime | None = None


class RunSummary(BaseModel):
    """Lightweight projection of RunResult for list views (GET /api/runs)."""

    id: str
    enquiry_id: str | None = None
    final_status: str
    verifier_pass: bool | None = None
    total_cost_usd: float = 0.0
    total_latency_ms: float = 0.0
    created_at: datetime | None = None
