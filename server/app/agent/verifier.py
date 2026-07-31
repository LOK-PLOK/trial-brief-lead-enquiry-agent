"""The independent Verifier. See docs/architecture.md section 9 and
docs/contracts.md section 3.

Rule (docs/development-rules.md): the Verifier is completely independent.
This module must NOT import anything from agent/planner.py or
agent/prompts/planner_prompt.py, and must issue its own, freshly
constructed, stateless LLM call -- never a self-check appended to a prior
call's context (enforced structurally -- see
tests/unit/test_verifier.py's import-boundary test).

Public surface: exactly one function, `verify()`. Everything else here is a
private (underscore-prefixed) helper.
"""

from __future__ import annotations

from app.agent.prompts.verifier_prompt import VERIFIER_SYSTEM_PROMPT, build_verifier_user_prompt
from app.core.config import get_settings
from app.core.logging import get_logger, run_id_ctx, stage_ctx
from app.llm.base import ModelAdapter, StructuredCompletionRequest
from app.schemas.plan import Plan
from app.schemas.tool_trace import ToolCallTrace
from app.schemas.verifier import VerifierDecision

logger = get_logger(__name__)


class VerifierValidationError(Exception):
    """Raised only when the adapter itself violates its own contract by
    returning something that isn't a `VerifierDecision` (docs/contracts.md
    section 5's structured-output guarantee) -- a bug in the adapter, not a
    normal verification outcome. Not eligible for any extra retry: per this
    task's requirement, `verify()` retries only according to the adapter's
    own internal contract (see llm/base.py), never with a Verifier-level
    retry loop layered on top (unlike the Planner's guardrail retry, there
    is no deterministic business rule here for the Verifier itself to
    re-attempt against)."""


def _detect_plan_deviation(plan: Plan, tool_call_trace: ToolCallTrace) -> str | None:
    """Deterministic, non-LLM ground truth for one of the two failure
    classes the brief requires (docs/contracts.md section 3 / brief section
    3.4): "the executor skipped, reordered or substituted a tool relative
    to the plan". Unlike fabrication, this is mechanically checkable from
    `plan.steps` and `tool_call_trace.calls` alone, so it is never left to
    the model's judgment in isolation -- see `verify()` for how this
    overrides the LLM's own `plan_deviation_detected` claim whenever it
    disagrees with this ground truth, satisfying "the Verifier must never
    trust the Planner or Executor outputs" for the one dimension that does
    not require natural-language understanding to check.

    Returns a human-readable description of the first deviation found, or
    `None` if the trace is consistent with the plan. A trace that is simply
    *shorter* than the plan -- because execution legitimately stopped after
    a tool failure (docs/contracts.md section 2) -- is NOT a deviation;
    only a mismatched, reordered, substituted, or extra step is.
    """
    planned = [step.tool for step in plan.steps]
    executed = [call.tool for call in tool_call_trace.calls]

    if len(executed) > len(planned):
        return (
            f"Trace contains {len(executed)} tool call(s) but the plan only specified "
            f"{len(planned)} step(s) -- an unplanned step was executed."
        )
    for position, (planned_tool, executed_tool) in enumerate(zip(planned, executed), start=1):
        if planned_tool != executed_tool:
            return (
                f"Step {position}: the plan specified '{planned_tool.value}' but the trace "
                f"shows '{executed_tool.value}' executed in its place."
            )
    return None


def _log_call(
    *,
    model: str,
    user_prompt: str,
    decision: VerifierDecision,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: float,
) -> None:
    """Structured log of the one adapter call `verify()` ever makes:
    prompt, response, latency, and token usage (this task's explicit
    logging requirement). Ephemeral stdout tier (docs/architecture.md
    section 12) -- the durable per-run trace in SQLite is populated later
    by agent/orchestrator.py, not here."""
    logger.info(
        "verifier llm call completed",
        extra={
            "event": "verifier_llm_call",
            "model": model,
            "system_prompt": VERIFIER_SYSTEM_PROMPT,
            "user_prompt": user_prompt,
            "response": decision.model_dump(mode="json", by_alias=True),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "latency_ms": latency_ms,
        },
    )


