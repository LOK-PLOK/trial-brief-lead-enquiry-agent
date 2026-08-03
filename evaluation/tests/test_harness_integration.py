"""Integration test for evaluation/harness.py: drives the real `Pipeline`
(real Planner/Executor/Verifier wiring, real `ToolRegistry`) through
`run_harness()`, using a fake `ModelAdapter` and mock tools -- the same
general pattern as
server/app/tests/integration/test_orchestrator_integration.py, duplicated
rather than imported to keep this module self-contained (this project's
existing convention for integration test modules).

Confirms the harness's own promises hold end-to-end: exactly N runs land in
the database linked to the batch, `compute_harness_metrics()`'s numbers
match what was actually persisted, and both report files are written.
"""

from __future__ import annotations

from app.agent.orchestrator import Pipeline
from app.core.config import Settings
from app.db import repository
from app.llm.base import ModelAdapter, StructuredCompletionRequest, StructuredCompletionResponse
from app.schemas.plan import Plan, PlanStep, ToolName
from app.schemas.verifier import VerifierDecision
from app.tools.base import Tool, ToolResult
from pydantic import BaseModel, ConfigDict

from evaluation import harness as harness_module

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
            "description": f"mock {tool_name} for harness integration tests",
            "args_schema": _PermissiveArgs,
            "result_schema": _PermissiveResult,
            "run": run_impl,
        },
    )


def _register_canonical_mock_tools() -> None:
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
    return {name: Tool._registry.pop(name) for name in _CANONICAL_TOOL_NAMES if name in Tool._registry}


def _restore_tool_slate(saved: dict) -> None:
    for name in _CANONICAL_TOOL_NAMES:
        Tool._registry.pop(name, None)
    Tool._registry.update(saved)


def _canonical_plan(enquiry_text: str) -> Plan:
    return Plan(
        steps=[
            PlanStep(
                step=1,
                tool=ToolName.PARSE_ENQUIRY,
                args={"enquiry_text": enquiry_text},
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


class _HarnessFakeAdapter(ModelAdapter):
    """Always returns a valid plan (matching whatever enquiry text it's
    asked about) and a passing verifier decision -- enough for every run in
    the harness to reach `final_status="completed"`, so the metrics being
    tested have a non-trivial denominator."""

    provider_name = "fake"

    def __init__(self) -> None:
        self.requests: list[StructuredCompletionRequest] = []

    def complete_structured(self, request: StructuredCompletionRequest) -> StructuredCompletionResponse:
        self.requests.append(request)
        if request.response_schema is Plan:
            parsed: BaseModel = _canonical_plan(request.user_prompt)
            model = "fake-planner-model"
        elif request.response_schema is VerifierDecision:
            parsed = _passing_decision()
            model = "fake-verifier-model"
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


def _enquiries(n: int) -> list[dict]:
    return [
        {"id": f"enq-{i}", "text": f"Hi, I'm enquirer {i}, interested in whisky casks."}
        for i in range(1, n + 1)
    ]


class TestHarnessFullStack:
    def test_full_stack_run_produces_n_completed_runs_linked_to_the_batch(
        self, db_session, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(harness_module, "REPORTS_DIR", tmp_path / "reports")
        saved = _isolate_tool_slate()
        try:
            _register_canonical_mock_tools()
            adapter = _HarnessFakeAdapter()
            pipeline = Pipeline(adapter=adapter, settings=Settings())

            summary = harness_module.run_harness(
                enquiries=_enquiries(2),
                n_repeats=2,
                expected_n_enquiries=None,
                pipeline=pipeline,
                db=db_session,
            )

            assert summary.n_runs_expected == 4
            assert summary.n_runs_executed == 4
            assert summary.n_runs_skipped == 0

            persisted = repository.list_runs_for_batch(db_session, summary.harness_batch_id)
            assert len(persisted) == 4
            assert all(r.final_status == "completed" for r in persisted)
            assert all(r.harness_batch_id == summary.harness_batch_id for r in persisted)

            assert summary.metrics["completion_rate"] == 1.0
            assert summary.metrics["pass_rate"] == 1.0
            assert summary.metrics["mean_tokens"] == 2 * (100 + 25)

            assert summary.json_report_path.exists()
            assert summary.markdown_report_path.exists()
        finally:
            _restore_tool_slate(saved)

    def test_variance_is_computed_across_the_repeats_of_each_enquiry(
        self, db_session, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(harness_module, "REPORTS_DIR", tmp_path / "reports")
        saved = _isolate_tool_slate()
        try:
            _register_canonical_mock_tools()
            adapter = _HarnessFakeAdapter()
            pipeline = Pipeline(adapter=adapter, settings=Settings())

            summary = harness_module.run_harness(
                enquiries=_enquiries(1),
                n_repeats=3,
                expected_n_enquiries=None,
                pipeline=pipeline,
                db=db_session,
            )

            variance = summary.metrics["variance"]
            assert variance["by_enquiry"]["enq-1"]["n_repeats"] == 3
        finally:
            _restore_tool_slate(saved)
