"""Integration tests for agent/verifier.py: a fake `ModelAdapter` end to end
with the outputs of a real `Plan` and a real `ToolCallTrace` (as produced by
agent/executor.py), exercising `verify()`'s full flow -- prompt
construction, structured-output validation, and the deterministic
plan-deviation cross-check -- together rather than in isolation. See
docs/contracts.md section 3.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.agent.executor import run_plan
from app.agent.verifier import verify
from app.llm.base import ModelAdapter, StructuredCompletionRequest, StructuredCompletionResponse
from app.schemas.plan import Plan, PlanStep, ToolName
from app.schemas.tool_trace import ToolCall, ToolCallStatus, ToolCallTrace
from app.schemas.verifier import VerifierDecision
from app.services.dedupe import compute_dedupe_hash
from app.tools.base import Tool, ToolResult
from app.tools.registry import ToolRegistry

ENQUIRY_TEXT = "Hi, I'm Jane Doe (jane@example.com), interested in whisky casks in Singapore."


class _ScriptedVerifierAdapter(ModelAdapter):
    """A fake adapter that returns a pre-scripted `VerifierDecision`,
    standing in for a real LLM provider (none is implemented yet)."""

    provider_name = "fake"

    def __init__(self, decision: VerifierDecision) -> None:
        self._decision = decision
        self.requests: list[StructuredCompletionRequest] = []

    def complete_structured(self, request: StructuredCompletionRequest) -> StructuredCompletionResponse:
        self.requests.append(request)
        return StructuredCompletionResponse(
            parsed=self._decision,
            raw_response={"id": "fake"},
            prompt_tokens=150,
            completion_tokens=50,
            latency_ms=20.0,
            model="fake-verifier-model",
        )


def _plan_step(step: int, tool: ToolName, args: dict | None = None) -> PlanStep:
    return PlanStep(step=step, tool=tool, args=args or {}, rationale="integration test step")


def _passing_decision() -> VerifierDecision:
    return VerifierDecision(
        passed=True,
        confidence=0.9,
        fabrication_detected=False,
        fabricated_fields=[],
        plan_deviation_detected=False,
        deviation_details=None,
        reason="Every field verified against the enquiry text; trace matches the plan.",
    )


class TestVerifierAgainstARealExecutorTrace:
    """`run_plan()`'s real output fed directly into `verify()` -- checks the
    two modules genuinely compose, not just that each type-checks alone."""

    def _mock_tool(self, tool_name: str, run_impl):
        from pydantic import BaseModel, ConfigDict

        class _Args(BaseModel):
            model_config = ConfigDict(extra="allow")

        class _Result(BaseModel):
            model_config = ConfigDict(extra="allow")

        return type(
            f"Mock_{tool_name}",
            (Tool,),
            {
                "name": tool_name,
                "description": "mock",
                "args_schema": _Args,
                "result_schema": _Result,
                "run": run_impl,
            },
        )

    def test_verifier_passes_a_clean_executor_trace_when_model_agrees(self) -> None:
        canonical_names = {tool.value for tool in ToolName}
        saved = {name: Tool._registry.pop(name) for name in canonical_names if name in Tool._registry}
        try:
            self._mock_tool(
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
            self._mock_tool(
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
            self._mock_tool(
                "score_lead",
                lambda self, args: ToolResult(
                    success=True, data={"score": 70, "breakdown": {}}, error=None, latency_ms=1.0
                ),
            )
            registry = ToolRegistry()
            plan = Plan(
                steps=[
                    _plan_step(1, ToolName.PARSE_ENQUIRY, {"enquiry_text": ENQUIRY_TEXT}),
                    _plan_step(2, ToolName.LOOKUP_JURISDICTION_RULE, {"country": "Singapore"}),
                    _plan_step(3, ToolName.SCORE_LEAD),
                ]
            )

            execution = run_plan(plan, ENQUIRY_TEXT, registry)
            assert execution.error is None
            assert execution.final_record is not None

            adapter = _ScriptedVerifierAdapter(_passing_decision())
            decision = verify(
                ENQUIRY_TEXT,
                plan,
                execution.trace,
                execution.final_record.model_dump(mode="json"),
                adapter,
            )

            assert decision.passed is True
            assert decision.fabrication_detected is False
            assert decision.plan_deviation_detected is False  # trace genuinely matches the plan
            assert len(adapter.requests) == 1
            # Derived evidence: handling_note from lookup must appear unchanged in the record.
            lookup_call = next(
                c for c in execution.trace.calls if c.tool == ToolName.LOOKUP_JURISDICTION_RULE
            )
            assert (
                execution.final_record.jurisdiction_rule["handling_note"]
                == lookup_call.result["handling_note"]
            )
        finally:
            for name in canonical_names:
                Tool._registry.pop(name, None)
            Tool._registry.update(saved)

    def test_verifier_flags_deviation_when_executor_stopped_early_and_model_still_says_pass(self) -> None:
        """A defensive scenario: even if the (fake, scripted) model
        incorrectly says `pass=True, plan_deviation_detected=False` on a
        trace that used a *different* tool than planned, the deterministic
        cross-check must still catch it -- this is what "never trust the
        Planner or Executor outputs" means operationally."""
        now = datetime.now(UTC)
        plan = Plan(
            steps=[
                _plan_step(1, ToolName.PARSE_ENQUIRY),
                _plan_step(2, ToolName.LOOKUP_JURISDICTION_RULE),
            ]
        )
        # Trace shows the two tools executed in the wrong order relative to
        # the plan -- a substitution the (bogus) model verdict below misses.
        trace = ToolCallTrace(
            calls=[
                ToolCall(
                    step=1,
                    tool=ToolName.LOOKUP_JURISDICTION_RULE,
                    args={},
                    status=ToolCallStatus.SUCCESS,
                    result={},
                    latency_ms=1.0,
                    started_at=now,
                    finished_at=now,
                ),
                ToolCall(
                    step=2,
                    tool=ToolName.PARSE_ENQUIRY,
                    args={},
                    status=ToolCallStatus.SUCCESS,
                    result={},
                    latency_ms=1.0,
                    started_at=now,
                    finished_at=now,
                ),
            ]
        )
        adapter = _ScriptedVerifierAdapter(_passing_decision())  # bogus: claims pass, no deviation

        decision = verify(ENQUIRY_TEXT, plan, trace, {}, adapter)

        assert decision.passed is False
        assert decision.plan_deviation_detected is True
        assert decision.deviation_details is not None


