"""Unit tests for agent/executor.py, using a duck-typed fake registry/tool
(not the real `ToolRegistry`/`Tool` machinery -- see
tests/integration/test_executor_integration.py for that) so `run_plan()`'s
own control-flow logic is tested in full isolation: ordering, stop-on-
failure, the three failure categories, trace preservation, and final-record
assembly. See docs/contracts.md section 2.
"""

from __future__ import annotations

import inspect
from datetime import datetime
from pathlib import Path

import pytest

from app.agent import executor as executor_module
from app.agent.executor import ExecutionResult, run_plan
from app.core.logging import stage_ctx
from app.schemas.plan import Plan, PlanStep, ToolName
from app.schemas.tool_trace import ToolCallStatus
from app.tools.base import ToolResult, ToolValidationError

ENQUIRY_TEXT = "Hi, I'm interested in whisky casks. My email is jane@example.com."


class _FakeTool:
    """A duck-typed stand-in for a real `Tool` instance: `run_plan()` only
    ever calls `.execute(args)` on whatever `registry.get_tool()` returns,
    so a fake needs nothing more than that one method."""

    def __init__(self, outcome: ToolResult | Exception, *, calls_log: list | None = None) -> None:
        self._outcome = outcome
        self._calls_log = calls_log

    def execute(self, args: dict) -> ToolResult:
        if self._calls_log is not None:
            self._calls_log.append(args)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _FakeRegistry:
    """Duck-typed stand-in for `ToolRegistry`: `run_plan()` only calls
    `.get_tool(name)`."""

    def __init__(self, tools: dict[str, _FakeTool]) -> None:
        self._tools = tools

    def get_tool(self, name: str):
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name!r}") from exc


def _success(data: dict, latency_ms: float = 1.0) -> ToolResult:
    return ToolResult(success=True, data=data, error=None, latency_ms=latency_ms)


def _business_failure(error: str, latency_ms: float = 1.0) -> ToolResult:
    return ToolResult(success=False, data=None, error=error, latency_ms=latency_ms)


def _step(step: int, tool: ToolName, args: dict | None = None) -> PlanStep:
    return PlanStep(step=step, tool=tool, args=args or {}, rationale="test step")


def _plan(*steps: PlanStep) -> Plan:
    return Plan(steps=list(steps))


class TestHappyPath:
    def test_returns_a_complete_trace_with_one_call_per_step(self) -> None:
        plan = _plan(
            _step(1, ToolName.PARSE_ENQUIRY, {"enquiry_text": ENQUIRY_TEXT}),
            _step(2, ToolName.LOOKUP_JURISDICTION_RULE, {"country": "Singapore"}),
        )
        registry = _FakeRegistry(
            {
                "parse_enquiry": _FakeTool(_success({"name": "Jane"})),
                "lookup_jurisdiction_rule": _FakeTool(_success({"country": "Singapore"})),
            }
        )

        result = run_plan(plan, ENQUIRY_TEXT, registry)

        assert isinstance(result, ExecutionResult)
        assert result.error is None
        assert len(result.trace.calls) == 2
        assert [c.status for c in result.trace.calls] == [ToolCallStatus.SUCCESS] * 2

    def test_executes_steps_strictly_in_plan_order_not_reordered(self) -> None:
        call_order: list[str] = []

        class _OrderTrackingTool(_FakeTool):
            def __init__(self, name: str, outcome: ToolResult) -> None:
                super().__init__(outcome)
                self._name = name

            def execute(self, args: dict) -> ToolResult:
                call_order.append(self._name)
                return super().execute(args)

        plan = _plan(
            _step(1, ToolName.WRITE_RECORD),
            _step(2, ToolName.PARSE_ENQUIRY),
            _step(3, ToolName.SCORE_LEAD),
        )
        write_record_result = _success({"lead_id": "1", "dedupe_hash": "h"})
        registry = _FakeRegistry(
            {
                "write_record": _OrderTrackingTool("write_record", write_record_result),
                "parse_enquiry": _OrderTrackingTool("parse_enquiry", _success({"name": "Jane"})),
                "score_lead": _OrderTrackingTool("score_lead", _success({"score": 10, "breakdown": {}})),
            }
        )

        run_plan(plan, ENQUIRY_TEXT, registry)

        assert call_order == ["write_record", "parse_enquiry", "score_lead"]

    def test_trace_preserves_the_exact_step_number_and_tool_and_args(self) -> None:
        plan = _plan(_step(1, ToolName.LOOKUP_JURISDICTION_RULE, {"country": "Singapore"}))
        registry = _FakeRegistry({"lookup_jurisdiction_rule": _FakeTool(_success({"country": "Singapore"}))})

        result = run_plan(plan, ENQUIRY_TEXT, registry)

        call = result.trace.calls[0]
        assert call.step == 1
        assert call.tool == ToolName.LOOKUP_JURISDICTION_RULE
        assert call.args == {"country": "Singapore"}

    def test_records_output_data_on_success(self) -> None:
        plan = _plan(_step(1, ToolName.SCORE_LEAD))
        score_result = _success({"score": 42, "breakdown": {"budget": 42}})
        registry = _FakeRegistry({"score_lead": _FakeTool(score_result)})

        result = run_plan(plan, ENQUIRY_TEXT, registry)

        call = result.trace.calls[0]
        assert call.status == ToolCallStatus.SUCCESS
        assert call.result == {"score": 42, "breakdown": {"budget": 42}}
        assert call.error is None
        assert call.validation_errors is None

    def test_uses_the_tool_registry_for_dispatch_not_direct_import(self) -> None:
        """The registry is consulted by tool name for every single step --
        this is what "use ToolRegistry for all tool lookup" means in
        practice."""
        seen_lookups: list[str] = []

        class _RecordingRegistry(_FakeRegistry):
            def get_tool(self, name: str):
                seen_lookups.append(name)
                return super().get_tool(name)

        plan = _plan(_step(1, ToolName.PARSE_ENQUIRY), _step(2, ToolName.SCORE_LEAD))
        registry = _RecordingRegistry(
            {
                "parse_enquiry": _FakeTool(_success({})),
                "score_lead": _FakeTool(_success({"score": 1, "breakdown": {}})),
            }
        )

        run_plan(plan, ENQUIRY_TEXT, registry)

        assert seen_lookups == ["parse_enquiry", "score_lead"]