def verify(
    enquiry_text: str,
    plan: Plan,
    tool_call_trace: ToolCallTrace,
    final_record: dict,
    adapter: ModelAdapter,
) -> VerifierDecision:
    """Call the Verifier LLM and return a validated `VerifierDecision`.

    - Uses only the provider-agnostic `ModelAdapter` interface (never a
      concrete provider class) -- docs/contracts.md section 5.
    - Uses structured outputs: the call sets `response_schema=
      VerifierDecision`, and the adapter's own contract guarantees
      `response.parsed` validates against it (or raises) before this
      function ever sees it.
    - Issues exactly one adapter call, built entirely from
      `agent/prompts/verifier_prompt.py` -- a fresh `StructuredCompletionRequest`
      with no messages/history field at all (docs/development-rules.md:
      "Each LLM call is isolated"), and no import from `agent/planner.py`
      or `agent/prompts/planner_prompt.py` anywhere in this module. This is
      what makes the Verifier genuinely independent, not merely a
      differently-worded self-check.
    - Makes no extra retry of its own on top of the one call above -- per
      this task's requirement, retries happen only inside the adapter, per
      its own contract (see `llm/base.py`); there is no Verifier-level
      guardrail-retry loop analogous to the Planner's, because there is no
      equivalent deterministic business rule for the Verifier itself to
      correct and resubmit.
    - Still validates the response itself rather than trusting the
      boundary blindly: confirms `response.parsed` really is a
      `VerifierDecision`, then cross-checks its `plan_deviation_detected`
      claim against `_detect_plan_deviation()` -- a deterministic
      recomputation from `plan` and `tool_call_trace` that the model cannot
      get wrong by hallucination, since it involves no natural-language
      judgment. If the deterministic check finds a deviation the model
      missed, the decision is corrected (forced to `passed=False`,
      `plan_deviation_detected=True`) before it is returned -- the Verifier
      never simply relays the model's own unchecked claim for the one
      dimension that can be checked in code.
    - Logs prompt, response, latency, and token usage for the call.
    """
    model = get_settings().resolved_model("verifier")
    user_prompt = build_verifier_user_prompt(
        enquiry_text,
        plan.model_dump(mode="json"),
        tool_call_trace.model_dump(mode="json"),
        final_record,
    )
    request = StructuredCompletionRequest(
        system_prompt=VERIFIER_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        response_schema=VerifierDecision,
        model=model,
        metadata={"run_id": run_id_ctx.get(), "stage": "verifier"},
    )

    stage_token = stage_ctx.set("verifier")
    try:
        response = adapter.complete_structured(request)

        if not isinstance(response.parsed, VerifierDecision):
            # Defensive: the adapter contract guarantees `parsed` already
            # validates against `response_schema` -- but a boundary is
            # never trusted blindly ("the Verifier must never trust the
            # Planner or Executor outputs" applies equally to its own
            # adapter dependency).
            raise VerifierValidationError(
                f"adapter returned {type(response.parsed).__name__}, expected VerifierDecision"
            )
        decision = response.parsed

        llm_claimed_deviation = decision.plan_deviation_detected
        deviation = _detect_plan_deviation(plan, tool_call_trace)
        if deviation is not None:
            if not llm_claimed_deviation:
                logger.warning(
                    "verifier deterministic plan-deviation check caught a deviation the "
                    "model's own judgment missed",
                    extra={
                        "event": "verifier_plan_deviation_override",
                        "deterministic_deviation": deviation,
                    },
                )
            decision = decision.model_copy(
                update={
                    "passed": False,
                    "plan_deviation_detected": True,
                    "deviation_details": deviation,
                    "reason": decision.reason or deviation,
                }
            )

        _log_call(
            model=response.model,
            user_prompt=user_prompt,
            decision=decision,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            latency_ms=response.latency_ms,
        )
        return decision
    finally:
        stage_ctx.reset(stage_token)
