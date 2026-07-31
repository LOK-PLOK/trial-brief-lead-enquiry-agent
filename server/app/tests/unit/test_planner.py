"""Unit tests for agent/planner.py. See docs/contracts.md section 1.

Uses a fake `ModelAdapter` test double throughout -- no real provider is
implemented yet (llm/factory.py), and the Planner must depend only on the
`ModelAdapter` interface (docs/contracts.md section 5), so a fake is exactly
what the contract calls for, not a workaround.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.agent import planner as planner_module
from app.agent.planner import PlannerValidationError, _check_mandatory_tools, build_plan
from app.core.config import Settings
from app.core.logging import run_id_ctx, stage_ctx
from app.llm.base import ModelAdapter, StructuredCompletionRequest, StructuredCompletionResponse
from app.schemas.plan import Plan, PlanStep, ToolName

VALID_MANIFEST = [
    {"name": "parse_enquiry", "description": "...", "args_schema": {}},
    {"name": "lookup_jurisdiction_rule", "description": "...", "args_schema": {}},
    {"name": "score_lead", "description": "...", "args_schema": {}},
    {"name": "write_record", "description": "...", "args_schema": {}},
]


def _valid_plan() -> Plan:
    return Plan(
        steps=[
            PlanStep(step=1, tool=ToolName.PARSE_ENQUIRY, args={}, rationale="Extract fields first."),
            PlanStep(
                step=2,
                tool=ToolName.LOOKUP_JURISDICTION_RULE,
                args={"country": "Singapore"},
                rationale="Need the jurisdiction rule before scoring.",
            ),
            PlanStep(step=3, tool=ToolName.SCORE_LEAD, args={}, rationale="Score the lead."),
            PlanStep(step=4, tool=ToolName.WRITE_RECORD, args={}, rationale="Persist the record."),
        ]
    )


def _plan_missing_score_lead() -> Plan:
    return Plan(
        steps=[
            PlanStep(step=1, tool=ToolName.PARSE_ENQUIRY, args={}, rationale="Extract fields first."),
            PlanStep(
                step=2,
                tool=ToolName.LOOKUP_JURISDICTION_RULE,
                args={"country": "Singapore"},
                rationale="Need the jurisdiction rule.",
            ),
            PlanStep(step=3, tool=ToolName.WRITE_RECORD, args={}, rationale="Persist the record."),
        ]
    )


def _plan_with_duplicate_jurisdiction_lookup() -> Plan:
    return Plan(
        steps=[
            PlanStep(
                step=1,
                tool=ToolName.LOOKUP_JURISDICTION_RULE,
                args={"country": "Singapore"},
                rationale="Lookup once.",
            ),
            PlanStep(
                step=2,
                tool=ToolName.LOOKUP_JURISDICTION_RULE,
                args={"country": "Singapore"},
                rationale="Lookup again (invalid).",
            ),
            PlanStep(step=3, tool=ToolName.SCORE_LEAD, args={}, rationale="Score the lead."),
        ]
    )


def _response_for(plan: Plan, *, model: str = "fake-model") -> StructuredCompletionResponse:
    return StructuredCompletionResponse(
        parsed=plan,
        raw_response={"id": "fake-completion"},
        prompt_tokens=100,
        completion_tokens=40,
        latency_ms=12.5,
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


class TestBuildPlanHappyPath:
    def test_returns_the_valid_plan_on_first_attempt(self) -> None:
        plan = _valid_plan()
        adapter = FakeAdapter([_response_for(plan)])

        result = build_plan("Hi, I'm interested in whisky casks.", VALID_MANIFEST, adapter)

        assert result is plan
        assert len(adapter.requests) == 1

    def test_request_uses_structured_output_with_plan_schema(self) -> None:
        adapter = FakeAdapter([_response_for(_valid_plan())])
        build_plan("enquiry", VALID_MANIFEST, adapter)

        request = adapter.requests[0]
        assert request.response_schema is Plan

    def test_request_includes_enquiry_text_and_manifest_in_user_prompt(self) -> None:
        adapter = FakeAdapter([_response_for(_valid_plan())])
        build_plan("Interested in casks, my email is jane@example.com", VALID_MANIFEST, adapter)

        user_prompt = adapter.requests[0].user_prompt
        assert "jane@example.com" in user_prompt
        assert "lookup_jurisdiction_rule" in user_prompt

    def test_request_carries_no_conversation_history(self) -> None:
        """Every call is a fresh, isolated StructuredCompletionRequest --
        development-rules.md: "Each LLM call is isolated". There is no
        messages/history field at all on the request dataclass."""
        adapter = FakeAdapter([_response_for(_valid_plan())])
        build_plan("enquiry", VALID_MANIFEST, adapter)

        request = adapter.requests[0]
        assert not hasattr(request, "messages")
        assert not hasattr(request, "history")

    def test_model_comes_from_settings_resolved_model_planner(self, monkeypatch) -> None:
        custom_settings = Settings(planner_model="custom-planner-model")
        monkeypatch.setattr(planner_module, "get_settings", lambda: custom_settings)

        adapter = FakeAdapter([_response_for(_valid_plan())])
        build_plan("enquiry", VALID_MANIFEST, adapter)

        assert adapter.requests[0].model == "custom-planner-model"

    def test_falls_back_to_model_name_when_no_planner_override(self, monkeypatch) -> None:
        custom_settings = Settings(model_name="fallback-model")
        monkeypatch.setattr(planner_module, "get_settings", lambda: custom_settings)

        adapter = FakeAdapter([_response_for(_valid_plan())])
        build_plan("enquiry", VALID_MANIFEST, adapter)

        assert adapter.requests[0].model == "fallback-model"


class TestMandatoryToolGuardrail:
    def test_check_mandatory_tools_passes_for_a_valid_plan(self) -> None:
        assert _check_mandatory_tools(_valid_plan()) == []

    def test_check_mandatory_tools_flags_missing_score_lead(self) -> None:
        violations = _check_mandatory_tools(_plan_missing_score_lead())
        assert any("score_lead" in v for v in violations)

    def test_check_mandatory_tools_flags_duplicate_jurisdiction_lookup(self) -> None:
        violations = _check_mandatory_tools(_plan_with_duplicate_jurisdiction_lookup())
        assert any("lookup_jurisdiction_rule" in v for v in violations)

    def test_check_mandatory_tools_can_flag_both_violations_at_once(self) -> None:
        plan = Plan(
            steps=[
                PlanStep(step=1, tool=ToolName.PARSE_ENQUIRY, args={}, rationale="Extract."),
            ]
        )
        violations = _check_mandatory_tools(plan)
        assert len(violations) == 2


class TestBuildPlanRetryOnGuardrailViolation:
    def test_retries_once_and_succeeds_on_second_attempt(self) -> None:
        bad_plan = _plan_missing_score_lead()
        good_plan = _valid_plan()
        adapter = FakeAdapter([_response_for(bad_plan), _response_for(good_plan)])

        result = build_plan("enquiry", VALID_MANIFEST, adapter)

        assert result is good_plan
        assert len(adapter.requests) == 2

    def test_retry_request_includes_correction_feedback(self) -> None:
        bad_plan = _plan_missing_score_lead()
        good_plan = _valid_plan()
        adapter = FakeAdapter([_response_for(bad_plan), _response_for(good_plan)])

        build_plan("enquiry", VALID_MANIFEST, adapter)

        retry_prompt = adapter.requests[1].user_prompt
        assert "CORRECTION REQUIRED" in retry_prompt
        assert "score_lead" in retry_prompt

    def test_retry_request_still_carries_full_original_context(self) -> None:
        """The retry is a fresh, self-contained call, not a continuation --
        it must still include the enquiry text and manifest, not just the
        correction note."""
        bad_plan = _plan_missing_score_lead()
        good_plan = _valid_plan()
        adapter = FakeAdapter([_response_for(bad_plan), _response_for(good_plan)])

        build_plan("Interested in casks, contact: jane@example.com", VALID_MANIFEST, adapter)

        retry_prompt = adapter.requests[1].user_prompt
        assert "jane@example.com" in retry_prompt
        assert "lookup_jurisdiction_rule" in retry_prompt

    def test_retry_request_uses_the_same_system_prompt(self) -> None:
        bad_plan = _plan_missing_score_lead()
        good_plan = _valid_plan()
        adapter = FakeAdapter([_response_for(bad_plan), _response_for(good_plan)])

        build_plan("enquiry", VALID_MANIFEST, adapter)

        first_system_prompt = adapter.requests[0].system_prompt
        second_system_prompt = adapter.requests[1].system_prompt
        assert first_system_prompt == second_system_prompt

    def test_raises_after_guardrail_fails_on_both_attempts(self) -> None:
        adapter = FakeAdapter(
            [
                _response_for(_plan_missing_score_lead()),
                _response_for(_plan_missing_score_lead()),
            ]
        )

        with pytest.raises(PlannerValidationError, match="score_lead"):
            build_plan("enquiry", VALID_MANIFEST, adapter)

        assert len(adapter.requests) == 2

    def test_never_makes_a_third_attempt(self) -> None:
        adapter = FakeAdapter(
            [
                _response_for(_plan_missing_score_lead()),
                _response_for(_plan_missing_score_lead()),
            ]
        )
        with pytest.raises(PlannerValidationError):
            build_plan("enquiry", VALID_MANIFEST, adapter)
        assert len(adapter.requests) == 2  # not 3+


class TestBuildPlanAdapterFailurePropagation:
    def test_adapter_exception_propagates_without_extra_retry(self) -> None:
        class _FakeProviderError(Exception):
            pass

        adapter = FakeAdapter([_FakeProviderError("rate limited")])

        with pytest.raises(_FakeProviderError):
            build_plan("enquiry", VALID_MANIFEST, adapter)

        # No Planner-level retry on top of an adapter-raised failure -- the
        # adapter already exhausted its own internal retry per its contract.
        assert len(adapter.requests) == 1

    def test_wrong_type_from_adapter_raises_without_retry(self) -> None:
        """Defensive validation: even though the adapter contract guarantees
        `parsed` matches `response_schema`, the Planner checks anyway rather
        than trusting the boundary blindly."""
        bogus_response = StructuredCompletionResponse(
            parsed="not-a-plan",  # type: ignore[arg-type]
            raw_response={},
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=1.0,
            model="fake-model",
        )
        adapter = FakeAdapter([bogus_response])

        with pytest.raises(PlannerValidationError, match="expected Plan"):
            build_plan("enquiry", VALID_MANIFEST, adapter)

        assert len(adapter.requests) == 1


class TestBuildPlanLogging:
    def test_logs_prompt_response_latency_and_token_usage(self, caplog) -> None:
        import logging

        plan = _valid_plan()
        adapter = FakeAdapter([_response_for(plan)])

        with caplog.at_level(logging.INFO, logger="app.agent.planner"):
            build_plan("enquiry", VALID_MANIFEST, adapter)

        matching = [r for r in caplog.records if getattr(r, "event", None) == "planner_llm_call"]
        assert len(matching) == 1
        record = matching[0]
        assert record.system_prompt
        assert record.user_prompt
        assert record.response == plan.model_dump(mode="json")
        assert record.latency_ms == 12.5
        assert record.prompt_tokens == 100
        assert record.completion_tokens == 40

    def test_logs_one_entry_per_attempt_including_the_failed_first_attempt(self, caplog) -> None:
        """The first attempt's outcome must still be observable even though
        the retry recovers it (docs/contracts.md section 1: "the first-
        attempt outcome is still counted... regardless of retry success")."""
        import logging

        bad_plan = _plan_missing_score_lead()
        good_plan = _valid_plan()
        adapter = FakeAdapter([_response_for(bad_plan), _response_for(good_plan)])

        with caplog.at_level(logging.INFO, logger="app.agent.planner"):
            build_plan("enquiry", VALID_MANIFEST, adapter)

        call_logs = [r for r in caplog.records if getattr(r, "event", None) == "planner_llm_call"]
        assert len(call_logs) == 2
        assert call_logs[0].attempt == 1
        assert call_logs[0].guardrail_violations != []
        assert call_logs[1].attempt == 2
        assert call_logs[1].guardrail_violations == []

    def test_sets_and_resets_stage_context(self) -> None:
        class _StageCapturingAdapter(ModelAdapter):
            provider_name = "fake"
            captured_stage: str | None = None

            def complete_structured(self, request):
                self.captured_stage = stage_ctx.get()
                return _response_for(_valid_plan())

        adapter = _StageCapturingAdapter()
        assert stage_ctx.get() is None

        build_plan("enquiry", VALID_MANIFEST, adapter)

        assert adapter.captured_stage == "planner"
        assert stage_ctx.get() is None  # reset after build_plan returns

    def test_resets_stage_context_even_when_build_plan_raises(self) -> None:
        class _Boom(Exception):
            pass

        class _RaisingAdapter(ModelAdapter):
            provider_name = "fake"

            def complete_structured(self, request):
                raise _Boom("simulated failure")

        assert stage_ctx.get() is None
        with pytest.raises(_Boom):
            build_plan("enquiry", VALID_MANIFEST, _RaisingAdapter())
        assert stage_ctx.get() is None

    def test_metadata_includes_run_id_from_context_var(self) -> None:
        token = run_id_ctx.set("run-123")
        try:
            adapter = FakeAdapter([_response_for(_valid_plan())])
            build_plan("enquiry", VALID_MANIFEST, adapter)
            assert adapter.requests[0].metadata["run_id"] == "run-123"
        finally:
            run_id_ctx.reset(token)


class TestPlannerNeverExecutesTools:
    """Structural guardrail: the Planner module must not import the
    Executor or the tools package at all (development-rules.md: "Planner
    never executes tools")."""

    def test_planner_module_does_not_import_executor_or_tools(self) -> None:
        source = Path(inspect.getfile(planner_module)).read_text()
        assert "agent.executor" not in source
        assert "app.tools" not in source

    def test_planner_module_has_exactly_one_public_callable(self) -> None:
        public_names = [
            name
            for name, value in vars(planner_module).items()
            if not name.startswith("_")
            and inspect.isfunction(value)
            and value.__module__ == planner_module.__name__
        ]
        assert public_names == ["build_plan"]