class TestVerifierEndToEndFabricationScenario:
    def test_fabrication_flagged_by_the_fake_adapter_flows_through_unchanged(self) -> None:
        now = datetime.now(UTC)
        plan = Plan(steps=[_plan_step(1, ToolName.PARSE_ENQUIRY)])
        trace = ToolCallTrace(
            calls=[
                ToolCall(
                    step=1,
                    tool=ToolName.PARSE_ENQUIRY,
                    args={},
                    status=ToolCallStatus.SUCCESS,
                    result={"name": "Jane Doe", "phone": "+1-555-0100"},
                    latency_ms=1.0,
                    started_at=now,
                    finished_at=now,
                )
            ]
        )
        final_record = {"extracted": {"name": "Jane Doe", "phone": "+1-555-0100"}}
        fabrication_decision = VerifierDecision(
            passed=False,
            confidence=0.88,
            fabrication_detected=True,
            fabricated_fields=["phone"],
            plan_deviation_detected=False,
            deviation_details=None,
            reason="The phone number does not appear anywhere in the enquiry text and was fabricated.",
        )
        adapter = _ScriptedVerifierAdapter(fabrication_decision)

        decision = verify(ENQUIRY_TEXT, plan, trace, final_record, adapter)

        assert decision.passed is False
        assert decision.fabrication_detected is True
        assert decision.fabricated_fields == ["phone"]
        assert decision.plan_deviation_detected is False  # untouched: trace matches plan

    def test_tampered_handling_note_fails_even_when_model_says_pass(self) -> None:
        now = datetime.now(UTC)
        lookup = {
            "country": "Singapore",
            "requires_disclaimer": False,
            "restricted": False,
            "handling_note": "standard",
        }
        plan = Plan(
            steps=[
                _plan_step(1, ToolName.PARSE_ENQUIRY),
                _plan_step(2, ToolName.LOOKUP_JURISDICTION_RULE),
                _plan_step(3, ToolName.SCORE_LEAD),
            ]
        )
        trace = ToolCallTrace(
            calls=[
                ToolCall(
                    step=1,
                    tool=ToolName.PARSE_ENQUIRY,
                    args={},
                    status=ToolCallStatus.SUCCESS,
                    result={"name": "Jane Doe", "email": "jane@example.com"},
                    latency_ms=1.0,
                    started_at=now,
                    finished_at=now,
                ),
                ToolCall(
                    step=2,
                    tool=ToolName.LOOKUP_JURISDICTION_RULE,
                    args={},
                    status=ToolCallStatus.SUCCESS,
                    result=lookup,
                    latency_ms=1.0,
                    started_at=now,
                    finished_at=now,
                ),
                ToolCall(
                    step=3,
                    tool=ToolName.SCORE_LEAD,
                    args={},
                    status=ToolCallStatus.SUCCESS,
                    result={"score": 70, "breakdown": {}},
                    latency_ms=1.0,
                    started_at=now,
                    finished_at=now,
                ),
            ]
        )
        final_record = {
            "extracted": {"name": "Jane Doe", "email": "jane@example.com"},
            "jurisdiction_rule": {**lookup, "handling_note": "altered by executor"},
            "score": 70,
            "score_breakdown": {},
            "dedupe_hash": compute_dedupe_hash("jane@example.com", None),
        }
        adapter = _ScriptedVerifierAdapter(_passing_decision())

        decision = verify(ENQUIRY_TEXT, plan, trace, final_record, adapter)

        assert decision.passed is False
        assert decision.fabrication_detected is True
        fields = decision.fabricated_fields
        assert "jurisdiction_rule" in fields or "handling_note" in fields


