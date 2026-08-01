"""ORM row -> API response schema conversions for the routes in this package.

Kept out of `db/repository.py` (which docs/contracts.md section 6 documents
as CRUD-only: reads/writes to the tables, nothing shaped for a particular API
consumer) and out of the individual `routes_*.py` modules (which
docs/architecture.md section 3 says should "only: validate the request, call
[a repository/pipeline function], and return the result" -- not carry mapping
logic inline). `Lead`/`HarnessSummary`/`HarnessMetrics` mostly map field-for-
field via `model_validate(row, from_attributes=True)`; `Run` -> `RunResult`
needs real reconstruction, since `db/repository.py::create_run()`'s own
mapping deliberately flattens `VerifierDecision` onto individual `Run`
columns and derives `model_provider`/`model_name` from `llm_calls[0]` rather
than storing either verbatim -- so undoing that is more than a rename.
"""

from __future__ import annotations

from app.db.models import HarnessBatch, Lead, Run
from app.schemas.harness import HarnessSummary
from app.schemas.lead import LeadOut
from app.schemas.lead_record import LeadRecord
from app.schemas.plan import Plan
from app.schemas.run import LlmCallUsage, RunResult, RunSummary
from app.schemas.tool_trace import ToolCallTrace
from app.schemas.verifier import VerifierDecision


def lead_to_out(lead: Lead) -> LeadOut:
    return LeadOut.model_validate(lead)


def harness_batch_to_summary(batch: HarnessBatch) -> HarnessSummary:
    return HarnessSummary.model_validate(batch)


def run_to_summary(run: Run) -> RunSummary:
    return RunSummary(
        id=run.id,
        enquiry_id=run.enquiry_id,
        final_status=run.final_status,
        verifier_pass=run.verifier_pass,
        total_cost_usd=run.total_cost_usd,
        total_latency_ms=run.total_latency_ms,
        created_at=run.created_at,
    )


def run_to_result(run: Run) -> RunResult:
    return RunResult(
        id=run.id,
        enquiry_text=run.enquiry_text,
        enquiry_id=run.enquiry_id,
        repeat_index=run.repeat_index,
        is_adversarial=run.is_adversarial,
        plan=Plan.model_validate(run.plan) if run.plan is not None else None,
        plan_schema_valid=run.plan_schema_valid,
        tool_call_trace=(
            ToolCallTrace.model_validate(run.tool_call_trace) if run.tool_call_trace is not None else None
        ),
        final_record=(LeadRecord.model_validate(run.final_record) if run.final_record is not None else None),
        verifier_decision=_verifier_decision_from_run(run),
        repair_attempted=run.repair_attempted,
        repair_succeeded=run.repair_succeeded,
        final_status=run.final_status,
        error_type=run.error_type,
        error_message=run.error_message,
        traceback=run.traceback,
        llm_calls=[_llm_call_usage(call) for call in run.llm_calls],
        total_tokens=run.total_tokens,
        total_cost_usd=run.total_cost_usd,
        total_latency_ms=run.total_latency_ms,
        created_at=run.created_at,
    )


def _verifier_decision_from_run(run: Run) -> VerifierDecision | None:
    """Undoes `create_run()`'s flattening of `VerifierDecision` onto
    individual `Run` columns. `None` whenever the Verifier never ran for
    this run at all (`verifier_pass is None`) -- an executor/planner
    failure that stopped the pipeline before verification, not a verifier
    result of "unknown"."""
    if run.verifier_pass is None:
        return None
    return VerifierDecision(
        **{"pass": run.verifier_pass},
        confidence=run.verifier_confidence or 0.0,
        fabrication_detected=run.fabrication_detected,
        fabricated_fields=run.fabricated_fields or [],
        plan_deviation_detected=run.plan_deviation_detected,
        deviation_details=run.deviation_details,
        reason=run.verifier_reason or "",
    )


def _llm_call_usage(call) -> LlmCallUsage:  # noqa: ANN001 -- app.db.models.LlmCall
    return LlmCallUsage(
        stage=call.stage,
        model_provider=call.model_provider,
        model_name=call.model_name,
        prompt_tokens=call.prompt_tokens,
        completion_tokens=call.completion_tokens,
        cost_usd=call.cost_usd,
        latency_ms=call.latency_ms,
    )
