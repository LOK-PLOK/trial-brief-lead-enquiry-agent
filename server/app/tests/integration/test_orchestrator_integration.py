"""Integration tests for agent/orchestrator.py: the full Planner -> Executor
-> Verifier pipeline exercised together through the real `Pipeline.run()`,
using one fake `ModelAdapter` (serving both the Planner's and the Verifier's
calls, distinguished by `request.response_schema`, exactly like a real
provider would) and mock tools registered under the real `ToolRegistry`/
`Tool` base class. See docs/contracts.md sections 1-3 and
tests/unit/test_orchestrator.py for the Orchestrator's own logic tested in
isolation with monkeypatched stages.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.agent.orchestrator import Pipeline
from app.core.config import Settings
from app.db import repository
from app.llm.base import ModelAdapter, StructuredCompletionRequest, StructuredCompletionResponse
from app.schemas.extraction import ExtractedFields
from app.schemas.plan import Plan, PlanStep, ToolName
from app.schemas.verifier import VerifierDecision
from app.tools.base import Tool, ToolResult

ENQUIRY_TEXT = "Hi, I'm Jane Doe (jane@example.com), interested in whisky casks in Singapore."

_CANONICAL_TOOL_NAMES = {tool.value for tool in ToolName}


class _PermissiveArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


class _PermissiveResult(BaseModel):
    model_config = ConfigDict(extra="allow")


def _mock_tool(tool_name: str, run_impl) -> type[Tool]:
    return type(
        f"Mock_{tool_name}",
        (Tool,),
        {
            "name": tool_name,
            "description": f"mock {tool_name} for orchestrator integration tests",
            "args_schema": _PermissiveArgs,
            "result_schema": _PermissiveResult,
            "run": run_impl,
        },
    )


def _register_canonical_mock_tools() -> None:
    """Registers a full, successful set of mock tools for all four
    canonical tools, so a plan produced by the fake Planner adapter can run
    to completion and assemble a `final_record`."""
    _mock_tool(
        "parse_enquiry",
        lambda self, args: ToolResult(
            success=True,
            data={
                "name": "Jane Doe",
                "email": "jane@example.com",
                "asset_interest": "Whisky cask",
            },
            error=None,
            latency_ms=1.0,
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
            success=True, data={"score": 80, "breakdown": {"budget": 80}}, error=None, latency_ms=1.0
        ),
    )
    _mock_tool(
        "write_record",
        lambda self, args: ToolResult(
            success=True, data={"lead_id": "lead-1", "dedupe_hash": "hash-1"}, error=None, latency_ms=1.0
        ),
    )


def _isolate_tool_slate():
    """Removes the four real tool classes from `Tool._registry`, returning
    the saved mapping so the caller can restore it. Mirrors
    tests/integration/test_executor_integration.py's fixture, duplicated
    here (rather than imported) to keep each integration test module
    self-contained, matching this codebase's existing convention (see
    tests/integration/test_verifier_integration.py)."""
    return {name: Tool._registry.pop(name) for name in _CANONICAL_TOOL_NAMES if name in Tool._registry}


def _restore_tool_slate(saved: dict) -> None:
    for name in _CANONICAL_TOOL_NAMES:
        Tool._registry.pop(name, None)
    Tool._registry.update(saved)


def _canonical_plan() -> Plan:
    return Plan(
        steps=[
            PlanStep(
                step=1,
                tool=ToolName.PARSE_ENQUIRY,
                args={"enquiry_text": ENQUIRY_TEXT},
                rationale="Extract structured fields from the enquiry.",
            ),
            PlanStep(
                step=2,
                tool=ToolName.LOOKUP_JURISDICTION_RULE,
                args={"country": "Singapore"},
                rationale="Look up the jurisdiction's handling rule.",
            ),
            PlanStep(step=3, tool=ToolName.SCORE_LEAD, args={}, rationale="Score the lead."),
            PlanStep(step=4, tool=ToolName.WRITE_RECORD, args={}, rationale="Persist the lead."),
        ]
    )


def _passing_decision() -> VerifierDecision:
    return VerifierDecision(
        passed=True,
        confidence=0.95,
        fabrication_detected=False,
        fabricated_fields=[],
        plan_deviation_detected=False,
        deviation_details=None,
        reason="Every field verified against the enquiry text; trace matches the plan.",
    )


def _failing_decision(reason: str = "Fabricated phone number.") -> VerifierDecision:
    return VerifierDecision(
        passed=False,
        confidence=0.8,
        fabrication_detected=True,
        fabricated_fields=["phone"],
        plan_deviation_detected=False,
        deviation_details=None,
        reason=reason,
    )


class _FullPipelineFakeAdapter(ModelAdapter):
    """Serves both the Planner's and the Verifier's calls from one fake
    provider, exactly as a real adapter would -- dispatches purely on
    `request.response_schema`, never on which module happens to be
    calling, since a real provider has no notion of "Planner" vs
    "Verifier" beyond the schema it's asked to fill."""

    provider_name = "fake"

    def __init__(self, *, plan: Plan, decision: VerifierDecision) -> None:
        self._plan = plan
        self._decision = decision
        self.requests: list[StructuredCompletionRequest] = []

    def complete_structured(self, request: StructuredCompletionRequest) -> StructuredCompletionResponse:
        self.requests.append(request)
        if request.response_schema is Plan:
            parsed: BaseModel = self._plan
            model = "fake-planner-model"
        elif request.response_schema is VerifierDecision:
            parsed = self._decision
            model = "fake-verifier-model"
        elif request.response_schema is ExtractedFields:
            # Served during the Repair Loop's `reextract` strategy.
            parsed = ExtractedFields(
                name="Jane Doe",
                email="jane@example.com",
                phone=None,
                country="Singapore",
                asset_interest="Whisky cask",
            )
            model = "fake-extractor-model"
        else:
            raise AssertionError(f"unexpected response_schema: {request.response_schema}")
        return StructuredCompletionResponse(
            parsed=parsed,
            raw_response={"id": "fake"},
            prompt_tokens=100,
            completion_tokens=25,
            latency_ms=15.0,
            model=model,
        )

    @property
    def planner_requests(self) -> list[StructuredCompletionRequest]:
        return [r for r in self.requests if r.response_schema is Plan]

    @property
    def verifier_requests(self) -> list[StructuredCompletionRequest]:
        return [r for r in self.requests if r.response_schema is VerifierDecision]


