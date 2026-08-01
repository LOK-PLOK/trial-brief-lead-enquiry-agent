"""Integration tests for agent/executor.py against the *real*
`ToolRegistry` and `Tool` base class (not a duck-typed fake -- see
tests/unit/test_executor.py for those), so the Executor's actual dispatch
through `ToolRegistry.get_tool()` and `Tool.execute()`'s validate -> run ->
validate pipeline is exercised end-to-end. Business logic is mocked: each
tool here is a "mock tool" per this task's requirement, standing in for the
still-`NotImplementedError`-raising real ones. See docs/contracts.md
sections 2 and 4.

`ToolName` is a closed enum, so a mock must be registered under one of the
four real tool names; `_isolated_tool_slate` temporarily removes the real
class for the duration of a test (avoiding `Tool`'s duplicate-name guard)
and restores it afterwards, leaving global state untouched for other test
modules.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from app.agent.executor import run_plan
from app.schemas.plan import Plan, PlanStep, ToolName
from app.schemas.tool_trace import ToolCallStatus
from app.tools.base import Tool, ToolResult
from app.tools.registry import ToolRegistry

ENQUIRY_TEXT = "Hi, I'm interested in whisky casks."

_CANONICAL_TOOL_NAMES = {tool.value for tool in ToolName}


class _PermissiveArgs(BaseModel):
    """Accepts any keyword args -- these integration tests exercise the
    Executor's orchestration, not each real tool's exact argument shape."""

    model_config = ConfigDict(extra="allow")


class _PermissiveResult(BaseModel):
    model_config = ConfigDict(extra="allow")


class _StrictArgs(BaseModel):
    """Has a genuinely required field, so tests can trigger a real
    `ToolValidationError` through `Tool.execute()`'s own validation."""

    required_field: str


@pytest.fixture
def isolated_tool_slate():
    """Removes the four real tool classes from `Tool._registry` so a test
    can register a mock under the same name without hitting the
    duplicate-name guard in `Tool.__init_subclass__`. Restores the real
    classes afterward, regardless of what the test registered -- other test
    modules never observe the swap."""
    saved = {name: Tool._registry.pop(name) for name in _CANONICAL_TOOL_NAMES if name in Tool._registry}
    yield
    for name in _CANONICAL_TOOL_NAMES:
        Tool._registry.pop(name, None)
    Tool._registry.update(saved)


def _mock_tool(
    tool_name: str,
    run_impl,
    *,
    args_schema: type[BaseModel] = _PermissiveArgs,
    result_schema: type[BaseModel] = _PermissiveResult,
) -> type[Tool]:
    """Dynamically defines (and, via `Tool.__init_subclass__`, registers) a
    `Tool` subclass named `tool_name` whose `run()` is `run_impl`."""
    return type(
        f"Mock_{tool_name}",
        (Tool,),
        {
            "name": tool_name,
            "description": f"mock {tool_name} for integration tests",
            "args_schema": args_schema,
            "result_schema": result_schema,
            "run": run_impl,
        },
    )


def _step(step: int, tool: ToolName, args: dict[str, Any] | None = None) -> PlanStep:
    return PlanStep(step=step, tool=tool, args=args or {}, rationale="integration test step")


def test_executor_dispatches_through_the_real_registry_and_records_success(isolated_tool_slate) -> None:
    _mock_tool(
        "parse_enquiry",
        lambda self, args: ToolResult(success=True, data={"name": "Jane"}, error=None, latency_ms=1.0),
    )
    registry = ToolRegistry()  # no adapter needed -- mock has no __init__ dependency
    plan = Plan(steps=[_step(1, ToolName.PARSE_ENQUIRY, {"enquiry_text": ENQUIRY_TEXT})])

    result = run_plan(plan, ENQUIRY_TEXT, registry)

    assert result.error is None
    assert result.trace.calls[0].status == ToolCallStatus.SUCCESS
    assert result.trace.calls[0].result == {"name": "Jane"}


