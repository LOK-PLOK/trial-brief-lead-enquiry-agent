"""The Planner. See docs/architecture.md section 6 and docs/contracts.md section 1.

Rule (docs/development-rules.md): the Planner never executes tools. This
module's only job is to produce a validated `Plan`; it must not import from
`agent/executor.py` or `tools/` (enforced structurally -- see
tests/unit/test_planner.py's import-boundary test).

Public surface: exactly one function, `build_plan()`. Everything else here is
a private (underscore-prefixed) helper.
"""

from __future__ import annotations

from app.agent.prompts.planner_prompt import PLANNER_SYSTEM_PROMPT, build_planner_user_prompt
from app.core.config import get_settings
from app.core.logging import get_logger, run_id_ctx, stage_ctx
from app.llm.base import ModelAdapter, StructuredCompletionRequest
from app.schemas.plan import Plan, ToolName

logger = get_logger(__name__)

# Deterministic, non-LLM guardrail layered on top of schema validation
# (docs/architecture.md sections 6 and 15): these two tools must each appear
# exactly once, checked in code rather than left to model judgment, since
# this is the cheap backstop against prompt injection talking the model into
# omitting (or duplicating) a required step.
_MANDATORY_TOOLS_EXACTLY_ONCE: tuple[ToolName, ...] = (
    ToolName.LOOKUP_JURISDICTION_RULE,
    ToolName.SCORE_LEAD,
)

# One initial attempt + exactly one retry, per docs/architecture.md section 6
# ("exactly one automatic repair-and-retry with the validation error fed back
# to the model") and docs/contracts.md section 1. This governs retries of the
# Planner's own guardrail check only -- the adapter's internal schema-shape
# retry (docs/contracts.md section 5) is a separate, opaque concern the
# Planner does not duplicate.
_MAX_ATTEMPTS = 2


class PlannerValidationError(Exception):
    """Raised when the Planner cannot obtain a valid `Plan`: either the
    adapter returned something that isn't actually a `Plan` (a violation of
    its own contract, docs/contracts.md section 5), or the mandatory-tool
    guardrail still fails after the one allowed retry.

    Per docs/contracts.md section 1's failure modes, callers should treat
    this the same as a schema-validation failure for harness reporting
    purposes (i.e. it counts toward the planner schema-breach-rate metric).
    """


def _check_mandatory_tools(plan: Plan) -> list[str]:
    """Return human-readable guardrail violations for `plan` (empty list if
    it passes). Pure function, no I/O -- trivially unit-testable."""
    violations = []
    for tool in _MANDATORY_TOOLS_EXACTLY_ONCE:
        count = sum(1 for step in plan.steps if step.tool is tool)
        if count != 1:
            violations.append(f"'{tool.value}' must appear exactly once in the plan, found {count}.")
    return violations


def _log_attempt(
    *,
    attempt: int,
    model: str,
    user_prompt: str,
    plan: Plan,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: float,
    violations: list[str],
) -> None:
    """Structured log of one adapter call: prompt, response, latency, and
    token usage (this task's explicit logging requirement), plus enough
    metadata to reconstruct why a retry did or didn't happen. This is the
    ephemeral stdout tier (docs/architecture.md section 12) -- the durable
    per-run trace in SQLite is populated later by agent/orchestrator.py, not
    here.
    """
    logger.info(
        "planner llm call completed",
        extra={
            "event": "planner_llm_call",
            "attempt": attempt,
            "model": model,
            "system_prompt": PLANNER_SYSTEM_PROMPT,
            "user_prompt": user_prompt,
            "response": plan.model_dump(mode="json"),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "latency_ms": latency_ms,
            "guardrail_violations": violations,
        },
    )


def build_plan(enquiry_text: str, tool_manifest: list[dict], adapter: ModelAdapter) -> Plan:
    """Call the Planner LLM and return a validated, guardrail-passing `Plan`.

    - Uses only the provider-agnostic `ModelAdapter` interface (never a
      concrete provider class) -- see docs/contracts.md section 5.
    - Uses structured outputs: every call sets `response_schema=Plan`, and
      the adapter's own contract guarantees `response.parsed` validates
      against it (or raises) before this function ever sees it.
    - Still validates the response itself rather than trusting the boundary
      blindly: confirms `response.parsed` really is a `Plan`, then applies
      the mandatory-tool guardrail (see `_check_mandatory_tools`), which the
      adapter has no way to check since it's a semantic rule, not a JSON
      Schema shape.
    - Retries at most once, using the same adapter contract, specifically
      when the guardrail fails -- a fresh, stateless call with the violation
      fed back into the prompt (docs/architecture.md section 6), never a
      continuation of the first call's conversation. If the adapter itself
      raises (its own schema retry already exhausted, or a provider error),
      that propagates immediately without an extra Planner-level retry.
    - Logs prompt, response, latency, and token usage for every attempt.
    """
    model = get_settings().resolved_model("planner")
    correction_note: str | None = None
    violations: list[str] = []

    stage_token = stage_ctx.set("planner")
    try:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            user_prompt = build_planner_user_prompt(
                enquiry_text, tool_manifest, correction_note=correction_note
            )
            request = StructuredCompletionRequest(
                system_prompt=PLANNER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_schema=Plan,
                model=model,
                max_tokens=1024,
                metadata={"run_id": run_id_ctx.get(), "stage": "planner", "attempt": attempt},
            )

            response = adapter.complete_structured(request)

            if not isinstance(response.parsed, Plan):
                # Defensive: the adapter contract guarantees `parsed` already
                # validates against `response_schema` -- but a boundary is
                # never trusted blindly ("Planner must validate all LLM
                # responses"). A wrong type here is a violated adapter
                # contract, not a recoverable planning mistake, so this is
                # not eligible for the guardrail retry below.
                raise PlannerValidationError(
                    f"adapter returned {type(response.parsed).__name__}, expected Plan"
                )
            plan = response.parsed

            violations = _check_mandatory_tools(plan)
            _log_attempt(
                attempt=attempt,
                model=response.model,
                user_prompt=user_prompt,
                plan=plan,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                latency_ms=response.latency_ms,
                violations=violations,
            )

            if not violations:
                return plan

            if attempt < _MAX_ATTEMPTS:
                logger.warning(
                    "planner guardrail violation, retrying once",
                    extra={
                        "event": "planner_guardrail_retry",
                        "attempt": attempt,
                        "violations": violations,
                    },
                )
                correction_note = (
                    "Your previous plan was INVALID: "
                    + " ".join(violations)
                    + " Return a corrected plan that fixes this while still satisfying "
                    "every requirement above."
                )

        raise PlannerValidationError(
            "Plan failed the mandatory-tool guardrail after retrying once: " + " ".join(violations)
        )
    finally:
        stage_ctx.reset(stage_token)