class TestFullPipelineHappyPath:
    def test_planner_executor_verifier_all_run_and_result_is_completed(self, db_session: Session) -> None:
        saved = _isolate_tool_slate()
        try:
            _register_canonical_mock_tools()
            adapter = _FullPipelineFakeAdapter(plan=_canonical_plan(), decision=_passing_decision())
            pipeline = Pipeline(adapter=adapter, settings=Settings())

            result = pipeline.run(ENQUIRY_TEXT, db=db_session)

            assert result.final_status == "completed"
            assert result.plan is not None
            assert result.plan_schema_valid is True
            assert result.tool_call_trace is not None
            assert len(result.tool_call_trace.calls) == 4
            assert result.final_record is not None
            from app.services.dedupe import compute_dedupe_hash

            assert result.final_record.dedupe_hash == compute_dedupe_hash("jane@example.com", None)
            assert result.tool_call_trace.calls[-1].tool == ToolName.WRITE_RECORD
            assert result.verifier_decision is not None
            assert result.verifier_decision.passed is True
            assert len(adapter.planner_requests) == 1
            assert len(adapter.verifier_requests) == 1
        finally:
            _restore_tool_slate(saved)

    def test_llm_usage_from_both_stages_is_aggregated_onto_the_result(self, db_session: Session) -> None:
        saved = _isolate_tool_slate()
        try:
            _register_canonical_mock_tools()
            adapter = _FullPipelineFakeAdapter(plan=_canonical_plan(), decision=_passing_decision())
            pipeline = Pipeline(adapter=adapter, settings=Settings())

            result = pipeline.run(ENQUIRY_TEXT, db=db_session)

            assert len(result.llm_calls) == 2
            assert {c.stage for c in result.llm_calls} == {"planner", "verifier"}
            assert result.total_tokens == 2 * (100 + 25)
            assert result.total_latency_ms == 2 * 15.0
        finally:
            _restore_tool_slate(saved)

    def test_result_is_actually_persisted_and_retrievable_via_the_repository(
        self, db_session: Session
    ) -> None:
        saved = _isolate_tool_slate()
        try:
            _register_canonical_mock_tools()
            adapter = _FullPipelineFakeAdapter(plan=_canonical_plan(), decision=_passing_decision())
            pipeline = Pipeline(adapter=adapter, settings=Settings())

            result = pipeline.run(ENQUIRY_TEXT, db=db_session)

            row = repository.get_run(db_session, result.id)
            assert row is not None
            assert row.final_status == "completed"
            assert row.verifier_pass is True
            assert row.enquiry_text == ENQUIRY_TEXT
            assert len(row.llm_calls) == 2
        finally:
            _restore_tool_slate(saved)


class TestFullPipelineVerifierFailure:
    def test_verifier_fail_attempts_repair_then_quarantines_when_still_failing(
        self, db_session: Session
    ) -> None:
        saved = _isolate_tool_slate()
        try:
            _register_canonical_mock_tools()
            # Fake adapter always returns the same failing VerifierDecision, so
            # the post-repair re-verify also fails — exercising the
            # "one repair then quarantine" path (docs/architecture.md §10).
            adapter = _FullPipelineFakeAdapter(plan=_canonical_plan(), decision=_failing_decision())
            pipeline = Pipeline(adapter=adapter, settings=Settings())

            result = pipeline.run(ENQUIRY_TEXT, db=db_session)

            assert result.final_status == "quarantined"
            assert result.repair_attempted is True
            assert result.repair_succeeded is False
            assert result.verifier_decision.fabrication_detected is True
            # Initial verify + post-repair verify (fresh, independent call).
            assert len(adapter.verifier_requests) == 2

            row = repository.get_run(db_session, result.id)
            assert row.final_status == "quarantined"
            assert row.fabrication_detected is True
            assert row.fabricated_fields == ["phone"]
            assert row.repair_attempted is True
            assert row.repair_succeeded is False
        finally:
            _restore_tool_slate(saved)


