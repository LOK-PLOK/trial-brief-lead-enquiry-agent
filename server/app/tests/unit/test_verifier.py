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
from app.agent.verifier import (
    VerifierValidationError,
    _apply_extracted_contradiction_gate,
    _detect_derived_field_fabrication,
    _detect_plan_deviation,
    _reason_self_contradicts_claim,
    _strip_self_contradictory_claims,
    verify,
)
from app.schemas.verifier import ExtractedFieldLabel, ExtractedFieldVerdict
from app.core.config import Settings
from app.core.logging import run_id_ctx, stage_ctx
from app.llm.base import ModelAdapter, StructuredCompletionRequest, StructuredCompletionResponse
from app.schemas.plan import Plan, PlanStep, ToolName
from app.schemas.tool_trace import ToolCall, ToolCallStatus, ToolCallTrace
from app.schemas.verifier import VerifierDecision
from app.services.dedupe import compute_dedupe_hash

ENQUIRY_TEXT = "Hi, I'm Jane Doe, jane@example.com, interested in whisky casks in Singapore."

LOOKUP_RESULT = {
    "country": "Singapore",
    "requires_disclaimer": True,
    "restricted": False,
    "handling_note": "Standard risk disclaimer for Singapore collectibles.",
}

SCORE_RESULT = {
    "score": 80,
    "breakdown": {"budget": 40, "urgency": 20, "jurisdiction_risk": 20},
}

PARSE_RESULT = {
    "name": "Jane Doe",
    "email": "jane@example.com",
    "phone": None,
    "country": "Singapore",
    "budget_band": "C",
    "asset_interest": "whisky casks",
    "urgency": "Within three months",
}

WRITE_RESULT = {
    "lead_id": "lead-1",
    "dedupe_hash": compute_dedupe_hash("jane@example.com", None),
}

_DEFAULT_RESULTS: dict[ToolName, dict] = {
    ToolName.PARSE_ENQUIRY: PARSE_RESULT,
    ToolName.LOOKUP_JURISDICTION_RULE: LOOKUP_RESULT,
    ToolName.SCORE_LEAD: SCORE_RESULT,
    ToolName.WRITE_RECORD: WRITE_RESULT,
}


def _plan(*tools: ToolName) -> Plan:
    return Plan(
        steps=[
            PlanStep(step=i, tool=tool, args={}, rationale="test step")
            for i, tool in enumerate(tools, start=1)
        ]
    )


def _trace(
    *tools: ToolName,
    statuses: list[str] | None = None,
    results: dict[ToolName, dict] | None = None,
) -> ToolCallTrace:
    statuses = statuses or [ToolCallStatus.SUCCESS] * len(tools)
    now = datetime.now(UTC)
    calls = []
    for i, (tool, status) in enumerate(zip(tools, statuses), start=1):
        if status == ToolCallStatus.SUCCESS:
            if results is not None and tool in results:
                result = results[tool]
            else:
                result = dict(_DEFAULT_RESULTS.get(tool, {}))
        else:
            result = None
        calls.append(
            ToolCall(
                step=i,
                tool=tool,
                args={},
                status=status,
                result=result,
                error=None if status == ToolCallStatus.SUCCESS else "failed",
                latency_ms=1.0,
                started_at=now,
                finished_at=now,
            )
        )
    return ToolCallTrace(calls=calls)


CANONICAL_TOOLS = (
    ToolName.PARSE_ENQUIRY,
    ToolName.LOOKUP_JURISDICTION_RULE,
    ToolName.SCORE_LEAD,
    ToolName.WRITE_RECORD,
)

