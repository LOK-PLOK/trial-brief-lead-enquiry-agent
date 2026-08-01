"""The one-shot Repair Loop. See docs/architecture.md section 10.

Triggered when the Verifier fails a run. Exactly one repair attempt, then
quarantine if still failing — a submission must never be silently dropped
(docs/development-rules.md).

Repair never executes `write_record`. Persistence is gated by Verifier pass
in the Orchestrator; repair only re-runs the failing pre-persist stages.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agent.executor import run_plan
from app.agent.plan_utils import strip_write_record
from app.agent.prompts.parse_enquiry_prompt import (
    PARSE_ENQUIRY_SYSTEM_PROMPT,
    build_parse_enquiry_user_prompt,
)
from app.agent.prompts.planner_prompt import PLANNER_SYSTEM_PROMPT, build_planner_user_prompt
from app.agent.prompts.repair_prompt import augment_for_fabrication, augment_for_plan_deviation
from app.agent.verifier import verify
from app.core.config import get_settings
from app.core.logging import get_logger, run_id_ctx
from app.llm.base import ModelAdapter, StructuredCompletionRequest
from app.schemas.extraction import ExtractedFields
from app.schemas.plan import Plan, ToolName
from app.schemas.tool_trace import ToolCallStatus, ToolCallTrace
from app.schemas.verifier import VerifierDecision
from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry

logger = get_logger(__name__)


@dataclass
class RepairResult:
    succeeded: bool
    new_plan: Plan | None
    new_trace: ToolCallTrace | None
    new_record: dict | None
    new_verifier_decision: VerifierDecision
    strategy_used: str  # "reextract" | "reexecute" | "replan"


class _FixedParseEnquiry:
    """Duck-typed stand-in for `ParseEnquiryTool` used only during the
    `reextract` strategy: returns a precomputed `ExtractedFields` so the
    subsequent deterministic tools see the repaired extraction without a
    second unguided LLM call that could re-introduce fabrication.

    Intentionally does **not** subclass `Tool` (which would auto-register
    a duplicate name). Swapped into `ToolRegistry._tools` for one
    `run_plan()` call only.
    """

    def __init__(self, data: dict) -> None:
        self._data = data

    def execute(self, raw_args: dict) -> ToolResult:  # noqa: ARG002
        return ToolResult(success=True, data=self._data, error=None, latency_ms=0.0)


def _failed_step_index(trace: ToolCallTrace) -> int | None:
    """Index of the first non-success call in `trace`, if any.

    Used by the `reexecute` strategy to retry from the failed step while
    reusing earlier successful outputs (docs/architecture.md section 10).
    """
    for index, call in enumerate(trace.calls):
        if call.status != ToolCallStatus.SUCCESS:
            return index
    return None


def attempt_repair(
    enquiry_text: str,
    plan: Plan,
    tool_call_trace: ToolCallTrace,
    final_record: dict | None,
    verifier_decision: VerifierDecision,
    adapter: ModelAdapter,
) -> RepairResult:
    """Run exactly one repair pass, then re-verify with a fresh Verifier call.

    Strategy selection (docs/architecture.md section 10):
    - `fabrication_detected` -> `reextract`
    - `plan_deviation_detected` -> `reexecute` (from failed step when the
      prior trace stopped on an error; otherwise a full re-run of the
      pre-persist plan)
    - otherwise -> `replan` (corrected plan, then execute)

    `write_record` is stripped from every plan before execution — Repair
    must never persist a lead.
    """
    del final_record  # available for future strategy heuristics; unused today

    if verifier_decision.fabrication_detected:
        strategy = "reextract"
    elif verifier_decision.plan_deviation_detected:
        strategy = "reexecute"
    else:
        strategy = "replan"

    logger.info(
        "repair attempt starting",
        extra={
            "event": "repair_started",
            "strategy": strategy,
            "fabrication_detected": verifier_decision.fabrication_detected,
            "plan_deviation_detected": verifier_decision.plan_deviation_detected,
        },
    )

    registry = ToolRegistry(adapter=adapter)
    execution_plan = strip_write_record(plan)
    new_plan = execution_plan
    new_trace: ToolCallTrace | None = None
    new_record: dict | None = None

    if strategy == "reextract":
        extracted = _reextract_fields(enquiry_text, verifier_decision, adapter)
        registry._tools["parse_enquiry"] = _FixedParseEnquiry(extracted.model_dump(mode="json"))
        execution = run_plan(execution_plan, enquiry_text, registry)
        new_trace = execution.trace
        new_record = (
            execution.final_record.model_dump(mode="json") if execution.final_record is not None else None
        )
    elif strategy == "reexecute":
        failed_at = _failed_step_index(tool_call_trace)
        if failed_at is not None and failed_at < len(execution_plan.steps):
            # Retry from the failed step; reuse prior successful calls.
            prior = [
                call for call in tool_call_trace.calls[:failed_at] if call.tool is not ToolName.WRITE_RECORD
            ]
            execution = run_plan(
                execution_plan,
                enquiry_text,
                registry,
                prior_calls=prior,
                start_step_index=failed_at,
            )
        else:
            # Plan deviation with a complete (but wrong) trace: full re-run
            # of pre-persist tools only.
            execution = run_plan(execution_plan, enquiry_text, registry)
        new_trace = execution.trace
        new_record = (
            execution.final_record.model_dump(mode="json") if execution.final_record is not None else None
        )
    else:  # replan
        new_plan = strip_write_record(_replan(enquiry_text, verifier_decision, adapter))
        execution = run_plan(new_plan, enquiry_text, registry)
        new_trace = execution.trace
        new_record = (
            execution.final_record.model_dump(mode="json") if execution.final_record is not None else None
        )

    if new_record is None or new_trace is None:
        failed_decision = VerifierDecision(
            passed=False,
            confidence=0.0,
            fabrication_detected=verifier_decision.fabrication_detected,
            fabricated_fields=list(verifier_decision.fabricated_fields),
            plan_deviation_detected=verifier_decision.plan_deviation_detected,
            deviation_details=verifier_decision.deviation_details,
            reason=(
                f"Repair strategy {strategy!r} did not produce a final_record "
                f"(executor error: {execution.error!r}). Original verifier reason: "
                f"{verifier_decision.reason}"
            ),
        )
        return RepairResult(
            succeeded=False,
            new_plan=new_plan,
            new_trace=new_trace,
            new_record=None,
            new_verifier_decision=failed_decision,
            strategy_used=strategy,
        )

    new_decision = verify(enquiry_text, new_plan, new_trace, new_record, adapter)
    return RepairResult(
        succeeded=bool(new_decision.passed),
        new_plan=new_plan,
        new_trace=new_trace,
        new_record=new_record,
        new_verifier_decision=new_decision,
        strategy_used=strategy,
    )


def _reextract_fields(
    enquiry_text: str,
    verifier_decision: VerifierDecision,
    adapter: ModelAdapter,
) -> ExtractedFields:
    base_user = build_parse_enquiry_user_prompt(enquiry_text)
    user_prompt = augment_for_fabrication(base_user, list(verifier_decision.fabricated_fields))
    if verifier_decision.reason:
        user_prompt = f"{user_prompt}\n\nVerifier reason: {verifier_decision.reason}"

    response = adapter.complete_structured(
        StructuredCompletionRequest(
            system_prompt=PARSE_ENQUIRY_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_schema=ExtractedFields,
            model=get_settings().resolved_model("extractor"),
            max_tokens=768,
            metadata={"run_id": run_id_ctx.get(), "stage": "parse_enquiry", "repair": True},
        )
    )
    if not isinstance(response.parsed, ExtractedFields):
        raise TypeError(
            f"repair reextract returned {type(response.parsed).__name__}, expected ExtractedFields"
        )
    return response.parsed


def _replan(
    enquiry_text: str,
    verifier_decision: VerifierDecision,
    adapter: ModelAdapter,
) -> Plan:
    base_user = build_planner_user_prompt(enquiry_text, ToolRegistry.tool_manifest())
    details = verifier_decision.deviation_details or verifier_decision.reason or "unspecified failure"
    user_prompt = augment_for_plan_deviation(base_user, details)

    response = adapter.complete_structured(
        StructuredCompletionRequest(
            system_prompt=PLANNER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_schema=Plan,
            model=get_settings().resolved_model("planner"),
            max_tokens=768,
            metadata={"run_id": run_id_ctx.get(), "stage": "repair_planner"},
        )
    )
    if not isinstance(response.parsed, Plan):
        raise TypeError(f"repair replan returned {type(response.parsed).__name__}, expected Plan")

    plan = response.parsed
    for tool in (ToolName.LOOKUP_JURISDICTION_RULE, ToolName.SCORE_LEAD):
        count = sum(1 for step in plan.steps if step.tool is tool)
        if count != 1:
            raise ValueError(
                f"Repaired plan failed mandatory-tool guardrail: '{tool.value}' "
                f"must appear exactly once, found {count}."
            )
    return plan