class TestFullPipelineExecutorFailure:
    def test_a_controlled_tool_failure_stops_before_the_verifier_and_reports_error(
        self, db_session: Session
    ) -> None:
        saved = _isolate_tool_slate()
        try:
            _mock_tool(
                "parse_enquiry",
                lambda self, args: ToolResult(
                    success=False, data=None, error="could not parse enquiry", latency_ms=1.0
                ),
            )
            _mock_tool(
                "lookup_jurisdiction_rule",
                lambda self, args: ToolResult(success=True, data={}, error=None, latency_ms=1.0),
            )
            _mock_tool(
                "score_lead", lambda self, args: ToolResult(success=True, data={}, error=None, latency_ms=1.0)
            )
            _mock_tool(
                "write_record",
                lambda self, args: ToolResult(success=True, data={}, error=None, latency_ms=1.0),
            )
            adapter = _FullPipelineFakeAdapter(plan=_canonical_plan(), decision=_passing_decision())
            pipeline = Pipeline(adapter=adapter, settings=Settings())

            result = pipeline.run(ENQUIRY_TEXT, db=db_session)

            assert result.final_status == "error"
            assert result.final_record is None
            assert result.verifier_decision is None
            assert len(result.tool_call_trace.calls) == 1  # stopped after the first failure
            # The Verifier must never even be called when there's no final
            # record for it to judge -- confirmed via the fake adapter,
            # which would otherwise have recorded a second (Verifier) call.
            assert len(adapter.verifier_requests) == 0

            row = repository.get_run(db_session, result.id)
            assert row.final_status == "error"
            assert row.verifier_pass is None
        finally:
            _restore_tool_slate(saved)


class TestFullPipelinePlannerFailure:
    def test_a_plan_that_never_satisfies_the_guardrail_produces_a_structured_error_result(
        self, db_session: Session
    ) -> None:
        """The fake adapter always returns the *same* invalid plan (missing
        the mandatory `score_lead`/`lookup_jurisdiction_rule` steps), so the
        Planner's one guardrail retry is also exhausted -- `build_plan()`
        raises `PlannerValidationError`, which the Orchestrator must catch."""
        saved = _isolate_tool_slate()
        try:
            invalid_plan = Plan(
                steps=[
                    PlanStep(
                        step=1,
                        tool=ToolName.PARSE_ENQUIRY,
                        args={},
                        rationale="Only step, missing mandatory tools.",
                    )
                ]
            )
            adapter = _FullPipelineFakeAdapter(plan=invalid_plan, decision=_passing_decision())
            pipeline = Pipeline(adapter=adapter, settings=Settings())

            result = pipeline.run(ENQUIRY_TEXT, db=db_session)

            assert result.final_status == "error"
            assert result.plan is None
            assert result.plan_schema_valid is False
            assert result.tool_call_trace is None
            assert result.final_record is None
            assert result.verifier_decision is None
            # One initial attempt + one guardrail retry, per the Planner's
            # own contract -- both should have gone through the adapter.
            assert len(adapter.planner_requests) == 2
            assert len(adapter.verifier_requests) == 0

            row = repository.get_run(db_session, result.id)
            assert row.final_status == "error"
            assert row.plan is None
        finally:
            _restore_tool_slate(saved)


class TestFullPipelineRunIdCorrelation:
    def test_the_same_run_id_is_used_for_the_result_and_the_persisted_row(self, db_session: Session) -> None:
        saved = _isolate_tool_slate()
        try:
            _register_canonical_mock_tools()
            adapter = _FullPipelineFakeAdapter(plan=_canonical_plan(), decision=_passing_decision())
            pipeline = Pipeline(adapter=adapter, settings=Settings())

            result = pipeline.run(ENQUIRY_TEXT, enquiry_id="enquiry-42", repeat_index=1, db=db_session)

            row = repository.get_run(db_session, result.id)
            assert row is not None
            assert row.id == result.id
            assert row.enquiry_id == "enquiry-42"
            assert row.repeat_index == 1
        finally:
            _restore_tool_slate(saved)


def test_registry_isolation_left_no_mocks_behind() -> None:
    """Runs last alphabetically-adjacent to confirm no test above leaked a
    mock tool registration into global state for other test modules."""
    from app.tools.parse_enquiry import ParseEnquiryTool

    assert Tool.registered_classes()["parse_enquiry"] is ParseEnquiryTool
    assert _CANONICAL_TOOL_NAMES <= Tool.registered_classes().keys()
