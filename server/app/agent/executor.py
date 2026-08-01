"""The Executor. See docs/architecture.md section 7 and docs/contracts.md section 2.

Rule (docs/development-rules.md): the Executor is deterministic. This module
contains no LLM calls of its own -- it only dispatches through whatever
`ToolRegistry` it's given (`registry.get_tool(name).execute(args)`); one of
those tools (`parse_enquiry`) happens to be LLM-backed internally, but that
call is entirely opaque to this module's control flow, which never imports a
concrete `Tool` subclass -- only the abstract `Tool`/`ToolRegistry` types.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import ValidationError

from app.core.logging import get_logger, stage_ctx
from app.schemas.lead_record import LeadRecord
from app.schemas.plan import Plan, PlanStep, ToolName
from app.schemas.tool_trace import ToolCall, ToolCallStatus, ToolCallTrace
from app.services.dedupe import compute_dedupe_hash
from app.tools.base import ToolValidationError
from app.tools.registry import ToolRegistry

logger = get_logger(__name__)


class ToolExecutionError(Exception):
    """Not raised by `run_plan()` itself -- every failure category it
    encounters is converted into a structured `ExecutionResult(error=...)`
    instead (see `run_plan()`'s docstring), so the trace is always
    preserved and the pipeline never crashes on a tool's behalf. This class
    remains available for a future caller (e.g. the Orchestrator) that
    wants to re-raise a failed `ExecutionResult` as an exception at its own
    layer, per the "or" in docs/contracts.md section 2's error behavior.
    """


@dataclass
class ExecutionResult:
    trace: ToolCallTrace
    final_record: LeadRecord | None
    error: str | None = None


# Pre-persist tools whose combined successful outputs assemble a
# `LeadRecord` for the Verifier. `write_record` is intentionally excluded:
# it runs only after the Verifier has passed (docs/architecture.md §10),
# and `dedupe_hash` is computed here from extracted contacts so the record
# can be judged before any DB insert. A plan that omits one of these, or
# stops before reaching it, simply yields `final_record=None` -- that is
# not itself an error.
_RECORD_ASSEMBLY_TOOLS: tuple[ToolName, ...] = (
    ToolName.PARSE_ENQUIRY,
    ToolName.LOOKUP_JURISDICTION_RULE,
    ToolName.SCORE_LEAD,
)

# Some canonical tools' args are, by their fixed `args_schema` (docs/contracts.md
# section 4's "Registered tools" table), literally another canonical tool's own
# output -- `score_lead` needs the real `extracted`/`jurisdiction_rule` objects,
# `write_record` needs `extracted`/`jurisdiction_rule`/`score`. The Planner
# cannot know these values at planning time (they don't exist until the
# referenced tool actually runs) -- `agent/prompts/planner_prompt.py` already
# documents exactly this: "Do not fabricate a tool's arguments when the real
# value can only come from ... an earlier step's output you have not seen yet
# -- use a short descriptive placeholder in `args` ... the deterministic
# Executor resolves the real value at execution time." (docs/contracts.md
# section 1's own Plan JSON example shows this literally, e.g.
# `"args": {"extracted": "..."}`.) This mapping is what makes that
# already-documented promise real.
#
# For each `(consuming tool, arg name)` pair below, if the named source tool
# already succeeded earlier in *this* execution, its actual result is
# substituted for whatever the Plan's `args` held for that key -- a
# correctness safeguard the Executor applies deterministically, never a
# planning decision, and never overriding a key with no known canonical
# source (e.g. `lookup_jurisdiction_rule`'s `country` is only overridden once
# `parse_enquiry` has actually produced one; a Plan that supplies `country`
# directly -- e.g. because `parse_enquiry` isn't in this particular plan at
# all -- keeps its own value). This changes only what `args` are handed to
# `tool.execute()` (and what gets recorded on the resulting `ToolCall`, which
# is strictly more useful for observability than an LLM placeholder that was
# never actually used) -- it does not change `run_plan()`'s signature,
# iteration order, stop-on-failure semantics, or any of the three failure
# categories documented below.
_CHAINED_ARG_SOURCES: dict[ToolName, dict[str, ToolName]] = {
    ToolName.LOOKUP_JURISDICTION_RULE: {"country": ToolName.PARSE_ENQUIRY},
    ToolName.SCORE_LEAD: {
        "extracted": ToolName.PARSE_ENQUIRY,
        "jurisdiction_rule": ToolName.LOOKUP_JURISDICTION_RULE,
    },
    ToolName.WRITE_RECORD: {
        "extracted": ToolName.PARSE_ENQUIRY,
        "jurisdiction_rule": ToolName.LOOKUP_JURISDICTION_RULE,
        "score": ToolName.SCORE_LEAD,
    },
}

# `lookup_jurisdiction_rule`'s chained arg is a *single field* of its source
# tool's result (`ExtractedFields.country`), not the whole object -- every
# other chained arg above uses its source tool's entire result verbatim.
_CHAINED_ARG_SUBFIELD: dict[tuple[ToolName, str], str] = {
    (ToolName.LOOKUP_JURISDICTION_RULE, "country"): "country",
}


def _resolve_args(step: PlanStep, enquiry_text: str, calls: list[ToolCall]) -> dict:
    """Return the args to actually execute `step` with (see
    `_CHAINED_ARG_SOURCES`'s docstring for why this exists).

    `parse_enquiry`'s `enquiry_text` arg is always pinned to the
    authoritative raw text this whole run was invoked with, regardless of
    whatever copy (if any) the Plan itself carries -- the real value is
    always already known (it's `run_plan()`'s own parameter), so there is
    never a reason to trust a possibly-stale or placeholder copy instead.
    """
    args = dict(step.args)

    if step.tool is ToolName.PARSE_ENQUIRY:
        args["enquiry_text"] = enquiry_text
        return args

    chained = _CHAINED_ARG_SOURCES.get(step.tool)
    if not chained:
        return args

    latest_success_by_tool: dict[ToolName, dict] = {
        call.tool: call.result
        for call in calls
        if call.status == ToolCallStatus.SUCCESS and call.result is not None
    }

    for arg_name, source_tool in chained.items():
        source_result = latest_success_by_tool.get(source_tool)
        if source_result is None:
            continue  # Source hasn't succeeded (yet, or ever this run) -- leave the Plan's own value.
        subfield = _CHAINED_ARG_SUBFIELD.get((step.tool, arg_name))
        if subfield is None:
            args[arg_name] = source_result
        elif subfield in source_result:
            args[arg_name] = source_result[subfield]
        # else: the source tool's result doesn't carry this subfield (e.g. a
        # test mock returning a partial result) -- leave the Plan's own
        # value rather than raising; this is a resolution *aid*, never a
        # new source of failure the strict args_schema validation
        # downstream wasn't already going to catch on its own.

    return args


def _elapsed_ms(perf_start: float) -> float:
    return (time.perf_counter() - perf_start) * 1000


def _record_call(
    *,
    step: PlanStep,
    args: dict,
    status: str,
    result: dict | None,
    error: str | None,
    validation_errors: list[dict] | None,
    started_at: datetime,
    finished_at: datetime,
    latency_ms: float,
) -> ToolCall:
    """Build the one `ToolCall` every executed step produces, regardless of
    outcome (docs/contracts.md section 2: "every step, success or failure,
    produces exactly one ToolCall entry"). `args` is whatever was actually
    passed to `tool.execute()` -- see `_resolve_args()` -- not necessarily
    `step.args` verbatim."""
    return ToolCall(
        step=step.step,
        tool=step.tool,
        args=args,
        status=status,
        result=result,
        error=error,
        validation_errors=validation_errors,
        latency_ms=latency_ms,
        started_at=started_at,
        finished_at=finished_at,
    )


def _log_call(call: ToolCall) -> None:
    """Ephemeral stdout tier (docs/architecture.md section 12) -- the
    durable tier is the `ToolCallTrace` this module returns, persisted
    later by agent/orchestrator.py. Logs every field this task's logging
    requirement calls for: start/end time, latency, success/failure,
    input, output, validation errors."""
    log = logger.info if call.status == ToolCallStatus.SUCCESS else logger.error
    log(
        f"executor tool call {call.status}",
        extra={
            "event": "executor_tool_call",
            "step": call.step,
            "tool": call.tool.value,
            "status": call.status,
            "input": call.args,
            "output": call.result,
            "error": call.error,
            "validation_errors": call.validation_errors,
            "latency_ms": call.latency_ms,
            "started_at": call.started_at,
            "finished_at": call.finished_at,
        },
    )


def _assemble_final_record(calls: list[ToolCall]) -> LeadRecord | None:
    """Best-effort assembly of the pre-persist `LeadRecord` from each
    assembly tool's successful output (see `_RECORD_ASSEMBLY_TOOLS`).
    `dedupe_hash` is derived from extracted email/phone via
    `compute_dedupe_hash` -- the same function `write_record` uses -- so the
    Verifier can judge the record before any lead row is inserted. Never
    raises: returns `None` if the plan didn't include all assembly tools, if
    one of them didn't succeed, or if their outputs don't combine into a
    valid `LeadRecord` for any other reason -- an incomplete/non-canonical
    plan is not itself an execution error."""
    results_by_tool: dict[str, dict] = {
        call.tool.value: call.result
        for call in calls
        if call.status == ToolCallStatus.SUCCESS and call.result is not None
    }
    if not all(tool.value in results_by_tool for tool in _RECORD_ASSEMBLY_TOOLS):
        return None

    try:
        score_data = results_by_tool[ToolName.SCORE_LEAD.value]
        extracted = results_by_tool[ToolName.PARSE_ENQUIRY.value]
        return LeadRecord(
            extracted=extracted,
            jurisdiction_rule=results_by_tool[ToolName.LOOKUP_JURISDICTION_RULE.value],
            score=score_data["score"],
            score_breakdown=score_data["breakdown"],
            dedupe_hash=compute_dedupe_hash(extracted.get("email"), extracted.get("phone")),
        )
    except (KeyError, ValidationError, TypeError, AttributeError):
        logger.warning(
            "could not assemble final_record from otherwise-successful tool outputs",
            extra={"event": "executor_final_record_assembly_failed"},
        )
        return None


def run_plan(
    plan: Plan,
    enquiry_text: str,
    registry: ToolRegistry,
    *,
    prior_calls: list[ToolCall] | None = None,
    start_step_index: int = 0,
) -> ExecutionResult:
    """Execute `plan.steps` strictly in the order given, dispatching through
    `registry.get_tool()` + `Tool.execute()` -- never by parsing model
    prose, and never by importing a concrete tool class (docs/architecture.md
    section 7; docs/contracts.md section 2). `registry` is accepted as a
    parameter (an additive, backward-compatible extension of the documented
    2-argument signature) because there is no other sanctioned way for this
    module to obtain tool instances without either hard-importing a concrete
    `Tool` subclass (forbidden) or constructing a `ModelAdapter` itself
    (which would make the Executor responsible for LLM-provider wiring it
    has no business knowing about, and would make it untestable with mock
    tools) -- see docs/contracts.md's cross-cutting rule that a contract
    change requires justification; this one is documented here and in
    docs/contracts.md section 2.

    `enquiry_text` is accepted per the documented contract for symmetry with
    tools that might need the raw text directly in the future; step
    dispatch itself only ever uses `step.args` from the already-validated
    `Plan`.

    `prior_calls` / `start_step_index` (additive, used by the Repair Loop's
    "retry from the failed step" strategy): when set, successful calls
    before `start_step_index` are reused verbatim and execution resumes at
    that index. Defaults (`None` / `0`) preserve the original full-plan
    behaviour.

    Every step produces exactly one `ToolCall`, appended to the trace
    regardless of outcome, so the returned trace is always complete up to
    wherever execution stopped -- even when it stops early. Three distinct
    failure categories are recognized, and every one of them is converted
    into a structured `ExecutionResult(error=...)` rather than raising or
    crashing the pipeline:

    1. **Tool validation failure** -- `Tool.execute()` raises
       `ToolValidationError` (malformed/hallucinated `step.args`, or a tool
       bug returning a malformed successful result). The structured
       per-field errors are preserved on the `ToolCall.validation_errors`.
    2. **Controlled tool failure** -- `Tool.execute()` returns normally with
       `ToolResult(success=False, error=...)` (an expected business
       outcome, not a bug). Note: `write_record` duplicate rejection is
       handled by the Orchestrator *after* Verifier pass, not here.
    3. **Unexpected internal exception** -- anything else (a tool bug, e.g.
       today's placeholder `NotImplementedError`; a provider timeout inside
       `parse_enquiry`; an unknown tool name from the registry). Logged
       loudly at ERROR with a full traceback, but still converted into the
       same structured failure shape rather than left to propagate and
       crash the caller.

    Execution stops at the first failure of any kind -- this is the only
    way a step goes unexecuted; the Executor never itself skips, reorders,
    or substitutes a step (it is literally "iterate `plan.steps`"). Only
    when every step succeeds does it attempt to assemble `final_record`.
    """
    calls: list[ToolCall] = list(prior_calls or [])[:start_step_index]
    stage_token = stage_ctx.set("executor")
    try:
        for step in plan.steps[start_step_index:]:
            started_at = datetime.now(UTC)
            perf_start = time.perf_counter()

            try:
                tool = registry.get_tool(step.tool.value)
            except KeyError:
                call = _record_call(
                    step=step,
                    args=step.args,
                    status=ToolCallStatus.ERROR,
                    result=None,
                    error=f"Unknown tool: {step.tool.value!r}",
                    validation_errors=None,
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                    latency_ms=_elapsed_ms(perf_start),
                )
                calls.append(call)
                _log_call(call)
                return ExecutionResult(trace=ToolCallTrace(calls=calls), final_record=None, error=call.error)

            resolved_args = _resolve_args(step, enquiry_text, calls)

            try:
                result = tool.execute(resolved_args)
            except ToolValidationError as exc:
                # Category 1: tool validation failure.
                call = _record_call(
                    step=step,
                    args=resolved_args,
                    status=ToolCallStatus.ERROR,
                    result=None,
                    error=str(exc),
                    validation_errors=exc.errors,
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                    latency_ms=_elapsed_ms(perf_start),
                )
                calls.append(call)
                _log_call(call)
                return ExecutionResult(trace=ToolCallTrace(calls=calls), final_record=None, error=call.error)
            except Exception as exc:
                # Category 3: unexpected internal exception -- never let this
                # crash the pipeline; log loudly, then convert it.
                logger.error(
                    "unexpected exception during tool execution",
                    exc_info=True,
                    extra={
                        "event": "executor_unexpected_exception",
                        "step": step.step,
                        "tool": step.tool.value,
                    },
                )
                call = _record_call(
                    step=step,
                    args=resolved_args,
                    status=ToolCallStatus.ERROR,
                    result=None,
                    error=f"{type(exc).__name__}: {exc}",
                    validation_errors=None,
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                    latency_ms=_elapsed_ms(perf_start),
                )
                calls.append(call)
                _log_call(call)
                return ExecutionResult(trace=ToolCallTrace(calls=calls), final_record=None, error=call.error)

            finished_at = datetime.now(UTC)
            latency_ms = _elapsed_ms(perf_start)

            if result.success:
                call = _record_call(
                    step=step,
                    args=resolved_args,
                    status=ToolCallStatus.SUCCESS,
                    result=result.data,
                    error=None,
                    validation_errors=None,
                    started_at=started_at,
                    finished_at=finished_at,
                    latency_ms=latency_ms,
                )
                calls.append(call)
                _log_call(call)
                continue

            # Category 2: controlled tool failure.
            call = _record_call(
                step=step,
                args=resolved_args,
                status=ToolCallStatus.ERROR,
                result=None,
                error=result.error,
                validation_errors=None,
                started_at=started_at,
                finished_at=finished_at,
                latency_ms=latency_ms,
            )
            calls.append(call)
            _log_call(call)
            return ExecutionResult(trace=ToolCallTrace(calls=calls), final_record=None, error=call.error)

        final_record = _assemble_final_record(calls)
        return ExecutionResult(trace=ToolCallTrace(calls=calls), final_record=final_record, error=None)
    finally:
        stage_ctx.reset(stage_token)