def test_executor_surfaces_a_real_tool_validation_error_through_the_full_stack(isolated_tool_slate) -> None:
    """`_StrictArgs` requires `required_field`; omitting it must raise
    `ToolValidationError` from the real `Tool.execute()`, which the
    Executor must catch and convert -- not something a hand-rolled fake
    could verify."""
    _mock_tool(
        "score_lead",
        lambda self, args: pytest.fail("run() must not be called when args validation fails"),
        args_schema=_StrictArgs,
    )
    registry = ToolRegistry()
    plan = Plan(steps=[_step(1, ToolName.SCORE_LEAD, {"not_the_required_field": 1})])

    result = run_plan(plan, ENQUIRY_TEXT, registry)

    call = result.trace.calls[0]
    assert call.status == ToolCallStatus.ERROR
    assert call.validation_errors is not None
    assert any(err.get("loc") == ("required_field",) for err in call.validation_errors)


def test_executor_surfaces_a_malformed_successful_result_as_a_validation_error(isolated_tool_slate) -> None:
    """The real `Tool.execute()` also validates a *successful* result's
    `data` against `result_schema`; a buggy `run()` that returns the wrong
    shape must surface the same way as bad input."""

    class _StrictResult(BaseModel):
        score: int

    _mock_tool(
        "score_lead",
        lambda self, args: ToolResult(success=True, data={"wrong_field": 1}, error=None, latency_ms=1.0),
        result_schema=_StrictResult,
    )
    registry = ToolRegistry()
    plan = Plan(steps=[_step(1, ToolName.SCORE_LEAD)])

    result = run_plan(plan, ENQUIRY_TEXT, registry)

    assert result.trace.calls[0].status == ToolCallStatus.ERROR
    assert result.trace.calls[0].validation_errors is not None


def test_executor_handles_a_controlled_business_failure_from_a_real_tool(isolated_tool_slate) -> None:
    _mock_tool(
        "write_record",
        lambda self, args: ToolResult(success=False, data=None, error="duplicate lead", latency_ms=1.0),
    )
    registry = ToolRegistry()
    plan = Plan(steps=[_step(1, ToolName.WRITE_RECORD)])

    result = run_plan(plan, ENQUIRY_TEXT, registry)

    assert result.error == "duplicate lead"
    assert result.trace.calls[0].validation_errors is None


def test_executor_runs_real_parse_enquiry_with_scripted_adapter() -> None:
    """Integration: real `ParseEnquiryTool` + registry, scripted adapter.
    Confirms the improved prompt is what the tool sends, and extracted
    budget/urgency bands flow through the Executor unchanged (still LLM
    extraction only — no deterministic parsers)."""
    from app.agent.prompts.parse_enquiry_prompt import PARSE_ENQUIRY_SYSTEM_PROMPT
    from app.llm.base import ModelAdapter, StructuredCompletionRequest, StructuredCompletionResponse
    from app.schemas.extraction import BudgetBand, ExtractedFields, Urgency

    captured: list[StructuredCompletionRequest] = []

    class _ScriptedAdapter(ModelAdapter):
        provider_name = "fake"

        def complete_structured(self, request: StructuredCompletionRequest) -> StructuredCompletionResponse:
            captured.append(request)
            return StructuredCompletionResponse(
                parsed=ExtractedFields(
                    name="Noah Berger",
                    email="noah.berger@example.ca",
                    phone="+1 416 555 7721",
                    country="Canada",
                    budget_band=BudgetBand.MEDIUM,
                    asset_interest="mid-range cask",
                    urgency=Urgency.LOW,
                ),
                raw_response={},
                prompt_tokens=10,
                completion_tokens=5,
                latency_ms=1.0,
                model="fake-model",
            )

    enquiry = (
        "Hi — I'm Noah Berger from Toronto, Canada. "
        "Contact: noah.berger@example.ca / +1 416 555 7721.\n\n"
        "Interested in a mid-range cask, roughly CAD 25–40k, "
        "sometime in the next few months (not urgent)."
    )
    registry = ToolRegistry(adapter=_ScriptedAdapter())
    plan = Plan(steps=[_step(1, ToolName.PARSE_ENQUIRY, {"enquiry_text": enquiry})])

    result = run_plan(plan, enquiry, registry)

    assert result.error is None
    assert result.trace.calls[0].status == ToolCallStatus.SUCCESS
    assert result.trace.calls[0].result["budget_band"] == "medium"
    assert result.trace.calls[0].result["urgency"] == "low"
    assert len(captured) == 1
    assert captured[0].system_prompt == PARSE_ENQUIRY_SYSTEM_PROMPT
    assert (
        "budget_band guidance" in captured[0].system_prompt.lower()
        or "## budget_band" in captured[0].system_prompt
    )
    assert "CAD 25–40k" in captured[0].user_prompt