class TestRecordFields:
    def test_records_start_time_end_time_and_latency(self) -> None:
        plan = _plan(_step(1, ToolName.SCORE_LEAD))
        registry = _FakeRegistry({"score_lead": _FakeTool(_success({"score": 1, "breakdown": {}}))})

        result = run_plan(plan, ENQUIRY_TEXT, registry)

        call = result.trace.calls[0]
        assert isinstance(call.started_at, datetime)
        assert isinstance(call.finished_at, datetime)
        assert call.finished_at >= call.started_at
        assert call.latency_ms >= 0.0

    def test_records_input_and_output(self) -> None:
        plan = _plan(_step(1, ToolName.LOOKUP_JURISDICTION_RULE, {"country": "Vietnam"}))
        registry = _FakeRegistry(
            {"lookup_jurisdiction_rule": _FakeTool(_success({"country": "Vietnam", "restricted": False}))}
        )

        result = run_plan(plan, ENQUIRY_TEXT, registry)

        call = result.trace.calls[0]
        assert call.args == {"country": "Vietnam"}
        assert call.result == {"country": "Vietnam", "restricted": False}


class TestFailureCategoryValidation:
    def test_tool_validation_failure_is_recorded_with_structured_errors(self) -> None:
        exc = ToolValidationError("parse_enquiry", [{"loc": ("enquiry_text",), "msg": "field required"}])
        plan = _plan(_step(1, ToolName.PARSE_ENQUIRY, {}))
        registry = _FakeRegistry({"parse_enquiry": _FakeTool(exc)})

        result = run_plan(plan, ENQUIRY_TEXT, registry)

        call = result.trace.calls[0]
        assert call.status == ToolCallStatus.ERROR
        assert call.validation_errors == [{"loc": ("enquiry_text",), "msg": "field required"}]
        assert "parse_enquiry" in call.error
        assert result.error == call.error

    def test_execution_stops_after_a_validation_failure(self) -> None:
        exc = ToolValidationError("parse_enquiry", [{"msg": "bad"}])
        plan = _plan(
            _step(1, ToolName.PARSE_ENQUIRY),
            _step(2, ToolName.SCORE_LEAD),
        )
        never_called = _FakeTool(_success({"score": 1, "breakdown": {}}))
        registry = _FakeRegistry({"parse_enquiry": _FakeTool(exc), "score_lead": never_called})

        result = run_plan(plan, ENQUIRY_TEXT, registry)

        assert len(result.trace.calls) == 1  # step 2 never attempted
        assert result.final_record is None