FINAL_RECORD = {
    "extracted": dict(PARSE_RESULT),
    "jurisdiction_rule": dict(LOOKUP_RESULT),
    "score": SCORE_RESULT["score"],
    "score_breakdown": dict(SCORE_RESULT["breakdown"]),
    "dedupe_hash": WRITE_RESULT["dedupe_hash"],
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
        assert WRITE_RESULT["dedupe_hash"] in user_prompt  # final record's dedupe_hash

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


class TestFabricationReconciliation:
    """LLM fabrication claims on tool-derived / computed fields must be
    cleared when deterministic evidence proves those fields match."""

    def test_clears_llm_claims_on_matching_handling_note_and_dedupe_hash(self) -> None:
        adapter = FakeAdapter(
            [
                _response_for(
                    _passing_decision(
                        passed=False,
                        fabrication_detected=True,
                        fabricated_fields=[
                            "dedupe_hash",
                            "jurisdiction_rule.handling_note",
                            "jurisdiction_rule.requires_disclaimer",
                        ],
                        reason=(
                            "handling_note / requires_disclaimer / dedupe_hash do not "
                            "appear in the enquiry text"
                        ),
                    )
                )
            ]
        )
        # Pre-persist shape: no write_record in the verify-time trace.
        tools = (
            ToolName.PARSE_ENQUIRY,
            ToolName.LOOKUP_JURISDICTION_RULE,
            ToolName.SCORE_LEAD,
        )
        decision = verify(ENQUIRY_TEXT, _plan(*tools), _trace(*tools), FINAL_RECORD, adapter)
        assert decision.fabrication_detected is False
        assert decision.fabricated_fields == []
        assert decision.passed is True

    def test_clears_llm_score_claim_when_score_matches_tool(self) -> None:
        adapter = FakeAdapter(
            [
                _response_for(
                    _passing_decision(
                        passed=False,
                        fabrication_detected=True,
                        fabricated_fields=["score", "score_breakdown"],
                        reason="score not in enquiry",
                    )
                )
            ]
        )
        decision = verify(
            ENQUIRY_TEXT, _plan(*CANONICAL_TOOLS), _trace(*CANONICAL_TOOLS), FINAL_RECORD, adapter
        )
        assert decision.fabrication_detected is False
        assert decision.passed is True

    def test_keeps_extracted_claim_while_clearing_matching_derived(self) -> None:
        adapter = FakeAdapter(
            [
                _response_for(
                    _passing_decision(
                        passed=False,
                        fabrication_detected=True,
                        fabricated_fields=["email", "handling_note", "dedupe_hash"],
                        reason="mixed claims",
                    )
                )
            ]
        )
        decision = verify(
            ENQUIRY_TEXT, _plan(*CANONICAL_TOOLS), _trace(*CANONICAL_TOOLS), FINAL_RECORD, adapter
        )
        assert decision.fabrication_detected is True
        assert decision.passed is False
        assert decision.fabricated_fields == ["email"]

    def test_keeps_derived_claim_when_tool_output_mismatches(self) -> None:
        altered = {
            **FINAL_RECORD,
            "jurisdiction_rule": {**LOOKUP_RESULT, "handling_note": "tampered"},
        }
        adapter = FakeAdapter(
            [
                _response_for(
                    _passing_decision(
                        passed=False,
                        fabrication_detected=True,
                        fabricated_fields=["jurisdiction_rule.handling_note"],
                        reason="handling_note not in enquiry",
                    )
                )
            ]
        )
        decision = verify(ENQUIRY_TEXT, _plan(*CANONICAL_TOOLS), _trace(*CANONICAL_TOOLS), altered, adapter)
        assert decision.fabrication_detected is True
        assert decision.passed is False
        fields = decision.fabricated_fields
        assert "jurisdiction_rule" in fields or "handling_note" in fields

    def test_keeps_dedupe_hash_claim_when_recompute_fails(self) -> None:
        altered = {**FINAL_RECORD, "dedupe_hash": "not-the-real-hash"}
        adapter = FakeAdapter(
            [
                _response_for(
                    _passing_decision(
                        passed=False,
                        fabrication_detected=True,
                        fabricated_fields=["dedupe_hash"],
                        reason="dedupe_hash not in enquiry",
                    )
                )
            ]
        )
        tools = (
            ToolName.PARSE_ENQUIRY,
            ToolName.LOOKUP_JURISDICTION_RULE,
            ToolName.SCORE_LEAD,
        )
        decision = verify(ENQUIRY_TEXT, _plan(*tools), _trace(*tools), altered, adapter)
        assert decision.fabrication_detected is True
        assert "dedupe_hash" in decision.fabricated_fields
        assert decision.passed is False

    def test_does_not_clear_extracted_fields_via_deterministic_match(self) -> None:
        """Even when the whole record matches tools, an extracted-field LLM
        claim must survive (enquiry-only validation)."""
        adapter = FakeAdapter(
            [
                _response_for(
                    _passing_decision(
                        passed=False,
                        fabrication_detected=True,
                        fabricated_fields=["phone"],
                        reason="phone not supported by enquiry",
                    )
                )
            ]
        )
        decision = verify(
            ENQUIRY_TEXT, _plan(*CANONICAL_TOOLS), _trace(*CANONICAL_TOOLS), FINAL_RECORD, adapter
        )
        assert decision.fabricated_fields == ["phone"]
        assert decision.fabrication_detected is True
        assert decision.passed is False


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
            result = verify(
                ENQUIRY_TEXT, _plan(*CANONICAL_TOOLS), _trace(*CANONICAL_TOOLS), FINAL_RECORD, adapter
            )

        matching = [r for r in caplog.records if getattr(r, "event", None) == "verifier_llm_call"]
        assert len(matching) == 1
        record = matching[0]
        assert record.system_prompt
        assert record.user_prompt
        # Logged response is the post-gate decision (may include deterministic
        # extracted_field_verdicts even when the raw LLM omitted them).
        assert record.response == result.model_dump(mode="json", by_alias=True)
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


class TestVerifierPromptExtractedVsDerived:
    def test_system_prompt_defines_extracted_and_derived_categories(self) -> None:
        prompt = verifier_prompt.VERIFIER_SYSTEM_PROMPT
        assert "EXTRACTED FIELDS" in prompt
        assert "DERIVED FIELDS" in prompt
        assert "NEVER: planner placeholder arguments" in prompt or "must NEVER be used as evidence" in prompt
        assert "ONLY against successful tool outputs" in prompt
        assert "NOT fabrication merely because it does not appear in the" in prompt
        assert "enquiry text" in prompt

    def test_system_prompt_uses_supported_contradicted_insufficient(self) -> None:
        prompt = verifier_prompt.VERIFIER_SYSTEM_PROMPT
        assert "NOT a second extractor" in prompt
        assert "NOT re-extracting" in prompt or "NOT\nre-extracting" in prompt
        assert "extracted_field_verdicts" in prompt
        assert "SUPPORTED" in prompt
        assert "CONTRADICTED" in prompt
        assert "INSUFFICIENT_EVIDENCE" in prompt
        assert "reasonable extractor" in prompt
        assert "Difference of interpretation is NOT" in prompt
        assert "ONLY this label" in prompt or "only CONTRADICTED" in prompt.lower()

    def test_system_prompt_worked_urgency_examples(self) -> None:
        prompt = verifier_prompt.VERIFIER_SYSTEM_PROMPT
        assert '"No rush at all"' in prompt or "No rush at all" in prompt
        assert '"Not urgent"' in prompt or "Not urgent" in prompt
        assert "next few months (not urgent)" in prompt
        assert "Within 30 days" in prompt or "within 30 days" in prompt
        assert "ASAP" in prompt
        assert "Returning `Exploratory` is CONTRADICTED" in prompt or "Exploratory` is CONTRADICTED" in prompt

    def test_user_prompt_reminds_support_framework(self) -> None:
        text = verifier_prompt.build_verifier_user_prompt("hi", {"steps": []}, {"calls": []}, {})
        assert "NOT a second extractor" in text
        assert "extracted_field_verdicts" in text
        assert "SUPPORTED / CONTRADICTED / INSUFFICIENT_EVIDENCE" in text
        assert "Only CONTRADICTED may appear in fabricated_fields" in text
        assert "AUD 120,000" in text
        assert "No rush at all" in text or "not urgent" in text.lower()

    def test_user_prompt_reminds_split_rules_and_redacts_plan_args(self) -> None:
        text = verifier_prompt.build_verifier_user_prompt(
            "hi",
            {
                "steps": [
                    {
                        "step": 1,
                        "tool": "parse_enquiry",
                        "args": {"enquiry_text": "hi"},
                        "rationale": "x",
                    },
                    {
                        "step": 3,
                        "tool": "score_lead",
                        "args": {
                            "extracted": {"budget_band": "Unknown", "urgency": "Unknown"},
                            "jurisdiction_rule": {"handling_note": ""},
                        },
                        "rationale": "y",
                    },
                ]
            },
            {"calls": []},
            {},
        )
        assert "parse_enquiry" in text
        assert "Never use planner placeholders" in text or "non-authoritative" in text.lower()
        assert "dedupe_hash" in text
        assert '"budget_band": "Unknown"' not in text
        assert '"urgency": "Unknown"' not in text
        assert '"_redacted"' in text


class TestManualEnquiryUrgencySupportPasses:
    """E01 / E02 / E10 style: parser urgency=Exploratory is SUPPORTED → PASS.
    Prompt must encode the support framework; verify() pass-through when
    the LLM follows it (scripted). Deterministic checks unchanged.
    """

    def _verify(
        self,
        enquiry: str,
        *,
        urgency: str,
        budget_band: str = "C",
        name: str = "Test Lead",
        email: str = "test@example.com",
        country: str = "Australia",
    ) -> VerifierDecision:
        parse = {
            "name": name,
            "email": email,
            "phone": "+61 400 000 000",
            "country": country,
            "budget_band": budget_band,
            "asset_interest": "whisky casks",
            "urgency": urgency,
        }
        urgency_pts = {"Exploratory": 5, "Within three months": 15, "Immediate": 30, "Unknown": 0}[urgency]
        budget_pts = {"A": 10, "B": 20, "C": 30, "D": 40, "Unknown": 0}[budget_band]
        breakdown = {
            "budget": budget_pts,
            "urgency": urgency_pts,
            "jurisdiction_risk": 15,
        }
        record = {
            "extracted": dict(parse),
            "jurisdiction_rule": dict(LOOKUP_RESULT),
            "score": sum(breakdown.values()),
            "score_breakdown": breakdown,
            "dedupe_hash": compute_dedupe_hash(parse["email"], parse["phone"]),
        }
        tools = (
            ToolName.PARSE_ENQUIRY,
            ToolName.LOOKUP_JURISDICTION_RULE,
            ToolName.SCORE_LEAD,
        )
        trace = _trace(
            *tools,
            results={
                ToolName.PARSE_ENQUIRY: parse,
                ToolName.LOOKUP_JURISDICTION_RULE: LOOKUP_RESULT,
                ToolName.SCORE_LEAD: {"score": record["score"], "breakdown": breakdown},
            },
        )
        adapter = FakeAdapter([_response_for(_passing_decision())])
        return verify(enquiry, _plan(*tools), trace, record, adapter)

    def test_e01_no_rush_at_all_low_passes(self) -> None:
        # Manual E01-style support path: "No rush at all" → urgency=Exploratory SUPPORTED.
        decision = self._verify(
            "Just browsing — maybe a small cask under £5,000 someday. No rush at all.",
            urgency="Exploratory",
            budget_band="A",
            name="Priya Nair",
            email="priya.nair@example.co.uk",
            country="United Kingdom",
        )
        assert decision.passed is True
        assert decision.fabrication_detected is False
        prompt = verifier_prompt.VERIFIER_SYSTEM_PROMPT
        assert "No rush at all" in prompt
        assert "SUPPORTED" in prompt

    def test_e02_next_few_months_not_urgent_low_passes(self) -> None:
        enquiry = (
            "Hi — I'm Noah Berger from Toronto, Canada. "
            "Contact: noah.berger@example.ca / +1 416 555 7721.\n\n"
            "Interested in a mid-range cask, roughly CAD 25–40k, "
            "sometime in the next few months (not urgent).\n\nThanks"
        )
        decision = self._verify(
            enquiry,
            urgency="Exploratory",
            budget_band="B",
            name="Noah Berger",
            email="noah.berger@example.ca",
            country="Canada",
        )
        assert decision.passed is True
        assert decision.fabrication_detected is False
        prompt = verifier_prompt.VERIFIER_SYSTEM_PROMPT
        assert "next few months (not urgent)" in prompt
        assert "Within three months` may also be reasonable" in prompt

    def test_e10_not_urgent_low_passes(self) -> None:
        enquiry = (
            "Hi, I'm Daniel Okonkwo calling from Lagos. My phone is +234 801 555 0199. "
            "I am interested in a medium-budget whisky cask, no email address — "
            "please use SMS only. Not urgent."
        )
        decision = self._verify(
            enquiry,
            urgency="Exploratory",
            budget_band="C",
            name="Daniel Okonkwo",
            email="unused@example.com",
            country="Nigeria",
        )
        assert decision.passed is True
        assert decision.fabrication_detected is False
        assert '"Not urgent"' in verifier_prompt.VERIFIER_SYSTEM_PROMPT

    def test_prompt_requires_structured_verdicts_and_contradiction_only_fabricates(self) -> None:
        prompt = verifier_prompt.VERIFIER_SYSTEM_PROMPT
        assert "extracted_field_verdicts" in prompt
        assert "INSUFFICIENT_EVIDENCE" in prompt
        assert "fabricated_fields" in prompt
        user = verifier_prompt.build_verifier_user_prompt("x", {"steps": []}, {"calls": []}, {})
        assert "Only CONTRADICTED may appear in fabricated_fields" in user

    def test_e06_band_b_for_95k_remains_contradicted_in_prompt(self) -> None:
        """E06: B for £95k (~USD 121k) is CONTRADICTED by thresholds — not preference."""
        prompt = verifier_prompt.VERIFIER_SYSTEM_PROMPT
        assert "£95,000" in prompt or "95,000" in prompt
        assert "CONTRADICTED" in prompt
        assert "50,000" in prompt or "50k" in prompt
        assert "`B`" in prompt or "`C`" in prompt


class TestUrgencySupportedEnumPasses:
    """Additional supported-urgency pass-through cases."""

    def _verify_urgency(
        self,
        enquiry: str,
        *,
        urgency: str,
        budget_band: str = "Unknown",
    ) -> VerifierDecision:
        parse = {
            "name": "Test Lead",
            "email": "test@example.com",
            "phone": None,
            "country": "United Kingdom",
            "budget_band": budget_band,
            "asset_interest": "whisky casks",
            "urgency": urgency,
        }
        record = {
            "extracted": dict(parse),
            "jurisdiction_rule": dict(LOOKUP_RESULT),
            "score": 15,
            "score_breakdown": {"budget": 0, "urgency": 0, "jurisdiction_risk": 15},
            "dedupe_hash": compute_dedupe_hash(parse["email"], parse["phone"]),
        }
        tools = (
            ToolName.PARSE_ENQUIRY,
            ToolName.LOOKUP_JURISDICTION_RULE,
            ToolName.SCORE_LEAD,
        )
        breakdown = {
            "Exploratory": {"budget": 0, "urgency": 5, "jurisdiction_risk": 15},
            "Within three months": {"budget": 0, "urgency": 15, "jurisdiction_risk": 15},
            "Immediate": {"budget": 0, "urgency": 30, "jurisdiction_risk": 15},
            "Unknown": {"budget": 0, "urgency": 0, "jurisdiction_risk": 15},
        }[urgency]
        record["score_breakdown"] = breakdown
        record["score"] = sum(breakdown.values())
        trace = _trace(
            *tools,
            results={
                ToolName.PARSE_ENQUIRY: parse,
                ToolName.LOOKUP_JURISDICTION_RULE: LOOKUP_RESULT,
                ToolName.SCORE_LEAD: {"score": record["score"], "breakdown": breakdown},
            },
        )
        adapter = FakeAdapter([_response_for(_passing_decision())])
        return verify(enquiry, _plan(*tools), trace, record, adapter)

    def test_not_urgent_to_low_passes(self) -> None:
        decision = self._verify_urgency("Please call when you can. Not urgent.", urgency="Exploratory")
        assert decision.passed is True
        assert decision.fabrication_detected is False

    def test_no_rush_to_low_passes(self) -> None:
        decision = self._verify_urgency("Interested in a cask. No rush.", urgency="Exploratory")
        assert decision.passed is True
        assert decision.fabrication_detected is False

    def test_someday_to_low_passes(self) -> None:
        decision = self._verify_urgency("Maybe a cask someday.", urgency="Exploratory")
        assert decision.passed is True
        assert decision.fabrication_detected is False

    def test_asap_to_high_passes(self) -> None:
        decision = self._verify_urgency("Need docs ASAP for the cask allocation.", urgency="Immediate")
        assert decision.passed is True
        assert decision.fabrication_detected is False

    def test_within_30_days_medium_passes(self) -> None:
        decision = self._verify_urgency(
            "I'd like to place funds within 30 days.", urgency="Within three months"
        )
        assert decision.passed is True
        assert decision.fabrication_detected is False
        assert "Within 30 days" in verifier_prompt.VERIFIER_SYSTEM_PROMPT or (
            "within 30 days" in verifier_prompt.VERIFIER_SYSTEM_PROMPT
        )


class TestPlannerPlaceholdersDoNotDriveFabrication:
    """Regression for run 5c056b3d…: planner args like budget_band=Unknown
    must not cause fabrication when parse_enquiry / lookup match final_record.
    """

    def _priya_shaped_plan(self) -> Plan:
        return Plan(
            steps=[
                PlanStep(
                    step=1,
                    tool=ToolName.PARSE_ENQUIRY,
                    args={"enquiry_text": ENQUIRY_TEXT},
                    rationale="extract",
                ),
                PlanStep(
                    step=2,
                    tool=ToolName.LOOKUP_JURISDICTION_RULE,
                    args={"country": "UK"},
                    rationale="lookup",
                ),
                PlanStep(
                    step=3,
                    tool=ToolName.SCORE_LEAD,
                    args={
                        "extracted": {
                            "name": "Jane Doe",
                            "email": "jane@example.com",
                            "budget_band": "Unknown",
                            "urgency": "Unknown",
                            "country": "UK",
                        },
                        "jurisdiction_rule": {
                            "country": "UK",
                            "requires_disclaimer": False,
                            "restricted": False,
                            "handling_note": "",
                        },
                    },
                    rationale="score",
                ),
            ]
        )

    def test_planner_unknown_budget_but_parse_low_passes(self) -> None:
        parse = {**PARSE_RESULT, "budget_band": "A", "urgency": "Exploratory"}
        record = {
            "extracted": dict(parse),
            "jurisdiction_rule": dict(LOOKUP_RESULT),
            "score": 30,
            "score_breakdown": {"budget": 10, "urgency": 5, "jurisdiction_risk": 15},
            "dedupe_hash": compute_dedupe_hash(parse["email"], parse["phone"]),
        }
        tools = (
            ToolName.PARSE_ENQUIRY,
            ToolName.LOOKUP_JURISDICTION_RULE,
            ToolName.SCORE_LEAD,
        )
        trace = _trace(
            *tools,
            results={
                ToolName.PARSE_ENQUIRY: parse,
                ToolName.LOOKUP_JURISDICTION_RULE: LOOKUP_RESULT,
                ToolName.SCORE_LEAD: {"score": 30, "breakdown": record["score_breakdown"]},
            },
        )
        adapter = FakeAdapter([_response_for(_passing_decision())])
        decision = verify(ENQUIRY_TEXT, self._priya_shaped_plan(), trace, record, adapter)
        assert decision.passed is True
        assert decision.fabrication_detected is False
        user_prompt = adapter.requests[0].user_prompt
        assert '"budget_band": "Unknown"' not in user_prompt
        assert '"urgency": "Unknown"' not in user_prompt
        # Successful parse values remain visible in the trace section.
        assert '"budget_band": "A"' in user_prompt

    def test_planner_placeholder_jurisdiction_ignored_when_lookup_matches_final(self) -> None:
        tools = (
            ToolName.PARSE_ENQUIRY,
            ToolName.LOOKUP_JURISDICTION_RULE,
            ToolName.SCORE_LEAD,
        )
        record = {
            "extracted": dict(PARSE_RESULT),
            "jurisdiction_rule": dict(LOOKUP_RESULT),
            "score": SCORE_RESULT["score"],
            "score_breakdown": dict(SCORE_RESULT["breakdown"]),
            "dedupe_hash": WRITE_RESULT["dedupe_hash"],
        }
        adapter = FakeAdapter(
            [
                _response_for(
                    _passing_decision(
                        passed=False,
                        fabrication_detected=True,
                        fabricated_fields=["jurisdiction_rule"],
                        reason="planner jurisdiction stub disagreed",
                    )
                )
            ]
        )
        decision = verify(
            ENQUIRY_TEXT,
            self._priya_shaped_plan(),
            _trace(*tools),
            record,
            adapter,
        )
        assert decision.passed is True
        assert decision.fabrication_detected is False
        assert decision.fabricated_fields == []

    def test_actual_parse_enquiry_mismatch_still_fails(self) -> None:
        altered = {
            **FINAL_RECORD,
            "extracted": {**PARSE_RESULT, "budget_band": "A"},
        }
        adapter = FakeAdapter([_response_for(_passing_decision())])
        decision = verify(ENQUIRY_TEXT, _plan(*CANONICAL_TOOLS), _trace(*CANONICAL_TOOLS), altered, adapter)
        assert decision.passed is False
        assert decision.fabrication_detected is True
        assert "budget_band" in decision.fabricated_fields

    def test_actual_tool_derived_mismatch_still_fails(self) -> None:
        altered = {
            **FINAL_RECORD,
            "jurisdiction_rule": {**LOOKUP_RESULT, "handling_note": "tampered"},
        }
        adapter = FakeAdapter([_response_for(_passing_decision())])
        decision = verify(ENQUIRY_TEXT, _plan(*CANONICAL_TOOLS), _trace(*CANONICAL_TOOLS), altered, adapter)
        assert decision.passed is False
        assert decision.fabrication_detected is True
        fields = decision.fabricated_fields
        assert "jurisdiction_rule" in fields or "handling_note" in fields


class TestDerivedFieldFabricationDetection:
    """Deterministic derived-field checks (architecture: tool outputs are
    valid evidence; altering them is fabrication)."""

    def test_pass_when_handling_note_matches_lookup(self) -> None:
        assert _detect_derived_field_fabrication(_trace(*CANONICAL_TOOLS), FINAL_RECORD) == []

    def test_pass_when_score_matches_score_lead(self) -> None:
        assert "score" not in _detect_derived_field_fabrication(_trace(*CANONICAL_TOOLS), FINAL_RECORD)

    def test_pass_when_jurisdiction_rule_matches_lookup(self) -> None:
        assert "jurisdiction_rule" not in _detect_derived_field_fabrication(
            _trace(*CANONICAL_TOOLS), FINAL_RECORD
        )

    def test_fail_when_executor_changes_handling_note(self) -> None:
        altered = {
            **FINAL_RECORD,
            "jurisdiction_rule": {
                **LOOKUP_RESULT,
                "handling_note": "INVENTED NOTE NOT FROM TOOL",
            },
        }
        fields = _detect_derived_field_fabrication(_trace(*CANONICAL_TOOLS), altered)
        assert "handling_note" in fields or "jurisdiction_rule" in fields

    def test_fail_when_executor_changes_score(self) -> None:
        altered = {**FINAL_RECORD, "score": 1}
        assert "score" in _detect_derived_field_fabrication(_trace(*CANONICAL_TOOLS), altered)

    def test_fail_when_score_invented_without_score_lead_output(self) -> None:
        tools = (ToolName.PARSE_ENQUIRY, ToolName.LOOKUP_JURISDICTION_RULE)
        record = {
            "extracted": dict(PARSE_RESULT),
            "jurisdiction_rule": dict(LOOKUP_RESULT),
            "score": 99,
            "score_breakdown": {"budget": 99},
            "dedupe_hash": WRITE_RESULT["dedupe_hash"],
        }
        fields = _detect_derived_field_fabrication(_trace(*tools), record)
        assert "score" in fields

    def test_verify_overrides_pass_when_handling_note_tampered(self) -> None:
        altered = {
            **FINAL_RECORD,
            "jurisdiction_rule": {**LOOKUP_RESULT, "handling_note": "tampered"},
        }
        adapter = FakeAdapter([_response_for(_passing_decision())])
        decision = verify(ENQUIRY_TEXT, _plan(*CANONICAL_TOOLS), _trace(*CANONICAL_TOOLS), altered, adapter)
        assert decision.passed is False
        assert decision.fabrication_detected is True
        fields = decision.fabricated_fields
        assert "jurisdiction_rule" in fields or "handling_note" in fields

    def test_verify_passes_when_derived_fields_match_and_model_passes(self) -> None:
        adapter = FakeAdapter([_response_for(_passing_decision())])
        decision = verify(
            ENQUIRY_TEXT, _plan(*CANONICAL_TOOLS), _trace(*CANONICAL_TOOLS), FINAL_RECORD, adapter
        )
        assert decision.passed is True
        assert decision.fabrication_detected is False


class TestExtractedFieldFabricationIsLeftToModel:
    """Extracted-field fabrication remains an LLM judgment against the
    enquiry; the deterministic layer must not invent it. Regression tests
    document the expected model contract via the prompt + a scripted fail."""

    def test_model_fabricated_email_is_returned(self) -> None:
        adapter = FakeAdapter(
            [
                _response_for(
                    _passing_decision(
                        passed=False,
                        fabrication_detected=True,
                        fabricated_fields=["email"],
                        reason="email not in enquiry",
                    )
                )
            ]
        )
        decision = verify(
            ENQUIRY_TEXT, _plan(*CANONICAL_TOOLS), _trace(*CANONICAL_TOOLS), FINAL_RECORD, adapter
        )
        assert decision.fabrication_detected is True
        assert "email" in decision.fabricated_fields

    def test_model_fabricated_country_is_returned(self) -> None:
        adapter = FakeAdapter(
            [
                _response_for(
                    _passing_decision(
                        passed=False,
                        fabrication_detected=True,
                        fabricated_fields=["country"],
                        reason="country not in enquiry",
                    )
                )
            ]
        )
        decision = verify(
            ENQUIRY_TEXT, _plan(*CANONICAL_TOOLS), _trace(*CANONICAL_TOOLS), FINAL_RECORD, adapter
        )
        assert "country" in decision.fabricated_fields


class TestSelfContradictionUnitChecks:
    """Direct unit tests for the deterministic consistency validator's
    text-matching helpers, independent of the full `verify()` pipeline."""

    def test_example_a_budget_high_restated_in_justification(self) -> None:
        reason = (
            'budget_band "C" is CONTRADICTED because AUD 120,000 clearly falls '
            "into the high category."
        )
        assert _reason_self_contradicts_claim(reason, "budget_band", "C") is True

    def test_example_b_urgency_low_explicitly_supported_and_fabricated(self) -> None:
        reason = 'urgency "Exploratory" is supported by "No rush at all" but is fabricated.'
        assert _reason_self_contradicts_claim(reason, "urgency", "Exploratory") is True

    def test_example_c_urgency_high_justified_by_high_signal_phrase(self) -> None:
        reason = (
            'urgency "Immediate" is contradicted because the enquiry says '
            '"please call me urgently."'
        )
        assert _reason_self_contradicts_claim(reason, "urgency", "Immediate") is True

    def test_genuine_contradiction_with_opposing_signal_is_not_cleared(self) -> None:
        reason = (
            'urgency "Immediate" is CONTRADICTED because the enquiry clearly states '
            '"not urgent," so high is fabricated.'
        )
        assert _reason_self_contradicts_claim(reason, "urgency", "Immediate") is False

    def test_unrelated_field_reason_does_not_match(self) -> None:
        reason = 'country "Singapore" does not appear anywhere in the enquiry text.'
        assert _reason_self_contradicts_claim(reason, "urgency", "Exploratory") is False

    def test_strip_clears_only_the_self_contradictory_field(self) -> None:
        decision = _passing_decision(
            passed=False,
            fabrication_detected=True,
            fabricated_fields=["urgency", "email"],
            reason=(
                'urgency "Exploratory" is supported by "Not urgent" but is fabricated. '
                "email does not appear anywhere in the enquiry."
            ),
        )
        cleaned, warnings = _strip_self_contradictory_claims(
            decision, {"extracted": {"urgency": "Exploratory", "email": "x@example.com"}}
        )
        assert cleaned.fabricated_fields == ["email"]
        assert cleaned.fabrication_detected is True
        assert cleaned.passed is False
        assert len(warnings) == 1
        assert "urgency" in warnings[0]

    def test_strip_is_noop_when_no_contradiction_present(self) -> None:
        decision = _passing_decision(
            passed=False,
            fabrication_detected=True,
            fabricated_fields=["email"],
            reason="email not in enquiry",
        )
        cleaned, warnings = _strip_self_contradictory_claims(
            decision, {"extracted": {"email": "x@example.com"}}
        )
        assert cleaned is decision
        assert warnings == []


class TestVerifierConsistencyValidatorEndToEnd:
    """Regression tests for the new deterministic post-verifier consistency
    validator: a self-contradictory LLM decision (the reason affirms the
    same value it lists as fabricated) must never quarantine an otherwise
    correct run. Covers E01, E02, E10, and the "Olivia Hart" (AUD 120,000)
    scenario from the reported failure modes.
    """

    def _record_and_trace(
        self,
        *,
        urgency: str,
        budget_band: str,
        name: str,
        email: str,
        phone: str | None,
        country: str,
    ) -> tuple[dict, ToolCallTrace, tuple[ToolName, ...]]:
        parse = {
            "name": name,
            "email": email,
            "phone": phone,
            "country": country,
            "budget_band": budget_band,
            "asset_interest": "whisky cask portfolio",
            "urgency": urgency,
        }
        record = {
            "extracted": dict(parse),
            "jurisdiction_rule": dict(LOOKUP_RESULT),
            "score": 90,
            "score_breakdown": {"budget": 40, "urgency": 30, "jurisdiction_risk": 20},
            "dedupe_hash": compute_dedupe_hash(parse["email"], parse["phone"]),
        }
        tools = (
            ToolName.PARSE_ENQUIRY,
            ToolName.LOOKUP_JURISDICTION_RULE,
            ToolName.SCORE_LEAD,
        )
        trace = _trace(
            *tools,
            results={
                ToolName.PARSE_ENQUIRY: parse,
                ToolName.LOOKUP_JURISDICTION_RULE: LOOKUP_RESULT,
                ToolName.SCORE_LEAD: {
                    "score": record["score"],
                    "breakdown": record["score_breakdown"],
                },
            },
        )
        return record, trace, tools

    def _verify(
        self,
        enquiry: str,
        decision: VerifierDecision,
        *,
        urgency: str,
        budget_band: str,
        name: str = "Test Lead",
        email: str = "test@example.com",
        phone: str | None = "+1 555 000 0000",
        country: str = "United Kingdom",
    ) -> VerifierDecision:
        record, trace, tools = self._record_and_trace(
            urgency=urgency,
            budget_band=budget_band,
            name=name,
            email=email,
            phone=phone,
            country=country,
        )
        adapter = FakeAdapter([_response_for(decision)])
        return verify(enquiry, _plan(*tools), trace, record, adapter)

    def test_olivia_hart_aud_120k_budget_contradiction_is_discarded(self) -> None:
        """Example A: budget_band 'high' is CONTRADICTED because AUD 120,000
        clearly falls into the high category -- self-contradictory."""
        decision = _passing_decision(
            passed=False,
            fabrication_detected=True,
            fabricated_fields=["budget_band"],
            reason=(
                'budget_band "C" is CONTRADICTED because AUD 120,000 clearly '
                "falls into the high category."
            ),
        )
        result = self._verify(
            "I want to buy a premium Scotch whisky cask portfolio around "
            "AUD 120,000 this month — please call me urgently.",
            decision,
            urgency="Immediate",
            budget_band="C",
            name="Olivia Hart",
            email="olivia.hart@example.com",
            phone="+61 412 555 018",
            country="Australia",
        )
        assert result.passed is True
        assert result.fabrication_detected is False
        assert result.fabricated_fields == []

    def test_olivia_hart_urgency_contradiction_is_discarded(self) -> None:
        """Example C: urgency 'high' is contradicted because the enquiry
        says 'please call me urgently' -- the justification quotes a
        known high-urgency signal for the exact value it disputes."""
        decision = _passing_decision(
            passed=False,
            fabrication_detected=True,
            fabricated_fields=["urgency"],
            reason=(
                'urgency "Immediate" is contradicted because the enquiry says '
                '"please call me urgently."'
            ),
        )
        result = self._verify(
            "I want to buy a premium Scotch whisky cask portfolio around "
            "AUD 120,000 this month — please call me urgently.",
            decision,
            urgency="Immediate",
            budget_band="C",
            name="Olivia Hart",
            email="olivia.hart@example.com",
            phone="+61 412 555 018",
            country="Australia",
        )
        assert result.passed is True
        assert result.fabrication_detected is False
        assert result.fabricated_fields == []

    def test_olivia_hart_both_fields_contradicted_still_passes(self) -> None:
        """Both Example A and Example C reported on the same enquiry."""
        decision = _passing_decision(
            passed=False,
            fabrication_detected=True,
            fabricated_fields=["budget_band", "urgency"],
            reason=(
                'budget_band "C" is CONTRADICTED because AUD 120,000 clearly '
                'falls into the C category. urgency "Immediate" is contradicted '
                'because the enquiry says "please call me urgently."'
            ),
        )
        result = self._verify(
            "I want to buy a premium Scotch whisky cask portfolio around "
            "AUD 120,000 this month — please call me urgently.",
            decision,
            urgency="Immediate",
            budget_band="C",
            name="Olivia Hart",
            email="olivia.hart@example.com",
            phone="+61 412 555 018",
            country="Australia",
        )
        assert result.passed is True
        assert result.fabrication_detected is False
        assert result.fabricated_fields == []

    def test_e01_no_rush_at_all_self_contradiction_discarded(self) -> None:
        """Example B: urgency 'low' is supported by 'No rush at all' but is
        fabricated -- explicit affirm-then-fabricate contradiction."""
        decision = _passing_decision(
            passed=False,
            fabrication_detected=True,
            fabricated_fields=["urgency"],
            reason='urgency "Exploratory" is supported by "No rush at all" but is fabricated.',
        )
        result = self._verify(
            "Just browsing — maybe a small cask under £5,000 someday. No rush at all.",
            decision,
            urgency="Exploratory",
            budget_band="A",
            name="Priya Nair",
            email="priya.nair@example.co.uk",
            phone=None,
            country="United Kingdom",
        )
        assert result.passed is True
        assert result.fabrication_detected is False
        assert result.fabricated_fields == []

    def test_e02_next_few_months_not_urgent_self_contradiction_discarded(self) -> None:
        decision = _passing_decision(
            passed=False,
            fabrication_detected=True,
            fabricated_fields=["urgency"],
            reason=(
                'urgency "Exploratory" is supported by "not urgent" but flagged as '
                "fabricated since Within three months also seemed plausible."
            ),
        )
        result = self._verify(
            "Hi — I'm Noah Berger from Toronto, Canada. "
            "Contact: noah.berger@example.ca / +1 416 555 7721.\n\n"
            "Interested in a mid-range cask, roughly CAD 25–40k, "
            "sometime in the next few months (not urgent).\n\nThanks",
            decision,
            urgency="Exploratory",
            budget_band="B",
            name="Noah Berger",
            email="noah.berger@example.ca",
            phone="+1 416 555 7721",
            country="Canada",
        )
        assert result.passed is True
        assert result.fabrication_detected is False
        assert result.fabricated_fields == []

    def test_e10_not_urgent_self_contradiction_discarded(self) -> None:
        decision = _passing_decision(
            passed=False,
            fabrication_detected=True,
            fabricated_fields=["urgency"],
            reason='urgency "Exploratory" is supported by "Not urgent" yet listed as fabricated.',
        )
        result = self._verify(
            "Hi, I'm Daniel Okonkwo calling from Lagos. My phone is "
            "+234 801 555 0199. I am interested in a medium-budget whisky "
            "cask, no email address — please use SMS only. Not urgent.",
            decision,
            urgency="Exploratory",
            budget_band="C",
            name="Daniel Okonkwo",
            email="unused@example.com",
            phone="+234 801 555 0199",
            country="Nigeria",
        )
        assert result.passed is True
        assert result.fabrication_detected is False
        assert result.fabricated_fields == []

    def test_genuine_fabrication_is_still_caught_when_not_self_contradictory(self) -> None:
        """Sanity control: an ordinary, non-contradictory fabrication claim
        (no affirming language, no restated value/signal) must still fail --
        the validator must not blanket-clear every claim."""
        decision = _passing_decision(
            passed=False,
            fabrication_detected=True,
            fabricated_fields=["email"],
            reason="email does not appear anywhere in the enquiry text.",
        )
        result = self._verify(
            "Interested in a whisky cask, no contact details given.",
            decision,
            urgency="Unknown",
            budget_band="Unknown",
            email="fabricated@example.com",
        )
        assert result.passed is False
        assert result.fabrication_detected is True
        assert "email" in result.fabricated_fields

    def test_deterministic_derived_mismatch_still_fails_after_consistency_clear(self) -> None:
        """A self-contradictory extracted-field claim gets cleared, but a
        genuine deterministic tool-derived mismatch present in the same run
        must still fail the run."""
        record, trace, tools = self._record_and_trace(
            urgency="Exploratory",
            budget_band="A",
            name="Priya Nair",
            email="priya.nair@example.co.uk",
            phone=None,
            country="United Kingdom",
        )
        record["score"] = 999  # deliberately wrong vs. score_lead tool output
        decision = _passing_decision(
            passed=False,
            fabrication_detected=True,
            fabricated_fields=["urgency"],
            reason='urgency "Exploratory" is supported by "No rush at all" but is fabricated.',
        )
        adapter = FakeAdapter([_response_for(decision)])
        result = verify(
            "No rush at all, just browsing.", _plan(*tools), trace, record, adapter
        )
        assert result.passed is False
        assert result.fabrication_detected is True
        assert "score" in result.fabricated_fields
        assert "urgency" not in result.fabricated_fields


class TestExtractedContradictionGateArchitecture:
    """Architectural fix: structured verdicts + deterministic closed-enum
    support. A Verifier LLM that 'changes its mind' and lists a supported
    enum as fabricated must never quarantine the run.
    """

    def _verify_hostile(
        self,
        enquiry: str,
        *,
        urgency: str,
        budget_band: str,
        hostile_fields: list[str],
        hostile_reason: str,
        verdicts: list[ExtractedFieldVerdict] | None = None,
        name: str = "Test Lead",
        email: str | None = "test@example.com",
        phone: str | None = "+1 555 000 0000",
        country: str = "United Kingdom",
    ) -> VerifierDecision:
        parse = {
            "name": name,
            "email": email,
            "phone": phone,
            "country": country,
            "budget_band": budget_band,
            "asset_interest": "whisky casks",
            "urgency": urgency,
        }
        record = {
            "extracted": dict(parse),
            "jurisdiction_rule": dict(LOOKUP_RESULT),
            "score": 55,
            "score_breakdown": {"budget": 25, "urgency": 15, "jurisdiction_risk": 15},
            "dedupe_hash": compute_dedupe_hash(parse["email"], parse["phone"]),
        }
        tools = (
            ToolName.PARSE_ENQUIRY,
            ToolName.LOOKUP_JURISDICTION_RULE,
            ToolName.SCORE_LEAD,
        )
        trace = _trace(
            *tools,
            results={
                ToolName.PARSE_ENQUIRY: parse,
                ToolName.LOOKUP_JURISDICTION_RULE: LOOKUP_RESULT,
                ToolName.SCORE_LEAD: {
                    "score": record["score"],
                    "breakdown": record["score_breakdown"],
                },
            },
        )
        decision = _passing_decision(
            passed=False,
            fabrication_detected=True,
            fabricated_fields=list(hostile_fields),
            extracted_field_verdicts=verdicts or [],
            reason=hostile_reason,
        )
        adapter = FakeAdapter([_response_for(decision)])
        return verify(enquiry, _plan(*tools), trace, record, adapter)

    # --- Explicit regression phrases (must never fail again) ---------------

    def test_under_5k_low_never_quarantined(self) -> None:
        result = self._verify_hostile(
            "Just browsing — maybe a small cask under £5,000 someday. No rush at all.",
            urgency="Exploratory",
            budget_band="A",
            hostile_fields=["budget_band", "urgency"],
            hostile_reason="budget_band A and urgency Exploratory are fabricated",
        )
        assert result.passed is True
        assert result.fabrication_detected is False

    def test_no_rush_low_never_quarantined(self) -> None:
        result = self._verify_hostile(
            "Interested in a cask. No rush.",
            urgency="Exploratory",
            budget_band="Unknown",
            hostile_fields=["urgency"],
            hostile_reason='urgency "Exploratory" is supported by "No rush" therefore fabricated.',
            verdicts=[
                ExtractedFieldVerdict(
                    field="urgency",
                    label=ExtractedFieldLabel.CONTRADICTED,
                    note="prefers Within three months",
                )
            ],
        )
        assert result.passed is True
        assert "urgency" not in result.fabricated_fields

    def test_not_urgent_low_never_quarantined(self) -> None:
        result = self._verify_hostile(
            "Medium-budget whisky cask. Not urgent.",
            urgency="Exploratory",
            budget_band="C",
            hostile_fields=["urgency"],
            hostile_reason="urgency Exploratory is fabricated",
        )
        assert result.passed is True

    def test_just_browsing_low_never_quarantined(self) -> None:
        result = self._verify_hostile(
            "just browsing for now",
            urgency="Exploratory",
            budget_band="Unknown",
            hostile_fields=["urgency"],
            hostile_reason="fabricated urgency",
        )
        assert result.passed is True

    def test_this_month_high_never_quarantined(self) -> None:
        result = self._verify_hostile(
            "Ready to buy this month.",
            urgency="Immediate",
            budget_band="Unknown",
            hostile_fields=["urgency"],
            hostile_reason="urgency high contradicted",
        )
        assert result.passed is True

    def test_urgently_high_never_quarantined(self) -> None:
        result = self._verify_hostile(
            "Please call me urgently this month.",
            urgency="Immediate",
            budget_band="Unknown",
            hostile_fields=["urgency"],
            hostile_reason=(
                'urgency "Immediate" is contradicted because the enquiry says '
                '"please call me urgently."'
            ),
        )
        assert result.passed is True

    def test_aud_120k_high_never_quarantined(self) -> None:
        result = self._verify_hostile(
            "around AUD 120,000 for a premium portfolio",
            urgency="Unknown",
            budget_band="C",
            hostile_fields=["budget_band"],
            hostile_reason=(
                'budget_band "C" is CONTRADICTED because AUD120k is high'
            ),
            verdicts=[
                ExtractedFieldVerdict(
                    field="budget_band",
                    label=ExtractedFieldLabel.CONTRADICTED,
                    note="AUD120k is high",
                )
            ],
            name="Olivia Hart",
            email="olivia.hart@example.com",
            phone="+61 412 555 018",
            country="Australia",
        )
        assert result.passed is True
        assert result.fabricated_fields == []

    def test_95k_high_never_quarantined(self) -> None:
        result = self._verify_hostile(
            "approximately £95,000 into a cask allocation within 30 days.",
            urgency="Immediate",
            budget_band="C",
            hostile_fields=["budget_band", "urgency"],
            hostile_reason="both fabricated",
        )
        assert result.passed is True

    def test_next_few_months_not_urgent_low_never_quarantined(self) -> None:
        result = self._verify_hostile(
            "CAD 25–40k, sometime in the next few months (not urgent).",
            urgency="Exploratory",
            budget_band="B",
            hostile_fields=["urgency"],
            hostile_reason="prefer medium; low fabricated",
            verdicts=[
                ExtractedFieldVerdict(
                    field="urgency",
                    label=ExtractedFieldLabel.CONTRADICTED,
                    note="should be Within three months",
                )
            ],
        )
        assert result.passed is True

    def test_within_30_days_medium_never_quarantined(self) -> None:
        result = self._verify_hostile(
            "I'd like to place funds within 30 days.",
            urgency="Within three months",
            budget_band="Unknown",
            hostile_fields=["urgency"],
            hostile_reason="should have been high",
        )
        assert result.passed is True

    # --- Manual E01–E14 shapes: hostile verifier cannot quarantine ---------

    def test_e01_olivia_hart_hostile_verifier_passes(self) -> None:
        enquiry = (
            "Hello,\n\nMy name is Olivia Hart. Email olivia.hart@example.com, "
            "phone +61 412 555 018. I am based in Australia.\n\n"
            "I want to buy a premium Scotch whisky cask portfolio around "
            "AUD 120,000 this month — please call me urgently.\n\nRegards,\nOlivia"
        )
        result = self._verify_hostile(
            enquiry,
            urgency="Immediate",
            budget_band="C",
            hostile_fields=["budget_band", "urgency"],
            hostile_reason=(
                'budget_band "C" is CONTRADICTED because AUD 120,000 clearly '
                'falls into the C category. urgency "Immediate" is contradicted '
                'because the enquiry says "please call me urgently."'
            ),
            name="Olivia Hart",
            email="olivia.hart@example.com",
            phone="+61 412 555 018",
            country="Australia",
        )
        assert result.passed is True
        assert result.fabrication_detected is False

    def test_e02_noah_berger_hostile_verifier_passes(self) -> None:
        enquiry = (
            "Hi — I'm Noah Berger from Toronto, Canada. Contact: "
            "noah.berger@example.ca / +1 416 555 7721.\n\n"
            "Interested in a mid-range cask, roughly CAD 25–40k, sometime in "
            "the next few months (not urgent).\n\nThanks"
        )
        result = self._verify_hostile(
            enquiry,
            urgency="Exploratory",
            budget_band="B",
            hostile_fields=["urgency", "budget_band"],
            hostile_reason="prefer different bands",
            name="Noah Berger",
            email="noah.berger@example.ca",
            phone="+1 416 555 7721",
            country="Canada",
        )
        assert result.passed is True

    def test_e03_priya_nair_hostile_verifier_passes(self) -> None:
        enquiry = (
            "Hello, I'm Priya Nair in the United Kingdom. "
            "priya.nair@example.co.uk, +44 7700 900123.\n\n"
            "Just browsing — maybe a small cask under £5,000 someday. "
            "No rush at all.\n\nCheers"
        )
        result = self._verify_hostile(
            enquiry,
            urgency="Exploratory",
            budget_band="A",
            hostile_fields=["urgency", "budget_band"],
            hostile_reason="low is supported therefore fabricated",
            name="Priya Nair",
            email="priya.nair@example.co.uk",
            phone="+44 7700 900123",
            country="United Kingdom",
        )
        assert result.passed is True

    def test_e04_marcus_webb_hostile_verifier_passes(self) -> None:
        enquiry = (
            "G'day, Marcus Webb here in Melbourne, Australia. "
            "marcus.webb@example.com.au / +61 398 555 441.\n"
            "Looking at a single cask around AUD 55k over the next quarter."
        )
        result = self._verify_hostile(
            enquiry,
            urgency="Within three months",
            budget_band="B",
            hostile_fields=["urgency", "budget_band"],
            hostile_reason="reclassified",
            name="Marcus Webb",
            email="marcus.webb@example.com.au",
            phone="+61 398 555 441",
            country="Australia",
        )
        assert result.passed is True

    def test_e05_sophie_tremblay_hostile_verifier_passes(self) -> None:
        enquiry = (
            "Bonjour, Sophie Tremblay, Montreal, Canada. "
            "sophie.tremblay@example.ca, +1 514 555 0199.\n"
            "Interested in investing about CAD 80,000 in bonded Scotch casks soon."
        )
        result = self._verify_hostile(
            enquiry,
            urgency="Within three months",
            budget_band="C",
            hostile_fields=["budget_band", "urgency"],
            hostile_reason="CAD 80k should not be high",
            name="Sophie Tremblay",
            email="sophie.tremblay@example.ca",
            phone="+1 514 555 0199",
            country="Canada",
        )
        assert result.passed is True

    def test_e06_james_whitfield_hostile_verifier_passes(self) -> None:
        enquiry = (
            "Dear team, James Whitfield, London, United Kingdom. "
            "james.whitfield@example.co.uk / +44 20 7946 0958.\n"
            "I'd like to place approximately £95,000 into a cask allocation "
            "within 30 days."
        )
        result = self._verify_hostile(
            enquiry,
            urgency="Immediate",
            budget_band="C",
            hostile_fields=["budget_band", "urgency"],
            hostile_reason="95k is medium; within 30 days is medium",
            name="James Whitfield",
            email="james.whitfield@example.co.uk",
            phone="+44 20 7946 0958",
            country="United Kingdom",
        )
        assert result.passed is True

    def test_e06_medium_for_95k_still_fails_via_deterministic_gate(self) -> None:
        """Real band contradiction must still quarantine (not preference)."""
        enquiry = (
            "I'd like to place approximately £95,000 into a cask allocation "
            "within 30 days."
        )
        result = self._verify_hostile(
            enquiry,
            urgency="Immediate",
            budget_band="B",  # wrong band
            hostile_fields=[],  # LLM missed it — gate must add
            hostile_reason="looks fine",
            name="James Whitfield",
            email="james.whitfield@example.co.uk",
            phone="+44 20 7946 0958",
            country="United Kingdom",
        )
        assert result.passed is False
        assert result.fabrication_detected is True
        assert "budget_band" in result.fabricated_fields

    def test_e07_ava_chen_hostile_verifier_passes(self) -> None:
        enquiry = (
            "Hi, I'm Ava Chen in New York, United States. ava.chen@example.com, "
            "+1 212 555 0144.\nReady to allocate USD 150,000 to whisky casks "
            "this week — please expedite."
        )
        result = self._verify_hostile(
            enquiry,
            urgency="Immediate",
            budget_band="C",
            hostile_fields=["budget_band", "urgency"],
            hostile_reason="fabricated",
            name="Ava Chen",
            email="ava.chen@example.com",
            phone="+1 212 555 0144",
            country="United States",
        )
        assert result.passed is True

    def test_e08_duplicate_contacts_hostile_verifier_passes(self) -> None:
        enquiry = (
            "Hello again — Olivia Hart, olivia.hart@example.com, +61 412 555 018, "
            "Australia. Still interested in the AUD 120k cask portfolio, urgent."
        )
        result = self._verify_hostile(
            enquiry,
            urgency="Immediate",
            budget_band="C",
            hostile_fields=["budget_band", "urgency"],
            hostile_reason="self-contradictory reclassification",
            name="Olivia Hart",
            email="olivia.hart@example.com",
            phone="+61 412 555 018",
            country="Australia",
        )
        assert result.passed is True

    def test_e10_daniel_okonkwo_hostile_verifier_passes(self) -> None:
        enquiry = (
            "Hi, I'm Daniel Okonkwo calling from Lagos. My phone is "
            "+234 801 555 0199. I am interested in a medium-budget whisky cask, "
            "no email address — please use SMS only. Not urgent."
        )
        result = self._verify_hostile(
            enquiry,
            urgency="Exploratory",
            budget_band="C",
            hostile_fields=["urgency"],
            hostile_reason='urgency "Exploratory" is supported by "Not urgent" but fabricated',
            name="Daniel Okonkwo",
            email=None,
            phone="+234 801 555 0199",
            country="Nigeria",
        )
        assert result.passed is True

    def test_e11_lena_ortiz_hostile_verifier_passes(self) -> None:
        enquiry = (
            "Hi, Lena Ortiz, lena.ortiz@example.com, +34 612 555 010, Spain.\n"
            "Looking at roughly AUD 55k for one cask over the next quarter."
        )
        result = self._verify_hostile(
            enquiry,
            urgency="Within three months",
            budget_band="B",
            hostile_fields=["budget_band", "urgency"],
            hostile_reason="prefer high",
            name="Lena Ortiz",
            email="lena.ortiz@example.com",
            phone="+34 612 555 010",
            country="Spain",
        )
        assert result.passed is True

    def test_gate_keeps_free_text_email_fabrication(self) -> None:
        decision = _passing_decision(
            passed=False,
            fabrication_detected=True,
            fabricated_fields=["email"],
            reason="email not in enquiry",
            extracted_field_verdicts=[
                ExtractedFieldVerdict(
                    field="email",
                    label=ExtractedFieldLabel.CONTRADICTED,
                    note="not present",
                )
            ],
        )
        cleaned, _ = _apply_extracted_contradiction_gate(
            decision,
            enquiry_text="Interested in casks, no contact details.",
            final_record={"extracted": {"email": "forged@example.com", "urgency": "unknown", "budget_band": "unknown"}},
        )
        assert "email" in cleaned.fabricated_fields

    def test_supported_structured_verdict_clears_fabricated_list(self) -> None:
        decision = _passing_decision(
            passed=False,
            fabrication_detected=True,
            fabricated_fields=["urgency"],
            reason="whatever",
            extracted_field_verdicts=[
                ExtractedFieldVerdict(
                    field="urgency",
                    label=ExtractedFieldLabel.SUPPORTED,
                    note="No rush at all",
                )
            ],
        )
        cleaned, warnings = _apply_extracted_contradiction_gate(
            decision,
            enquiry_text="No rush at all.",
            final_record={"extracted": {"urgency": "Exploratory", "budget_band": "Unknown"}},
        )
        assert cleaned.fabricated_fields == []
        assert cleaned.passed is True
        assert any("urgency" in w for w in warnings)