class TestVerifierFabricationReconciliationIntegration:
    def test_llm_false_positive_on_tool_derived_fields_is_cleared(self) -> None:
        now = datetime.now(UTC)
        lookup = {
            "country": "Singapore",
            "requires_disclaimer": False,
            "restricted": False,
            "handling_note": "standard",
        }
        plan = Plan(
            steps=[
                _plan_step(1, ToolName.PARSE_ENQUIRY),
                _plan_step(2, ToolName.LOOKUP_JURISDICTION_RULE),
                _plan_step(3, ToolName.SCORE_LEAD),
            ]
        )
        parse = {"name": "Jane Doe", "email": "jane@example.com", "phone": None}
        trace = ToolCallTrace(
            calls=[
                ToolCall(
                    step=1,
                    tool=ToolName.PARSE_ENQUIRY,
                    args={},
                    status=ToolCallStatus.SUCCESS,
                    result=parse,
                    latency_ms=1.0,
                    started_at=now,
                    finished_at=now,
                ),
                ToolCall(
                    step=2,
                    tool=ToolName.LOOKUP_JURISDICTION_RULE,
                    args={},
                    status=ToolCallStatus.SUCCESS,
                    result=lookup,
                    latency_ms=1.0,
                    started_at=now,
                    finished_at=now,
                ),
                ToolCall(
                    step=3,
                    tool=ToolName.SCORE_LEAD,
                    args={},
                    status=ToolCallStatus.SUCCESS,
                    result={"score": 70, "breakdown": {}},
                    latency_ms=1.0,
                    started_at=now,
                    finished_at=now,
                ),
            ]
        )
        final_record = {
            "extracted": parse,
            "jurisdiction_rule": dict(lookup),
            "score": 70,
            "score_breakdown": {},
            "dedupe_hash": compute_dedupe_hash("jane@example.com", None),
        }
        false_positive = VerifierDecision(
            passed=False,
            confidence=0.7,
            fabrication_detected=True,
            fabricated_fields=[
                "dedupe_hash",
                "jurisdiction_rule.handling_note",
                "jurisdiction_rule.requires_disclaimer",
            ],
            plan_deviation_detected=False,
            deviation_details=None,
            reason="These fields do not appear in the enquiry text.",
        )
        adapter = _ScriptedVerifierAdapter(false_positive)

        decision = verify(ENQUIRY_TEXT, plan, trace, final_record, adapter)

        assert decision.fabrication_detected is False
        assert decision.fabricated_fields == []
        assert decision.passed is True
        assert decision.plan_deviation_detected is False