class TestFailureCategoryControlledFailure:
    def test_controlled_failure_is_recorded_without_validation_errors(self) -> None:
        plan = _plan(_step(1, ToolName.WRITE_RECORD))
        registry = _FakeRegistry({"write_record": _FakeTool(_business_failure("duplicate lead"))})

        result = run_plan(plan, ENQUIRY_TEXT, registry)

        call = result.trace.calls[0]
        assert call.status == ToolCallStatus.ERROR
        assert call.error == "duplicate lead"
        assert call.validation_errors is None
        assert result.error == "duplicate lead"

    def test_execution_stops_after_a_controlled_failure(self) -> None:
        plan = _plan(
            _step(1, ToolName.WRITE_RECORD),
            _step(2, ToolName.SCORE_LEAD),
        )
        registry = _FakeRegistry(
            {
                "write_record": _FakeTool(_business_failure("duplicate lead")),
                "score_lead": _FakeTool(_success({"score": 1, "breakdown": {}})),
            }
        )

        result = run_plan(plan, ENQUIRY_TEXT, registry)

        assert len(result.trace.calls) == 1
        assert result.final_record is None


class TestFailureCategoryUnexpectedException:
    def test_unexpected_exception_is_converted_to_a_structured_failure(self) -> None:
        plan = _plan(_step(1, ToolName.PARSE_ENQUIRY))
        registry = _FakeRegistry({"parse_enquiry": _FakeTool(NotImplementedError("no business logic yet"))})

        result = run_plan(plan, ENQUIRY_TEXT, registry)

        assert isinstance(result, ExecutionResult)  # did not raise / crash
        call = result.trace.calls[0]
        assert call.status == ToolCallStatus.ERROR
        assert "NotImplementedError" in call.error
        assert "no business logic yet" in call.error
        assert call.validation_errors is None

    def test_does_not_propagate_the_original_exception_type(self) -> None:
        """The whole point: run_plan() must not itself raise whatever the
        tool raised."""

        class _WeirdBug(RuntimeError):
            pass

        plan = _plan(_step(1, ToolName.SCORE_LEAD))
        registry = _FakeRegistry({"score_lead": _FakeTool(_WeirdBug("kaboom"))})

        result = run_plan(plan, ENQUIRY_TEXT, registry)  # must not raise

        assert result.error is not None
        assert "kaboom" in result.error

    def test_unknown_tool_name_is_converted_to_a_structured_failure(self) -> None:
        """Defensive: `ToolName` is a closed enum so this should never
        happen in practice, but a registry/tool-registration mismatch must
        not crash the pipeline either."""
        plan = _plan(_step(1, ToolName.WRITE_RECORD))
        registry = _FakeRegistry({})  # nothing registered

        result = run_plan(plan, ENQUIRY_TEXT, registry)

        call = result.trace.calls[0]
        assert call.status == ToolCallStatus.ERROR
        assert "write_record" in call.error
        assert result.final_record is None

    def test_execution_stops_after_an_unexpected_exception(self) -> None:
        plan = _plan(
            _step(1, ToolName.PARSE_ENQUIRY),
            _step(2, ToolName.SCORE_LEAD),
        )
        registry = _FakeRegistry(
            {
                "parse_enquiry": _FakeTool(RuntimeError("boom")),
                "score_lead": _FakeTool(_success({"score": 1, "breakdown": {}})),
            }
        )

        result = run_plan(plan, ENQUIRY_TEXT, registry)

        assert len(result.trace.calls) == 1


class TestTracePreservedOnEarlyStop:
    def test_trace_includes_successful_steps_before_the_failing_one(self) -> None:
        plan = _plan(
            _step(1, ToolName.PARSE_ENQUIRY),
            _step(2, ToolName.LOOKUP_JURISDICTION_RULE),
            _step(3, ToolName.SCORE_LEAD),
        )
        registry = _FakeRegistry(
            {
                "parse_enquiry": _FakeTool(_success({"name": "Jane"})),
                "lookup_jurisdiction_rule": _FakeTool(_business_failure("unknown jurisdiction")),
                "score_lead": _FakeTool(_success({"score": 1, "breakdown": {}})),
            }
        )

        result = run_plan(plan, ENQUIRY_TEXT, registry)

        assert len(result.trace.calls) == 2  # step 1 (success) + step 2 (failure); step 3 never ran
        assert result.trace.calls[0].status == ToolCallStatus.SUCCESS
        assert result.trace.calls[1].status == ToolCallStatus.ERROR
        assert result.final_record is None
        assert result.error == "unknown jurisdiction"


