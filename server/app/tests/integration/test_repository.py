"""Integration tests for db/repository.py against a real (file-based)
SQLite database. See docs/contracts.md section 6 for the contract each
function is expected to satisfy.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.db import repository
from app.schemas.plan import Plan, PlanStep, ToolName
from app.schemas.run import LlmCallUsage, RunResult
from app.schemas.tool_trace import ToolCall, ToolCallTrace
from app.schemas.verifier import VerifierDecision


def _make_run_result(**overrides) -> RunResult:
    defaults: dict = {
        "id": "run-1",
        "enquiry_text": "Hi, I'm interested in whisky casks.",
        "final_status": "completed",
    }
    defaults.update(overrides)
    return RunResult(**defaults)


class TestCreateAndGetRun:
    def test_create_run_persists_scalar_fields(self, db_session) -> None:
        run = _make_run_result(
            id="run-scalar",
            enquiry_id="enq-01",
            repeat_index=2,
            is_adversarial=True,
            final_status="completed",
            total_tokens=1180,
            total_cost_usd=0.0031,
            total_latency_ms=2140.5,
        )
        row = repository.create_run(db_session, run)

        assert row.id == "run-scalar"
        assert row.enquiry_text == run.enquiry_text
        assert row.enquiry_id == "enq-01"
        assert row.repeat_index == 2
        assert row.is_adversarial is True
        assert row.final_status == "completed"
        assert row.total_tokens == 1180
        assert row.total_cost_usd == pytest.approx(0.0031)
        assert row.total_latency_ms == pytest.approx(2140.5)

    def test_create_run_persists_nested_plan_trace_and_record_as_json(self, db_session) -> None:
        plan = Plan(
            steps=[
                PlanStep(step=1, tool=ToolName.PARSE_ENQUIRY, args={}, rationale="Extract fields."),
            ]
        )
        now = datetime.now(UTC)
        trace = ToolCallTrace(
            calls=[
                ToolCall(
                    step=1,
                    tool=ToolName.PARSE_ENQUIRY,
                    args={},
                    status="success",
                    result={"name": "Jane Doe"},
                    latency_ms=12.3,
                    started_at=now,
                    finished_at=now,
                )
            ]
        )
        run = _make_run_result(id="run-nested", plan=plan, plan_schema_valid=True, tool_call_trace=trace)

        row = repository.create_run(db_session, run)

        assert row.plan == plan.model_dump(mode="json")
        assert row.plan_schema_valid is True
        assert row.tool_call_trace == trace.model_dump(mode="json")
        assert row.tool_call_trace["calls"][0]["result"] == {"name": "Jane Doe"}

    def test_create_run_flattens_verifier_decision_onto_columns(self, db_session) -> None:
        verifier = VerifierDecision(
            passed=False,
            confidence=0.81,
            fabrication_detected=True,
            fabricated_fields=["phone"],
            plan_deviation_detected=False,
            deviation_details=None,
            reason="Phone number not present in enquiry_text.",
        )
        run = _make_run_result(id="run-verifier", final_status="quarantined", verifier_decision=verifier)

        row = repository.create_run(db_session, run)

        assert row.verifier_pass is False
        assert row.verifier_confidence == pytest.approx(0.81)
        assert row.verifier_reason == "Phone number not present in enquiry_text."
        assert row.fabrication_detected is True
        assert row.fabricated_fields == ["phone"]
        assert row.plan_deviation_detected is False
        assert row.deviation_details is None

    def test_create_run_persists_llm_call_children(self, db_session) -> None:
        run = _make_run_result(
            id="run-llm-calls",
            llm_calls=[
                LlmCallUsage(
                    stage="planner",
                    model_provider="openai",
                    model_name="gpt-4o-mini",
                    prompt_tokens=340,
                    completion_tokens=120,
                    cost_usd=0.0009,
                    latency_ms=610.2,
                ),
                LlmCallUsage(
                    stage="verifier",
                    model_provider="openai",
                    model_name="gpt-4o-mini",
                    prompt_tokens=200,
                    completion_tokens=40,
                    cost_usd=0.0004,
                    latency_ms=300.0,
                ),
            ],
        )

        row = repository.create_run(db_session, run)

        assert len(row.llm_calls) == 2
        stages = {call.stage for call in row.llm_calls}
        assert stages == {"planner", "verifier"}
        assert row.model_provider == "openai"
        assert row.model_name == "gpt-4o-mini"

    def test_create_run_without_llm_calls_leaves_model_fields_none(self, db_session) -> None:
        run = _make_run_result(id="run-no-llm-calls")
        row = repository.create_run(db_session, run)
        assert row.model_provider is None
        assert row.model_name is None
        assert row.llm_calls == []

    def test_create_run_accepts_optional_harness_batch_id(self, db_session) -> None:
        batch = repository.create_harness_batch(db_session, n_runs=0)
        run = _make_run_result(id="run-in-batch")

        row = repository.create_run(db_session, run, harness_batch_id=batch.id)

        assert row.harness_batch_id == batch.id

    def test_get_run_returns_persisted_row(self, db_session) -> None:
        repository.create_run(db_session, _make_run_result(id="run-get"))
        found = repository.get_run(db_session, "run-get")
        assert found is not None
        assert found.id == "run-get"

    def test_get_run_returns_none_for_unknown_id(self, db_session) -> None:
        assert repository.get_run(db_session, "does-not-exist") is None


class TestListRuns:
    def test_list_runs_orders_most_recent_first(self, db_session) -> None:
        now = datetime.now(UTC)
        repository.create_run(
            db_session, _make_run_result(id="run-oldest", created_at=now - timedelta(minutes=2))
        )
        repository.create_run(
            db_session, _make_run_result(id="run-middle", created_at=now - timedelta(minutes=1))
        )
        repository.create_run(db_session, _make_run_result(id="run-newest", created_at=now))

        rows = repository.list_runs(db_session)

        assert [row.id for row in rows] == ["run-newest", "run-middle", "run-oldest"]

    def test_list_runs_respects_limit_and_offset(self, db_session) -> None:
        now = datetime.now(UTC)
        for i in range(5):
            repository.create_run(
                db_session,
                _make_run_result(id=f"run-{i}", created_at=now - timedelta(minutes=i)),
            )

        page = repository.list_runs(db_session, limit=2, offset=1)

        assert [row.id for row in page] == ["run-1", "run-2"]

    def test_list_runs_on_empty_database_returns_empty_list(self, db_session) -> None:
        assert repository.list_runs(db_session) == []


class TestLeads:
    def test_create_lead_persists_all_fields(self, db_session) -> None:
        lead = repository.create_lead(
            db_session,
            dedupe_hash="hash-1",
            name="Jane Doe",
            email="jane@example.com",
            phone="5551234",
            country="Singapore",
            budget_band="high",
            asset_interest="single malt casks",
            urgency="medium",
            score=78,
            score_breakdown={"budget": 40, "urgency": 20, "jurisdiction_risk": 18},
            jurisdiction_rule={"country": "Singapore", "requires_disclaimer": True},
            status="accepted",
        )

        assert lead.id is not None
        assert lead.dedupe_hash == "hash-1"
        assert lead.status == "accepted"
        assert lead.score == 78

    def test_create_lead_with_duplicate_dedupe_hash_raises_integrity_error(self, db_session) -> None:
        repository.create_lead(db_session, dedupe_hash="dup-hash", status="accepted")

        with pytest.raises(IntegrityError):
            repository.create_lead(db_session, dedupe_hash="dup-hash", status="accepted")

    def test_session_is_usable_after_integrity_error(self, db_session) -> None:
        repository.create_lead(db_session, dedupe_hash="dup-hash-2", status="accepted")
        with pytest.raises(IntegrityError):
            repository.create_lead(db_session, dedupe_hash="dup-hash-2", status="accepted")

        # The failed insert's rollback must not poison the session -- a
        # subsequent, unrelated write on the same session must still work.
        lead = repository.create_lead(db_session, dedupe_hash="fresh-hash", status="accepted")
        assert lead.dedupe_hash == "fresh-hash"

    def test_find_lead_by_dedupe_hash_returns_none_when_missing(self, db_session) -> None:
        assert repository.find_lead_by_dedupe_hash(db_session, "not-there") is None

    def test_find_lead_by_dedupe_hash_returns_matching_row(self, db_session) -> None:
        repository.create_lead(db_session, dedupe_hash="findme", status="accepted")
        found = repository.find_lead_by_dedupe_hash(db_session, "findme")
        assert found is not None
        assert found.dedupe_hash == "findme"

    def test_list_leads_filters_by_status(self, db_session) -> None:
        repository.create_lead(db_session, dedupe_hash="a", status="accepted")
        repository.create_lead(db_session, dedupe_hash="b", status="quarantined")
        repository.create_lead(db_session, dedupe_hash="c", status="accepted")

        accepted = repository.list_leads(db_session, status="accepted")
        quarantined = repository.list_leads(db_session, status="quarantined")

        assert {lead.dedupe_hash for lead in accepted} == {"a", "c"}
        assert {lead.dedupe_hash for lead in quarantined} == {"b"}

    def test_list_leads_without_status_returns_all(self, db_session) -> None:
        repository.create_lead(db_session, dedupe_hash="a", status="accepted")
        repository.create_lead(db_session, dedupe_hash="b", status="quarantined")

        all_leads = repository.list_leads(db_session)

        assert {lead.dedupe_hash for lead in all_leads} == {"a", "b"}


class TestHarnessBatches:
    def test_create_and_fetch_latest_harness_batch(self, db_session) -> None:
        now = datetime.now(UTC)
        repository.create_harness_batch(db_session, started_at=now - timedelta(hours=1), n_runs=45)
        newest = repository.create_harness_batch(db_session, started_at=now, n_runs=45)

        latest = repository.get_latest_harness_batch(db_session)

        assert latest is not None
        assert latest.id == newest.id

    def test_get_latest_harness_batch_returns_none_when_empty(self, db_session) -> None:
        assert repository.get_latest_harness_batch(db_session) is None