def test_executor_converts_unexpected_adapter_crash_to_structured_error() -> None:
    """Unexpected exceptions from the adapter (not LLMProviderError) still
    become a controlled Executor ERROR rather than crashing the process."""
    from app.llm.base import ModelAdapter, StructuredCompletionRequest, StructuredCompletionResponse

    class _FakeAdapter(ModelAdapter):
        provider_name = "fake"

        def complete_structured(self, request: StructuredCompletionRequest) -> StructuredCompletionResponse:
            raise NotImplementedError("adapter not configured")

    registry = ToolRegistry(adapter=_FakeAdapter())
    plan = Plan(steps=[_step(1, ToolName.PARSE_ENQUIRY, {"enquiry_text": ENQUIRY_TEXT})])

    result = run_plan(plan, ENQUIRY_TEXT, registry)

    assert result.error is not None
    assert "NotImplementedError" in result.error
    assert result.trace.calls[0].status == ToolCallStatus.ERROR


def test_executor_stops_and_never_looks_up_the_next_tool_after_a_failure(isolated_tool_slate) -> None:
    looked_up: list[str] = []
    real_get_tool = ToolRegistry.get_tool

    def _tracking_get_tool(self, name: str):
        looked_up.append(name)
        return real_get_tool(self, name)

    _mock_tool(
        "parse_enquiry",
        lambda self, args: ToolResult(success=False, data=None, error="broken enquiry", latency_ms=1.0),
    )
    _mock_tool("score_lead", lambda self, args: ToolResult(success=True, data={}, error=None, latency_ms=1.0))
    registry = ToolRegistry()
    plan = Plan(
        steps=[
            _step(1, ToolName.PARSE_ENQUIRY),
            _step(2, ToolName.SCORE_LEAD),
        ]
    )

    ToolRegistry.get_tool = _tracking_get_tool
    try:
        result = run_plan(plan, ENQUIRY_TEXT, registry)
    finally:
        ToolRegistry.get_tool = real_get_tool

    assert looked_up == ["parse_enquiry"]  # score_lead never even looked up
    assert len(result.trace.calls) == 1


def test_full_canonical_plan_with_mock_tools_assembles_a_final_record(isolated_tool_slate) -> None:
    from app.services.dedupe import compute_dedupe_hash

    _mock_tool(
        "parse_enquiry",
        lambda self, args: ToolResult(
            success=True, data={"name": "Jane Doe", "email": "jane@example.com"}, error=None, latency_ms=1.0
        ),
    )
    _mock_tool(
        "lookup_jurisdiction_rule",
        lambda self, args: ToolResult(
            success=True,
            data={
                "country": "Singapore",
                "requires_disclaimer": False,
                "restricted": False,
                "handling_note": "standard",
            },
            error=None,
            latency_ms=1.0,
        ),
    )
    _mock_tool(
        "score_lead",
        lambda self, args: ToolResult(
            success=True, data={"score": 90, "breakdown": {"budget": 90}}, error=None, latency_ms=1.0
        ),
    )
    registry = ToolRegistry()
    plan = Plan(
        steps=[
            _step(1, ToolName.PARSE_ENQUIRY, {"enquiry_text": ENQUIRY_TEXT}),
            _step(2, ToolName.LOOKUP_JURISDICTION_RULE, {"country": "Singapore"}),
            _step(3, ToolName.SCORE_LEAD),
        ]
    )

    result = run_plan(plan, ENQUIRY_TEXT, registry)

    assert result.error is None
    assert result.final_record is not None
    assert result.final_record.extracted.name == "Jane Doe"
    assert result.final_record.score == 90
    assert result.final_record.dedupe_hash == compute_dedupe_hash("jane@example.com", None)
    assert len(result.trace.calls) == 3
    assert all(c.tool != ToolName.WRITE_RECORD for c in result.trace.calls)


def test_isolated_tool_slate_restores_the_real_tool_after_teardown() -> None:
    """Runs without the fixture, after every other test in this module has
    used (and torn down) `isolated_tool_slate` -- confirms none of the
    mocks registered above leaked into global state."""
    from app.tools.parse_enquiry import ParseEnquiryTool

    assert Tool.registered_classes()["parse_enquiry"] is ParseEnquiryTool
    assert _CANONICAL_TOOL_NAMES <= Tool.registered_classes().keys()