class TestFinalRecordAssembly:
    FULL_PLAN = _plan(
        _step(1, ToolName.PARSE_ENQUIRY),
        _step(2, ToolName.LOOKUP_JURISDICTION_RULE, {"country": "Singapore"}),
        _step(3, ToolName.SCORE_LEAD),
        _step(4, ToolName.WRITE_RECORD),
    )

    def _full_registry(self) -> _FakeRegistry:
        return _FakeRegistry(
            {
                "parse_enquiry": _FakeTool(_success({"name": "Jane Doe", "email": "jane@example.com"})),
                "lookup_jurisdiction_rule": _FakeTool(
                    _success(
                        {
                            "country": "Singapore",
                            "requires_disclaimer": True,
                            "restricted": False,
                            "handling_note": "standard",
                        }
                    )
                ),
                "score_lead": _FakeTool(_success({"score": 78, "breakdown": {"budget": 40}})),
                "write_record": _FakeTool(_success({"lead_id": "lead-1", "dedupe_hash": "abc123"})),
            }
        )

    def test_assembles_final_record_when_all_four_canonical_tools_succeed(self) -> None:
        result = run_plan(self.FULL_PLAN, ENQUIRY_TEXT, self._full_registry())

        assert result.error is None
        assert result.final_record is not None
        assert result.final_record.extracted.name == "Jane Doe"
        assert result.final_record.jurisdiction_rule["country"] == "Singapore"
        assert result.final_record.score == 78
        assert result.final_record.score_breakdown == {"budget": 40}
        assert result.final_record.dedupe_hash == "abc123"

    def test_final_record_is_none_when_a_canonical_tool_is_missing_from_the_plan(self) -> None:
        plan = _plan(
            _step(1, ToolName.PARSE_ENQUIRY),
            _step(2, ToolName.LOOKUP_JURISDICTION_RULE),
            _step(3, ToolName.SCORE_LEAD),
            # no write_record step
        )
        registry = _FakeRegistry(
            {
                "parse_enquiry": _FakeTool(_success({})),
                "lookup_jurisdiction_rule": _FakeTool(_success({})),
                "score_lead": _FakeTool(_success({"score": 1, "breakdown": {}})),
            }
        )

        result = run_plan(plan, ENQUIRY_TEXT, registry)

        assert result.error is None  # every attempted step succeeded
        assert result.final_record is None  # but the record can't be assembled

    def test_final_record_is_none_when_execution_stopped_early(self) -> None:
        plan = _plan(
            _step(1, ToolName.PARSE_ENQUIRY),
            _step(2, ToolName.LOOKUP_JURISDICTION_RULE),
        )
        registry = _FakeRegistry(
            {
                "parse_enquiry": _FakeTool(_success({})),
                "lookup_jurisdiction_rule": _FakeTool(_business_failure("nope")),
            }
        )

        result = run_plan(plan, ENQUIRY_TEXT, registry)

        assert result.final_record is None


class TestStageContext:
    def test_sets_and_resets_stage_context(self) -> None:
        captured: list[str | None] = []

        class _CapturingTool(_FakeTool):
            def execute(self, args: dict) -> ToolResult:
                captured.append(stage_ctx.get())
                return super().execute(args)

        plan = _plan(_step(1, ToolName.SCORE_LEAD))
        registry = _FakeRegistry({"score_lead": _CapturingTool(_success({"score": 1, "breakdown": {}}))})

        assert stage_ctx.get() is None
        run_plan(plan, ENQUIRY_TEXT, registry)
        assert captured == ["executor"]
        assert stage_ctx.get() is None

    def test_resets_stage_context_even_on_unexpected_exception(self) -> None:
        plan = _plan(_step(1, ToolName.SCORE_LEAD))
        registry = _FakeRegistry({"score_lead": _FakeTool(RuntimeError("boom"))})

        assert stage_ctx.get() is None
        run_plan(plan, ENQUIRY_TEXT, registry)  # handled internally, doesn't raise
        assert stage_ctx.get() is None


class TestEmptyPlan:
    def test_empty_plan_returns_empty_trace_and_no_final_record(self) -> None:
        result = run_plan(_plan(), ENQUIRY_TEXT, _FakeRegistry({}))
        assert result.trace.calls == []
        assert result.final_record is None
        assert result.error is None


class TestExecutorNeverImportsToolClasses:
    """Structural guardrail: requirement 6 ("never reference tool classes
    directly") and development-rules.md's determinism/isolation intent."""

    def test_executor_module_does_not_import_concrete_tool_classes(self) -> None:
        source = Path(inspect.getfile(executor_module)).read_text()
        forbidden = [
            "ParseEnquiryTool",
            "LookupJurisdictionRuleTool",
            "ScoreLeadTool",
            "WriteRecordTool",
            "from app.tools.parse_enquiry",
            "from app.tools.lookup_jurisdiction_rule",
            "from app.tools.score_lead",
            "from app.tools.write_record",
        ]
        for token in forbidden:
            assert token not in source

    def test_executor_module_has_no_llm_imports(self) -> None:
        source = Path(inspect.getfile(executor_module)).read_text()
        assert "app.llm" not in source

    @pytest.mark.parametrize("forbidden_module", ["app.agent.repair", "app.agent.verifier"])
    def test_executor_module_does_not_import_repair_or_verifier(self, forbidden_module: str) -> None:
        source = Path(inspect.getfile(executor_module)).read_text()
        assert forbidden_module not in source
