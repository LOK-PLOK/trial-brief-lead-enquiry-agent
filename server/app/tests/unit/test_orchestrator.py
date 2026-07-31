"""Unit tests for agent/orchestrator.py.

`build_plan`, `run_plan`, and `verify` are monkeypatched here so the
Orchestrator's own logic -- run ID generation, stage sequencing, failure
handling, usage aggregation, and persistence -- is tested in isolation from
the real Planner/Executor/Verifier. See tests/integration/
test_orchestrator_integration.py for the full real pipeline exercised
together with fake adapters and mock tools.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from app.agent import orchestrator as orchestrator_module
from app.agent.executor import ExecutionResult
from app.agent.orchestrator import Pipeline, _UsageTrackingAdapter
from app.core.config import Settings
from app.core.logging import run_id_ctx, stage_ctx
from app.db import repository as repository_module
from app.llm.base import ModelAdapter, StructuredCompletionRequest, StructuredCompletionResponse
from app.schemas.extraction import ExtractedFields
from app.schemas.lead_record import LeadRecord
from app.schemas.plan import Plan, PlanStep, ToolName
from app.schemas.tool_trace import ToolCall, ToolCallStatus, ToolCallTrace
from app.schemas.verifier import VerifierDecision

ENQUIRY_TEXT = "Hi, I'm interested in whisky casks."


class _NeverCalledAdapter(ModelAdapter):
    """A `ModelAdapter` that fails the test if it's ever actually invoked --
    used wherever a stage function is fully monkeypatched and shouldn't
    need the adapter."""

    provider_name = "fake"

    def complete_structured(self, request: StructuredCompletionRequest) -> StructuredCompletionResponse:
        raise AssertionError("adapter.complete_structured() should not have been called")


def _valid_plan() -> Plan:
    return Plan(
        steps=[
            PlanStep(step=1, tool=ToolName.PARSE_ENQUIRY, args={}, rationale="Extract fields."),
            PlanStep(step=2, tool=ToolName.LOOKUP_JURISDICTION_RULE, args={}, rationale="Lookup rule."),
            PlanStep(step=3, tool=ToolName.SCORE_LEAD, args={}, rationale="Score."),
            PlanStep(step=4, tool=ToolName.WRITE_RECORD, args={}, rationale="Persist."),
        ]
    )


def _trace_with_one_call(status: str = ToolCallStatus.SUCCESS) -> ToolCallTrace:
    now = datetime.now(UTC)
    return ToolCallTrace(
        calls=[
            ToolCall(
                step=1,
                tool=ToolName.PARSE_ENQUIRY,
                args={},
                status=status,
                result={} if status == ToolCallStatus.SUCCESS else None,
                error=None if status == ToolCallStatus.SUCCESS else "boom",
                latency_ms=1.0,
                started_at=now,
                finished_at=now,
            )
        ]
    )


def _passing_decision(**overrides) -> VerifierDecision:
    defaults = dict(
        passed=True,
        confidence=0.9,
        fabrication_detected=False,
        fabricated_fields=[],
        plan_deviation_detected=False,
        deviation_details=None,
        reason="ok",
    )
    defaults.update(overrides)
    return VerifierDecision(**defaults)


def _final_record() -> LeadRecord:
    return LeadRecord(
        extracted=ExtractedFields(name="Jane"),
        jurisdiction_rule={"country": "Singapore"},
        score=10,
        score_breakdown={},
        dedupe_hash="hash-1",
    )


def _successful_execution(trace: ToolCallTrace | None = None) -> ExecutionResult:
    return ExecutionResult(trace=trace or _trace_with_one_call(), final_record=_final_record(), error=None)


def _failed_execution(error: str = "tool failed", trace: ToolCallTrace | None = None) -> ExecutionResult:
    return ExecutionResult(
        trace=trace or _trace_with_one_call(ToolCallStatus.ERROR), final_record=None, error=error
    )


def _stage_fn(outcome):
    """Builds a stand-in for build_plan/run_plan/verify: raises `outcome`
    if it's an exception, otherwise returns it -- signature-agnostic via
    *args/**kwargs so one helper covers all three call shapes."""

    def _fn(*args, **kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return _fn


@pytest.fixture
def no_op_persistence(monkeypatch):
    """Replaces `repository.create_run` with a no-op recorder so unit tests
    never touch a real database; returns the list of (db, result) calls
    made."""
    calls: list[tuple[object, object]] = []

    def _fake_create_run(db, result, **kwargs):
        calls.append((db, result))

    monkeypatch.setattr(repository_module, "create_run", _fake_create_run)
    return calls


@pytest.fixture
def pipeline() -> Pipeline:
    return Pipeline(adapter=_NeverCalledAdapter(), settings=Settings())


class TestUsageTrackingAdapter:
    def test_records_one_usage_entry_per_call(self) -> None:
        inner = _RecordingInnerAdapter()
        tracking = _UsageTrackingAdapter(inner, is_local=False)

        tracking.complete_structured(_request(stage="planner"))
        tracking.complete_structured(_request(stage="verifier"))

        assert len(tracking.calls) == 2
        assert [c.stage for c in tracking.calls] == ["planner", "verifier"]

    def test_extracts_model_tokens_and_latency_from_the_response(self) -> None:
        inner = _RecordingInnerAdapter(
            model="model-x", prompt_tokens=100, completion_tokens=20, latency_ms=42.0
        )
        tracking = _UsageTrackingAdapter(inner, is_local=False)

        tracking.complete_structured(_request(stage="planner"))

        usage = tracking.calls[0]
        assert usage.model_name == "model-x"
        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 20
        assert usage.latency_ms == 42.0
        assert usage.model_provider == inner.provider_name

    def test_defaults_to_unknown_stage_when_metadata_omits_it(self) -> None:
        inner = _RecordingInnerAdapter()
        tracking = _UsageTrackingAdapter(inner, is_local=False)

        request = StructuredCompletionRequest(
            system_prompt="s", user_prompt="u", response_schema=VerifierDecision, model="m"
        )
        tracking.complete_structured(request)

        assert tracking.calls[0].stage == "unknown"

    def test_is_local_true_reports_zero_cost_regardless_of_pricing_table(self) -> None:
        inner = _RecordingInnerAdapter(model="totally-unpriced-model")
        tracking = _UsageTrackingAdapter(inner, is_local=True)

        tracking.complete_structured(_request(stage="planner"))

        assert tracking.calls[0].cost_usd == 0.0

    def test_delegates_and_returns_the_inner_response_unchanged(self) -> None:
        inner = _RecordingInnerAdapter()
        tracking = _UsageTrackingAdapter(inner, is_local=False)

        response = tracking.complete_structured(_request(stage="planner"))

        assert response is inner.last_response

    def test_provider_name_matches_the_inner_adapter(self) -> None:
        inner = _RecordingInnerAdapter()
        tracking = _UsageTrackingAdapter(inner, is_local=False)
        assert tracking.provider_name == inner.provider_name


class _RecordingInnerAdapter(ModelAdapter):
    provider_name = "fake-provider"

    def __init__(self, *, model="model-x", prompt_tokens=10, completion_tokens=5, latency_ms=1.0) -> None:
        self._model = model
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens
        self._latency_ms = latency_ms
        self.last_response: StructuredCompletionResponse | None = None

    def complete_structured(self, request: StructuredCompletionRequest) -> StructuredCompletionResponse:
        self.last_response = StructuredCompletionResponse(
            parsed=VerifierDecision(
                passed=True,
                confidence=1.0,
                fabrication_detected=False,
                plan_deviation_detected=False,
                reason="",
            ),
            raw_response={},
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            latency_ms=self._latency_ms,
            model=self._model,
        )
        return self.last_response


def _request(*, stage: str) -> StructuredCompletionRequest:
    return StructuredCompletionRequest(
        system_prompt="s",
        user_prompt="u",
        response_schema=VerifierDecision,
        model="m",
        metadata={"stage": stage},
    )


class TestRunGeneratesAUniqueRunId:
    def test_two_runs_get_different_ids(self, pipeline, no_op_persistence, monkeypatch) -> None:
        monkeypatch.setattr(orchestrator_module, "build_plan", _stage_fn(RuntimeError("stop early")))
        result_1 = pipeline.run(ENQUIRY_TEXT)
        result_2 = pipeline.run(ENQUIRY_TEXT)
        assert result_1.id != result_2.id
        assert result_1.id  # non-empty


class TestRunIdSharedAcrossStagesForLoggingAndPersistence:
    def test_all_three_stages_see_the_same_run_id_via_context_var(
        self, pipeline, no_op_persistence, monkeypatch
    ) -> None:
        observed: dict[str, str | None] = {}

        def _fake_build_plan(enquiry_text, tool_manifest, adapter):
            observed["planner"] = run_id_ctx.get()
            return _valid_plan()

        def _fake_run_plan(plan, enquiry_text, registry):
            observed["executor"] = run_id_ctx.get()
            return _successful_execution()

        def _fake_verify(enquiry_text, plan, trace, final_record, adapter):
            observed["verifier"] = run_id_ctx.get()
            return _passing_decision()

        monkeypatch.setattr(orchestrator_module, "build_plan", _fake_build_plan)
        monkeypatch.setattr(orchestrator_module, "run_plan", _fake_run_plan)
        monkeypatch.setattr(orchestrator_module, "verify", _fake_verify)

        result = pipeline.run(ENQUIRY_TEXT)

        assert observed["planner"] == result.id
        assert observed["executor"] == result.id
        assert observed["verifier"] == result.id

    def test_persisted_run_result_id_matches_the_shared_run_id(
        self, pipeline, no_op_persistence, monkeypatch
    ) -> None:
        monkeypatch.setattr(orchestrator_module, "build_plan", _stage_fn(_valid_plan()))
        monkeypatch.setattr(orchestrator_module, "run_plan", _stage_fn(_successful_execution()))
        monkeypatch.setattr(orchestrator_module, "verify", _stage_fn(_passing_decision()))

        result = pipeline.run(ENQUIRY_TEXT)

        assert len(no_op_persistence) == 1
        _, persisted_result = no_op_persistence[0]
        assert persisted_result.id == result.id


class TestContextVarsAlwaysResetAfterRun:
    def test_reset_on_success(self, pipeline, no_op_persistence, monkeypatch) -> None:
        monkeypatch.setattr(orchestrator_module, "build_plan", _stage_fn(_valid_plan()))
        monkeypatch.setattr(orchestrator_module, "run_plan", _stage_fn(_successful_execution()))
        monkeypatch.setattr(orchestrator_module, "verify", _stage_fn(_passing_decision()))

        assert run_id_ctx.get() is None
        assert stage_ctx.get() is None
        pipeline.run(ENQUIRY_TEXT)
        assert run_id_ctx.get() is None
        assert stage_ctx.get() is None

    def test_reset_on_planner_failure(self, pipeline, no_op_persistence, monkeypatch) -> None:
        monkeypatch.setattr(orchestrator_module, "build_plan", _stage_fn(RuntimeError("boom")))

        assert run_id_ctx.get() is None
        pipeline.run(ENQUIRY_TEXT)
        assert run_id_ctx.get() is None
        assert stage_ctx.get() is None


class TestPipelineNeverRaises:
    @pytest.mark.parametrize(
        "failing_stage,exc",
        [
            ("build_plan", RuntimeError("planner boom")),
            ("run_plan", RuntimeError("executor boom")),
            ("verify", RuntimeError("verifier boom")),
        ],
    )
    def test_any_stage_exception_is_swallowed_into_a_structured_result(
        self, pipeline, no_op_persistence, monkeypatch, failing_stage, exc
    ) -> None:
        monkeypatch.setattr(orchestrator_module, "build_plan", _stage_fn(_valid_plan()))
        monkeypatch.setattr(orchestrator_module, "run_plan", _stage_fn(_successful_execution()))
        monkeypatch.setattr(orchestrator_module, "verify", _stage_fn(_passing_decision()))
        monkeypatch.setattr(orchestrator_module, failing_stage, _stage_fn(exc))

        result = pipeline.run(ENQUIRY_TEXT)  # must not raise

        assert result.final_status == "error"


class TestPlannerFailure:
    def test_produces_a_structured_error_result(self, pipeline, no_op_persistence, monkeypatch) -> None:
        monkeypatch.setattr(orchestrator_module, "build_plan", _stage_fn(RuntimeError("planner boom")))

        result = pipeline.run(ENQUIRY_TEXT)

        assert result.final_status == "error"
        assert result.plan is None
        assert result.plan_schema_valid is False
        assert result.tool_call_trace is None
        assert result.final_record is None
        assert result.verifier_decision is None

    def test_executor_and_verifier_are_never_invoked(self, pipeline, no_op_persistence, monkeypatch) -> None:
        executor_called = False
        verifier_called = False

        def _run_plan(*args, **kwargs):
            nonlocal executor_called
            executor_called = True
            return _successful_execution()

        def _verify(*args, **kwargs):
            nonlocal verifier_called
            verifier_called = True
            return _passing_decision()

        monkeypatch.setattr(orchestrator_module, "build_plan", _stage_fn(RuntimeError("boom")))
        monkeypatch.setattr(orchestrator_module, "run_plan", _run_plan)
        monkeypatch.setattr(orchestrator_module, "verify", _verify)

        pipeline.run(ENQUIRY_TEXT)

        assert executor_called is False
        assert verifier_called is False


class TestExecutorFailure:
    def test_unexpected_executor_exception_produces_structured_error_result(
        self, pipeline, no_op_persistence, monkeypatch
    ) -> None:
        monkeypatch.setattr(orchestrator_module, "build_plan", _stage_fn(_valid_plan()))
        monkeypatch.setattr(orchestrator_module, "run_plan", _stage_fn(RuntimeError("executor boom")))

        result = pipeline.run(ENQUIRY_TEXT)

        assert result.final_status == "error"
        assert result.plan is not None
        assert result.plan_schema_valid is True
        assert result.tool_call_trace is None

    def test_controlled_tool_failure_skips_verifier_and_preserves_trace(
        self, pipeline, no_op_persistence, monkeypatch
    ) -> None:
        trace = _trace_with_one_call(ToolCallStatus.ERROR)
        verifier_called = False

        def _verify(*args, **kwargs):
            nonlocal verifier_called
            verifier_called = True
            return _passing_decision()

        monkeypatch.setattr(orchestrator_module, "build_plan", _stage_fn(_valid_plan()))
        monkeypatch.setattr(orchestrator_module, "run_plan", _stage_fn(_failed_execution(trace=trace)))
        monkeypatch.setattr(orchestrator_module, "verify", _verify)

        result = pipeline.run(ENQUIRY_TEXT)

        assert verifier_called is False
        assert result.final_status == "error"
        assert result.tool_call_trace is not None
        assert result.tool_call_trace.calls == trace.calls
        assert result.final_record is None

    def test_missing_final_record_without_an_explicit_error_also_skips_verifier(
        self, pipeline, no_op_persistence, monkeypatch
    ) -> None:
        """Every step could have "succeeded" yet the plan didn't include
        all four canonical tools, so `final_record` is `None` with
        `error=None` -- this must still be treated as a failure, since
        there is nothing for the Verifier to judge."""
        execution = ExecutionResult(trace=_trace_with_one_call(), final_record=None, error=None)
        verifier_called = False

        def _verify(*args, **kwargs):
            nonlocal verifier_called
            verifier_called = True
            return _passing_decision()

        monkeypatch.setattr(orchestrator_module, "build_plan", _stage_fn(_valid_plan()))
        monkeypatch.setattr(orchestrator_module, "run_plan", _stage_fn(execution))
        monkeypatch.setattr(orchestrator_module, "verify", _verify)

        result = pipeline.run(ENQUIRY_TEXT)

        assert verifier_called is False
        assert result.final_status == "error"


class TestVerifierFailure:
    def test_unexpected_verifier_exception_produces_structured_error_result(
        self, pipeline, no_op_persistence, monkeypatch
    ) -> None:
        execution = _successful_execution()
        monkeypatch.setattr(orchestrator_module, "build_plan", _stage_fn(_valid_plan()))
        monkeypatch.setattr(orchestrator_module, "run_plan", _stage_fn(execution))
        monkeypatch.setattr(orchestrator_module, "verify", _stage_fn(RuntimeError("verifier boom")))

        result = pipeline.run(ENQUIRY_TEXT)

        assert result.final_status == "error"
        assert result.tool_call_trace is not None
        assert result.final_record is not None  # preserved even though verification itself failed
        assert result.verifier_decision is None


class TestVerifierOutcomeRouting:
    def test_pass_routes_to_completed(self, pipeline, no_op_persistence, monkeypatch) -> None:
        monkeypatch.setattr(orchestrator_module, "build_plan", _stage_fn(_valid_plan()))
        monkeypatch.setattr(orchestrator_module, "run_plan", _stage_fn(_successful_execution()))
        monkeypatch.setattr(orchestrator_module, "verify", _stage_fn(_passing_decision()))

        result = pipeline.run(ENQUIRY_TEXT)

        assert result.final_status == "completed"
        assert result.verifier_decision is not None
        assert result.verifier_decision.passed is True

    def test_fail_routes_to_quarantined_without_a_repair_attempt(
        self, pipeline, no_op_persistence, monkeypatch
    ) -> None:
        failing_decision = _passing_decision(passed=False, reason="fabrication detected")
        monkeypatch.setattr(orchestrator_module, "build_plan", _stage_fn(_valid_plan()))
        monkeypatch.setattr(orchestrator_module, "run_plan", _stage_fn(_successful_execution()))
        monkeypatch.setattr(orchestrator_module, "verify", _stage_fn(failing_decision))

        result = pipeline.run(ENQUIRY_TEXT)

        assert result.final_status == "quarantined"
        assert result.repair_attempted is False
        assert result.repair_succeeded is None
        assert result.verifier_decision.passed is False


class TestUsageAggregation:
    def test_total_tokens_cost_and_latency_are_summed_across_stages(
        self, pipeline, no_op_persistence, monkeypatch
    ) -> None:
        def _fake_build_plan(enquiry_text, tool_manifest, adapter):
            adapter.complete_structured(_request(stage="planner"))
            return _valid_plan()

        def _fake_verify(enquiry_text, plan, trace, final_record, adapter):
            adapter.complete_structured(_request(stage="verifier"))
            return _passing_decision()

        monkeypatch.setattr(orchestrator_module, "build_plan", _fake_build_plan)
        monkeypatch.setattr(orchestrator_module, "run_plan", _stage_fn(_successful_execution()))
        monkeypatch.setattr(orchestrator_module, "verify", _fake_verify)

        pipeline_with_real_adapter = Pipeline(adapter=_RecordingInnerAdapter(), settings=Settings())
        result = pipeline_with_real_adapter.run(ENQUIRY_TEXT)

        assert len(result.llm_calls) == 2
        assert {c.stage for c in result.llm_calls} == {"planner", "verifier"}
        assert result.total_tokens == sum(c.prompt_tokens + c.completion_tokens for c in result.llm_calls)
        assert result.total_latency_ms == sum(c.latency_ms for c in result.llm_calls)

    def test_zero_usage_when_no_llm_call_was_made(self, pipeline, no_op_persistence, monkeypatch) -> None:
        monkeypatch.setattr(orchestrator_module, "build_plan", _stage_fn(RuntimeError("boom")))

        result = pipeline.run(ENQUIRY_TEXT)

        assert result.llm_calls == []
        assert result.total_tokens == 0
        assert result.total_cost_usd == 0.0
        assert result.total_latency_ms == 0.0


class TestCommonFieldsPropagation:
    def test_enquiry_id_repeat_index_and_is_adversarial_propagate_on_success(
        self, pipeline, no_op_persistence, monkeypatch
    ) -> None:
        monkeypatch.setattr(orchestrator_module, "build_plan", _stage_fn(_valid_plan()))
        monkeypatch.setattr(orchestrator_module, "run_plan", _stage_fn(_successful_execution()))
        monkeypatch.setattr(orchestrator_module, "verify", _stage_fn(_passing_decision()))

        result = pipeline.run(ENQUIRY_TEXT, enquiry_id="enq-1", repeat_index=2, is_adversarial=True)

        assert result.enquiry_id == "enq-1"
        assert result.repeat_index == 2
        assert result.is_adversarial is True
        assert result.enquiry_text == ENQUIRY_TEXT

    def test_propagate_even_on_planner_failure(self, pipeline, no_op_persistence, monkeypatch) -> None:
        monkeypatch.setattr(orchestrator_module, "build_plan", _stage_fn(RuntimeError("boom")))

        result = pipeline.run(ENQUIRY_TEXT, enquiry_id="enq-2", repeat_index=0)

        assert result.enquiry_id == "enq-2"
        assert result.repeat_index == 0

    def test_created_at_is_always_set(self, pipeline, no_op_persistence, monkeypatch) -> None:
        monkeypatch.setattr(orchestrator_module, "build_plan", _stage_fn(RuntimeError("boom")))
        result = pipeline.run(ENQUIRY_TEXT)
        assert result.created_at is not None


class TestPersistence:
    def test_uses_the_explicitly_provided_session(self, pipeline, no_op_persistence, monkeypatch) -> None:
        monkeypatch.setattr(orchestrator_module, "build_plan", _stage_fn(_valid_plan()))
        monkeypatch.setattr(orchestrator_module, "run_plan", _stage_fn(_successful_execution()))
        monkeypatch.setattr(orchestrator_module, "verify", _stage_fn(_passing_decision()))

        sentinel_db = object()
        pipeline.run(ENQUIRY_TEXT, db=sentinel_db)

        assert len(no_op_persistence) == 1
        used_db, _ = no_op_persistence[0]
        assert used_db is sentinel_db

    def test_opens_its_own_session_scope_when_none_is_provided(
        self, pipeline, no_op_persistence, monkeypatch
    ) -> None:
        monkeypatch.setattr(orchestrator_module, "build_plan", _stage_fn(_valid_plan()))
        monkeypatch.setattr(orchestrator_module, "run_plan", _stage_fn(_successful_execution()))
        monkeypatch.setattr(orchestrator_module, "verify", _stage_fn(_passing_decision()))

        scoped_session_sentinel = object()
        entered = []

        @contextmanager
        def _fake_session_scope():
            entered.append(True)
            yield scoped_session_sentinel

        monkeypatch.setattr(orchestrator_module, "session_scope", _fake_session_scope)

        pipeline.run(ENQUIRY_TEXT)

        assert entered == [True]
        used_db, _ = no_op_persistence[0]
        assert used_db is scoped_session_sentinel

    def test_persist_failure_does_not_prevent_run_from_returning_a_result(
        self, pipeline, monkeypatch
    ) -> None:
        def _raising_create_run(db, result, **kwargs):
            raise RuntimeError("db is down")

        monkeypatch.setattr(repository_module, "create_run", _raising_create_run)
        monkeypatch.setattr(orchestrator_module, "build_plan", _stage_fn(_valid_plan()))
        monkeypatch.setattr(orchestrator_module, "run_plan", _stage_fn(_successful_execution()))
        monkeypatch.setattr(orchestrator_module, "verify", _stage_fn(_passing_decision()))

        result = pipeline.run(ENQUIRY_TEXT, db=object())  # must not raise

        assert result.final_status == "completed"

    def test_persist_is_attempted_even_when_the_run_itself_errored(
        self, pipeline, no_op_persistence, monkeypatch
    ) -> None:
        """A failed run is still worth recording -- it must still reach the
        repository, not be silently dropped."""
        monkeypatch.setattr(orchestrator_module, "build_plan", _stage_fn(RuntimeError("boom")))

        pipeline.run(ENQUIRY_TEXT)

        assert len(no_op_persistence) == 1
        _, persisted_result = no_op_persistence[0]
        assert persisted_result.final_status == "error"


class TestPipelineStructure:
    def test_pipeline_is_constructed_from_adapter_and_settings_only(self) -> None:
        settings = Settings()
        adapter = _NeverCalledAdapter()
        pipeline = Pipeline(adapter=adapter, settings=settings)
        assert pipeline.adapter is adapter
        assert pipeline.settings is settings
