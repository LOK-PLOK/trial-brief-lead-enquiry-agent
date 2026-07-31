"""Unit tests for agent/verifier.py. See docs/contracts.md section 3.

Uses a fake `ModelAdapter` test double throughout -- no real provider is
implemented yet (llm/factory.py), and the Verifier must depend only on the
`ModelAdapter` interface (docs/contracts.md section 5), so a fake is exactly
what the contract calls for, not a workaround.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.agent import verifier as verifier_module
from app.agent.prompts import planner_prompt, verifier_prompt
from app.agent.verifier import VerifierValidationError, _detect_plan_deviation, verify
from app.core.config import Settings
from app.core.logging import run_id_ctx, stage_ctx
from app.llm.base import ModelAdapter, StructuredCompletionRequest, StructuredCompletionResponse
from app.schemas.plan import Plan, PlanStep, ToolName
from app.schemas.tool_trace import ToolCall, ToolCallStatus, ToolCallTrace
from app.schemas.verifier import VerifierDecision

ENQUIRY_TEXT = "Hi, I'm Jane Doe, jane@example.com, interested in whisky casks in Singapore."


def _plan(*tools: ToolName) -> Plan:
    return Plan(
        steps=[
            PlanStep(step=i, tool=tool, args={}, rationale="test step")
            for i, tool in enumerate(tools, start=1)
        ]
    )


def _trace(*tools: ToolName, statuses: list[str] | None = None) -> ToolCallTrace:
    statuses = statuses or [ToolCallStatus.SUCCESS] * len(tools)
    now = datetime.now(UTC)
    return ToolCallTrace(
        calls=[
            ToolCall(
                step=i,
                tool=tool,
                args={},
                status=status,
                result={} if status == ToolCallStatus.SUCCESS else None,
                error=None if status == ToolCallStatus.SUCCESS else "failed",
                latency_ms=1.0,
                started_at=now,
                finished_at=now,
            )
            for i, (tool, status) in enumerate(zip(tools, statuses), start=1)
        ]
    )


CANONICAL_TOOLS = (
    ToolName.PARSE_ENQUIRY,
    ToolName.LOOKUP_JURISDICTION_RULE,
    ToolName.SCORE_LEAD,
    ToolName.WRITE_RECORD,
)

FINAL_RECORD = {
    "extracted": {"name": "Jane Doe", "email": "jane@example.com"},
    "jurisdiction_rule": {"country": "Singapore"},
    "score": 80,
    "score_breakdown": {"budget": 40},
    "dedupe_hash": "abc123",
}


def _passing_decision(**overrides) -> VerifierDecision:
    defaults = dict(
        passed=True,
        confidence=0.95,
        fabrication_detected=False,
        fabricated_fields=[],
        plan_deviation_detected=False,
        deviation_details=None,
        reason="All fields verified against the enquiry text; trace matches the plan.",
    )
    defaults.update(overrides)
    return VerifierDecision(**defaults)


def _response_for(decision: VerifierDecision, *, model: str = "fake-model") -> StructuredCompletionResponse:
    return StructuredCompletionResponse(
        parsed=decision,
        raw_response={"id": "fake-completion"},
        prompt_tokens=200,
        completion_tokens=60,
        latency_ms=15.5,
        model=model,
    )


class FakeAdapter(ModelAdapter):
    """Records every request it receives and returns queued responses (or
    raises queued exceptions) in order, one per call to `complete_structured`."""

    provider_name = "fake"

    def __init__(self, responses: list[StructuredCompletionResponse | Exception]) -> None:
        self._responses = list(responses)
        self.requests: list[StructuredCompletionRequest] = []

    def complete_structured(self, request: StructuredCompletionRequest) -> StructuredCompletionResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("FakeAdapter called more times than responses were queued")
        outcome = self._responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class TestVerifyHappyPath:
    def test_returns_the_decision_on_a_matching_plan_and_trace(self) -> None:
        decision = _passing_decision()
        adapter = FakeAdapter([_response_for(decision)])
        plan = _plan(*CANONICAL_TOOLS)
        trace = _trace(*CANONICAL_TOOLS)

        result = verify(ENQUIRY_TEXT, plan, trace, FINAL_RECORD, adapter)

        assert result.passed is True
        assert len(adapter.requests) == 1

    def test_request_uses_structured_output_with_verifier_decision_schema(self) -> None:
        adapter = FakeAdapter([_response_for(_passing_decision())])
        plan = _plan(*CANONICAL_TOOLS)
        trace = _trace(*CANONICAL_TOOLS)

        verify(ENQUIRY_TEXT, plan, trace, FINAL_RECORD, adapter)

        assert adapter.requests[0].response_schema is VerifierDecision

    def test_request_includes_enquiry_plan_trace_and_record_in_user_prompt(self) -> None:
        adapter = FakeAdapter([_response_for(_passing_decision())])
        plan = _plan(*CANONICAL_TOOLS)
        trace = _trace(*CANONICAL_TOOLS)

        verify(ENQUIRY_TEXT, plan, trace, FINAL_RECORD, adapter)

        user_prompt = adapter.requests[0].user_prompt
        assert "jane@example.com" in user_prompt  # enquiry text
        assert "parse_enquiry" in user_prompt  # plan
        assert "abc123" in user_prompt  # final record's dedupe_hash

    def test_request_carries_no_conversation_history(self) -> None:
        """Every call is a fresh, isolated StructuredCompletionRequest --
        development-rules.md: "Each LLM call is isolated". There is no
        messages/history field at all on the request dataclass."""
        adapter = FakeAdapter([_response_for(_passing_decision())])
        verify(ENQUIRY_TEXT, _plan(*CANONICAL_TOOLS), _trace(*CANONICAL_TOOLS), FINAL_RECORD, adapter)

        request = adapter.requests[0]
        assert not hasattr(request, "messages")
        assert not hasattr(request, "history")

    def test_model_comes_from_settings_resolved_model_verifier(self, monkeypatch) -> None:
        custom_settings = Settings(verifier_model="custom-verifier-model")
        monkeypatch.setattr(verifier_module, "get_settings", lambda: custom_settings)

        adapter = FakeAdapter([_response_for(_passing_decision())])
        verify(ENQUIRY_TEXT, _plan(*CANONICAL_TOOLS), _trace(*CANONICAL_TOOLS), FINAL_RECORD, adapter)

        assert adapter.requests[0].model == "custom-verifier-model"

    def test_falls_back_to_model_name_when_no_verifier_override(self, monkeypatch) -> None:
        custom_settings = Settings(model_name="fallback-model")
        monkeypatch.setattr(verifier_module, "get_settings", lambda: custom_settings)

        adapter = FakeAdapter([_response_for(_passing_decision())])
        verify(ENQUIRY_TEXT, _plan(*CANONICAL_TOOLS), _trace(*CANONICAL_TOOLS), FINAL_RECORD, adapter)

        assert adapter.requests[0].model == "fallback-model"

    def test_makes_exactly_one_adapter_call_never_more(self) -> None:
        """Requirement: retry only according to the existing adapter
        contract -- no Verifier-level retry loop of its own."""
        adapter = FakeAdapter([_response_for(_passing_decision())])
        verify(ENQUIRY_TEXT, _plan(*CANONICAL_TOOLS), _trace(*CANONICAL_TOOLS), FINAL_RECORD, adapter)
        assert len(adapter.requests) == 1


class TestVerifyFabricationPassthrough:
    def test_fabrication_verdict_from_the_model_is_returned_as_is(self) -> None:
        decision = _passing_decision(
            passed=False,
            fabrication_detected=True,
            fabricated_fields=["phone"],
            reason="Phone number does not appear anywhere in the enquiry text.",
        )
        adapter = FakeAdapter([_response_for(decision)])
        plan = _plan(*CANONICAL_TOOLS)
        trace = _trace(*CANONICAL_TOOLS)

        result = verify(ENQUIRY_TEXT, plan, trace, FINAL_RECORD, adapter)

        assert result.passed is False
        assert result.fabrication_detected is True
        assert result.fabricated_fields == ["phone"]
        assert "phone" in result.reason.lower()


class TestDetectPlanDeviation:
    def test_no_deviation_when_trace_matches_plan_exactly(self) -> None:
        plan = _plan(*CANONICAL_TOOLS)
        trace = _trace(*CANONICAL_TOOLS)
        assert _detect_plan_deviation(plan, trace) is None

    def test_no_deviation_when_trace_is_a_shorter_prefix_after_early_stop(self) -> None:
        """A trace shorter than the plan because execution legitimately
        stopped on a tool failure is NOT a deviation."""
        plan = _plan(*CANONICAL_TOOLS)
        trace = _trace(ToolName.PARSE_ENQUIRY, ToolName.LOOKUP_JURISDICTION_RULE)
        assert _detect_plan_deviation(plan, trace) is None

    def test_detects_substitution(self) -> None:
        plan = _plan(ToolName.PARSE_ENQUIRY, ToolName.LOOKUP_JURISDICTION_RULE, ToolName.SCORE_LEAD)
        trace = _trace(ToolName.PARSE_ENQUIRY, ToolName.SCORE_LEAD, ToolName.LOOKUP_JURISDICTION_RULE)
        deviation = _detect_plan_deviation(plan, trace)
        assert deviation is not None
        assert "Step 2" in deviation

    def test_detects_reorder(self) -> None:
        plan = _plan(ToolName.LOOKUP_JURISDICTION_RULE, ToolName.SCORE_LEAD)
        trace = _trace(ToolName.SCORE_LEAD, ToolName.LOOKUP_JURISDICTION_RULE)
        assert _detect_plan_deviation(plan, trace) is not None

    def test_detects_extra_unplanned_step(self) -> None:
        plan = _plan(ToolName.PARSE_ENQUIRY)
        trace = _trace(ToolName.PARSE_ENQUIRY, ToolName.SCORE_LEAD)
        deviation = _detect_plan_deviation(plan, trace)
        assert deviation is not None
        assert "unplanned" in deviation.lower()

    def test_no_deviation_for_two_empty_sequences(self) -> None:
        assert _detect_plan_deviation(_plan(), _trace()) is None


class TestVerifyPlanDeviationOverride:
    def test_overrides_a_missed_deviation_the_model_did_not_catch(self) -> None:
        """The core independence guarantee for this dimension: the Verifier
        must never simply trust the model's own plan_deviation_detected
        claim when ground truth disagrees."""
        decision = _passing_decision(passed=True, plan_deviation_detected=False)
        adapter = FakeAdapter([_response_for(decision)])
        plan = _plan(ToolName.PARSE_ENQUIRY, ToolName.LOOKUP_JURISDICTION_RULE)
        trace = _trace(ToolName.LOOKUP_JURISDICTION_RULE, ToolName.PARSE_ENQUIRY)  # reordered

        result = verify(ENQUIRY_TEXT, plan, trace, FINAL_RECORD, adapter)

        assert result.passed is False
        assert result.plan_deviation_detected is True
        assert result.deviation_details is not None
        assert result.reason  # non-empty

    def test_does_not_alter_a_correct_no_deviation_decision(self) -> None:
        decision = _passing_decision(passed=True, plan_deviation_detected=False)
        adapter = FakeAdapter([_response_for(decision)])
        plan = _plan(*CANONICAL_TOOLS)
        trace = _trace(*CANONICAL_TOOLS)

        result = verify(ENQUIRY_TEXT, plan, trace, FINAL_RECORD, adapter)

        assert result.passed is True
        assert result.plan_deviation_detected is False
        assert result.deviation_details is None

    def test_preserves_existing_reason_when_model_already_correctly_flagged_it(self) -> None:
        decision = _passing_decision(
            passed=False,
            plan_deviation_detected=True,
            deviation_details="model's own description",
            reason="model's own reason",
        )
        adapter = FakeAdapter([_response_for(decision)])
        plan = _plan(ToolName.PARSE_ENQUIRY, ToolName.LOOKUP_JURISDICTION_RULE)
        trace = _trace(ToolName.LOOKUP_JURISDICTION_RULE, ToolName.PARSE_ENQUIRY)

        result = verify(ENQUIRY_TEXT, plan, trace, FINAL_RECORD, adapter)

        assert result.reason == "model's own reason"  # not clobbered

    def test_deterministic_deviation_does_not_clear_a_fabrication_failure(self) -> None:
        """A real deviation must force pass=False and flip the deviation
        flags, without erasing an independent fabrication finding."""
        decision = _passing_decision(
            passed=False,
            fabrication_detected=True,
            fabricated_fields=["phone"],
            reason="phone fabricated",
        )
        adapter = FakeAdapter([_response_for(decision)])
        plan = _plan(ToolName.PARSE_ENQUIRY, ToolName.LOOKUP_JURISDICTION_RULE)
        trace = _trace(ToolName.LOOKUP_JURISDICTION_RULE, ToolName.PARSE_ENQUIRY)

        result = verify(ENQUIRY_TEXT, plan, trace, FINAL_RECORD, adapter)

        assert result.passed is False
        assert result.fabrication_detected is True  # untouched
        assert result.plan_deviation_detected is True  # added


class TestVerifyAdapterFailurePropagation:
    def test_adapter_exception_propagates_without_extra_retry(self) -> None:
        class _FakeProviderError(Exception):
            pass

        adapter = FakeAdapter([_FakeProviderError("rate limited")])

        with pytest.raises(_FakeProviderError):
            verify(ENQUIRY_TEXT, _plan(*CANONICAL_TOOLS), _trace(*CANONICAL_TOOLS), FINAL_RECORD, adapter)

        assert len(adapter.requests) == 1

    def test_wrong_type_from_adapter_raises_verifier_validation_error(self) -> None:
        bogus_response = StructuredCompletionResponse(
            parsed="not-a-decision",  # type: ignore[arg-type]
            raw_response={},
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=1.0,
            model="fake-model",
        )
        adapter = FakeAdapter([bogus_response])

        with pytest.raises(VerifierValidationError, match="expected VerifierDecision"):
            verify(ENQUIRY_TEXT, _plan(*CANONICAL_TOOLS), _trace(*CANONICAL_TOOLS), FINAL_RECORD, adapter)

        assert len(adapter.requests) == 1


class TestVerifyLogging:
    def test_logs_prompt_response_latency_and_token_usage(self, caplog) -> None:
        import logging

        decision = _passing_decision()
        adapter = FakeAdapter([_response_for(decision)])

        with caplog.at_level(logging.INFO, logger="app.agent.verifier"):
            verify(ENQUIRY_TEXT, _plan(*CANONICAL_TOOLS), _trace(*CANONICAL_TOOLS), FINAL_RECORD, adapter)

        matching = [r for r in caplog.records if getattr(r, "event", None) == "verifier_llm_call"]
        assert len(matching) == 1
        record = matching[0]
        assert record.system_prompt
        assert record.user_prompt
        assert record.response == decision.model_dump(mode="json", by_alias=True)
        assert record.latency_ms == 15.5
        assert record.prompt_tokens == 200
        assert record.completion_tokens == 60

    def test_logged_response_reflects_the_overridden_decision_not_the_raw_one(self, caplog) -> None:
        import logging

        decision = _passing_decision(passed=True, plan_deviation_detected=False)
        adapter = FakeAdapter([_response_for(decision)])
        plan = _plan(ToolName.PARSE_ENQUIRY, ToolName.LOOKUP_JURISDICTION_RULE)
        trace = _trace(ToolName.LOOKUP_JURISDICTION_RULE, ToolName.PARSE_ENQUIRY)

        with caplog.at_level(logging.INFO, logger="app.agent.verifier"):
            verify(ENQUIRY_TEXT, plan, trace, FINAL_RECORD, adapter)

        record = next(r for r in caplog.records if getattr(r, "event", None) == "verifier_llm_call")
        assert record.response["pass"] is False

    def test_warns_when_deterministic_check_overrides_a_missed_deviation(self, caplog) -> None:
        import logging

        decision = _passing_decision(passed=True, plan_deviation_detected=False)
        adapter = FakeAdapter([_response_for(decision)])
        plan = _plan(ToolName.PARSE_ENQUIRY, ToolName.LOOKUP_JURISDICTION_RULE)
        trace = _trace(ToolName.LOOKUP_JURISDICTION_RULE, ToolName.PARSE_ENQUIRY)

        with caplog.at_level(logging.WARNING, logger="app.agent.verifier"):
            verify(ENQUIRY_TEXT, plan, trace, FINAL_RECORD, adapter)

        overrides = [
            r for r in caplog.records if getattr(r, "event", None) == "verifier_plan_deviation_override"
        ]
        assert len(overrides) == 1

    def test_sets_and_resets_stage_context(self) -> None:
        class _StageCapturingAdapter(ModelAdapter):
            provider_name = "fake"
            captured_stage: str | None = None

            def complete_structured(self, request):
                self.captured_stage = stage_ctx.get()
                return _response_for(_passing_decision())

        adapter = _StageCapturingAdapter()
        assert stage_ctx.get() is None

        verify(ENQUIRY_TEXT, _plan(*CANONICAL_TOOLS), _trace(*CANONICAL_TOOLS), FINAL_RECORD, adapter)

        assert adapter.captured_stage == "verifier"
        assert stage_ctx.get() is None

    def test_resets_stage_context_even_when_verify_raises(self) -> None:
        class _Boom(Exception):
            pass

        class _RaisingAdapter(ModelAdapter):
            provider_name = "fake"

            def complete_structured(self, request):
                raise _Boom("simulated failure")

        assert stage_ctx.get() is None
        plan = _plan(*CANONICAL_TOOLS)
        trace = _trace(*CANONICAL_TOOLS)
        with pytest.raises(_Boom):
            verify(ENQUIRY_TEXT, plan, trace, FINAL_RECORD, _RaisingAdapter())
        assert stage_ctx.get() is None

    def test_metadata_includes_run_id_from_context_var(self) -> None:
        token = run_id_ctx.set("run-456")
        try:
            adapter = FakeAdapter([_response_for(_passing_decision())])
            verify(ENQUIRY_TEXT, _plan(*CANONICAL_TOOLS), _trace(*CANONICAL_TOOLS), FINAL_RECORD, adapter)
            assert adapter.requests[0].metadata["run_id"] == "run-456"
        finally:
            run_id_ctx.reset(token)


class TestVerifierIndependence:
    """Structural guardrails for docs/development-rules.md's "Verifier is
    completely independent" and the brief's "not the same prompt, not the
    same request, not a self-check appended to extraction"."""

    def test_verifier_module_does_not_import_planner_module_or_prompt(self) -> None:
        source = Path(inspect.getfile(verifier_module)).read_text()
        assert "agent.planner" not in source
        assert "agent.prompts.planner_prompt" not in source
        assert "PLANNER_SYSTEM_PROMPT" not in source
        assert "build_planner_user_prompt" not in source

    def test_verifier_module_does_not_import_executor_or_tools(self) -> None:
        source = Path(inspect.getfile(verifier_module)).read_text()
        assert "agent.executor" not in source
        assert "app.tools" not in source

    def test_verifier_module_does_not_import_repair_or_harness(self) -> None:
        source = Path(inspect.getfile(verifier_module)).read_text()
        assert "agent.repair" not in source
        assert "evaluation" not in source

    def test_verifier_prompt_module_does_not_import_planner_prompt_module(self) -> None:
        """The module docstring is allowed to *mention* planner_prompt.py in
        prose (explaining the independence rule); it must never actually
        import from it or reference its symbols."""
        source = Path(inspect.getfile(verifier_prompt)).read_text()
        assert "import planner_prompt" not in source
        assert "from app.agent.prompts.planner_prompt" not in source
        assert "PLANNER_SYSTEM_PROMPT" not in source

    def test_system_prompts_are_different_strings(self) -> None:
        assert verifier_prompt.VERIFIER_SYSTEM_PROMPT != planner_prompt.PLANNER_SYSTEM_PROMPT

    def test_system_prompts_share_no_verbatim_lines(self) -> None:
        """A stronger check than mere string inequality: not even a single
        line of prompt text is copy-pasted between the two."""
        verifier_lines = {
            line.strip() for line in verifier_prompt.VERIFIER_SYSTEM_PROMPT.splitlines() if line.strip()
        }
        planner_lines = {
            line.strip() for line in planner_prompt.PLANNER_SYSTEM_PROMPT.splitlines() if line.strip()
        }
        assert verifier_lines.isdisjoint(planner_lines)

    def test_verifier_module_has_exactly_one_public_callable(self) -> None:
        public_names = [
            name
            for name, value in vars(verifier_module).items()
            if not name.startswith("_")
            and inspect.isfunction(value)
            and value.__module__ == verifier_module.__name__
        ]
        assert public_names == ["verify"]

    def test_request_built_by_verify_uses_only_verifier_prompt_module_content(self) -> None:
        """The actual `StructuredCompletionRequest` sent to the adapter must
        be traceable entirely to verifier_prompt.py -- not merely that the
        module doesn't import planner_prompt, but that the live request
        really is built from the independent prompt."""
        adapter = FakeAdapter([_response_for(_passing_decision())])
        verify(ENQUIRY_TEXT, _plan(*CANONICAL_TOOLS), _trace(*CANONICAL_TOOLS), FINAL_RECORD, adapter)

        request = adapter.requests[0]
        assert request.system_prompt == verifier_prompt.VERIFIER_SYSTEM_PROMPT
        assert request.system_prompt != planner_prompt.PLANNER_SYSTEM_PROMPT
