"""Integration tests for the FastAPI JSON surface (server/app/api/routes_*.py),
exercised end-to-end via `TestClient` against the real `app.main.app` --
routing, request/response validation, and status codes included, not just
the underlying repository/pipeline calls (already covered directly by
test_orchestrator_integration.py and test_repository.py).

`get_db`/`get_adapter`/`get_app_settings` are overridden per-test via
FastAPI's `dependency_overrides` so nothing here ever touches the real
`OPENROUTER_API_KEY` or the real `server/data/app.db` -- see conftest.py's
`db_session` fixture for the isolated, throwaway database each test gets.
Mirrors test_orchestrator_integration.py's mock-tool-registration pattern,
duplicated (not imported) to keep this module self-contained, per this
codebase's existing convention (see that module's own docstring).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.deps import get_adapter, get_app_settings, get_db
from app.core.config import Settings
from app.db import repository
from app.llm.base import ModelAdapter, StructuredCompletionRequest, StructuredCompletionResponse
from app.main import app
from app.schemas.plan import Plan, PlanStep, ToolName
from app.schemas.verifier import VerifierDecision
from app.tools.base import Tool, ToolResult

ENQUIRY_TEXT = "Hi, I'm Jane Doe (jane@example.com), interested in whisky casks in Singapore."

_CANONICAL_TOOL_NAMES = {tool.value for tool in ToolName}

# Mirrors evaluation/metrics.py::_compute_metrics_from_runs([])'s own
# n_runs=0 shape -- a minimal but fully valid `HarnessMetrics` payload for
# tests that only need *a* persisted batch to exist, not real numbers.
_EMPTY_METRICS = {
    "n_runs": 0,
    "completion_rate": None,
    "quarantined_rate": None,
    "error_rate": None,
    "pass_rate": None,
    "fabrication_rate": None,
    "fabrication_caught_rate": None,
    "planner_schema_breach_rate": None,
    "extractor_schema_breach_rate": None,
    "tool_selection_accuracy": None,
    "repair_attempted_rate": None,
    "repair_success_rate": None,
    "mean_latency_ms": 0.0,
    "median_latency_ms": 0.0,
    "p95_latency_ms": 0.0,
    "mean_tokens": 0.0,
    "mean_cost_usd": 0.0,
    "variance": {
        "by_enquiry": {},
        "overall_latency_ms_stdev": 0.0,
        "overall_cost_usd_stdev": 0.0,
        "overall_tokens_stdev": 0.0,
    },
    "notes": [],
}


class _PermissiveArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


class _PermissiveResult(BaseModel):
    model_config = ConfigDict(extra="allow")


def _mock_tool(tool_name: str, run_impl) -> type[Tool]:
    return type(
        f"ApiMock_{tool_name}",
        (Tool,),
        {
            "name": tool_name,
            "description": f"mock {tool_name} for API route integration tests",
            "args_schema": _PermissiveArgs,
            "result_schema": _PermissiveResult,
            "run": run_impl,
        },
    )


def _register_canonical_mock_tools() -> None:
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
            success=True, data={"score": 80, "breakdown": {"budget": 80}}, error=None, latency_ms=1.0
        ),
    )
    _mock_tool(
        "write_record",
        lambda self, args: ToolResult(
            success=True, data={"lead_id": "lead-1", "dedupe_hash": "hash-1"}, error=None, latency_ms=1.0
        ),
    )


def _isolate_tool_slate() -> dict:
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


class _FakeAdapter(ModelAdapter):
    provider_name = "fake"

    def __init__(self, *, plan: Plan, decision: VerifierDecision) -> None:
        self._plan = plan
        self._decision = decision

    def complete_structured(self, request: StructuredCompletionRequest) -> StructuredCompletionResponse:
        if request.response_schema is Plan:
            parsed: BaseModel = self._plan
            model = "fake-planner-model"
        elif request.response_schema is VerifierDecision:
            parsed = self._decision
            model = "fake-verifier-model"
        else:
            raise AssertionError(f"unexpected response_schema: {request.response_schema}")
        return StructuredCompletionResponse(
            parsed=parsed,
            raw_response={"id": "fake"},
            prompt_tokens=10,
            completion_tokens=5,
            latency_ms=1.0,
            model=model,
        )


@pytest.fixture
def api_client(db_session: Session):
    """`TestClient` wired to the real `app.main.app`, with `get_db` pinned to
    this test's isolated `db_session` and `get_adapter`/`get_app_settings`
    overridden so no test here ever needs a real `OPENROUTER_API_KEY`."""
    saved = _isolate_tool_slate()
    _register_canonical_mock_tools()

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_adapter] = lambda: _FakeAdapter(
        plan=_canonical_plan(), decision=_passing_decision()
    )
    app.dependency_overrides[get_app_settings] = lambda: Settings()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        _restore_tool_slate(saved)


class TestHealth:
    def test_health_returns_ok(self, api_client: TestClient) -> None:
        response = api_client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestCreateAndListRuns:
    def test_post_runs_executes_the_pipeline_and_returns_a_completed_run_result(
        self, api_client: TestClient
    ) -> None:
        response = api_client.post("/api/runs", json={"enquiry_text": ENQUIRY_TEXT})

        assert response.status_code == 200
        body = response.json()
        assert body["final_status"] == "completed"
        assert body["enquiry_text"] == ENQUIRY_TEXT
        from app.services.dedupe import compute_dedupe_hash

        assert body["final_record"]["dedupe_hash"] == compute_dedupe_hash("jane@example.com", None)
        assert body["verifier_decision"]["pass"] is True
        assert len(body["tool_call_trace"]["calls"]) == 4
        assert body["tool_call_trace"]["calls"][-1]["tool"] == "write_record"

    def test_get_runs_lists_previously_created_runs_most_recent_first(
        self, api_client: TestClient, db_session: Session
    ) -> None:
        first = api_client.post("/api/runs", json={"enquiry_text": ENQUIRY_TEXT}).json()
        second = api_client.post("/api/runs", json={"enquiry_text": ENQUIRY_TEXT}).json()

        response = api_client.get("/api/runs")

        assert response.status_code == 200
        ids = [row["id"] for row in response.json()]
        assert ids[0] == second["id"]
        assert ids[1] == first["id"]

    def test_get_run_by_id_returns_the_full_run_result(self, api_client: TestClient) -> None:
        created = api_client.post("/api/runs", json={"enquiry_text": ENQUIRY_TEXT}).json()

        response = api_client.get(f"/api/runs/{created['id']}")

        assert response.status_code == 200
        assert response.json()["id"] == created["id"]
        from app.services.dedupe import compute_dedupe_hash

        assert response.json()["final_record"]["dedupe_hash"] == compute_dedupe_hash("jane@example.com", None)

    def test_get_run_by_unknown_id_returns_404_with_structured_detail(self, api_client: TestClient) -> None:
        response = api_client.get("/api/runs/does-not-exist")

        assert response.status_code == 404
        assert "does-not-exist" in response.json()["detail"]


class TestLeads:
    def test_list_leads_is_empty_before_any_run(self, api_client: TestClient) -> None:
        response = api_client.get("/api/leads")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_leads_reflects_a_lead_persisted_via_the_repository(
        self, api_client: TestClient, db_session: Session
    ) -> None:
        """`write_record` (docs/architecture.md section 8) deliberately opens
        its own session against the real, process-wide engine rather than
        the request-scoped `db` this route uses (see its own docstring), so
        exercising a full `POST /api/runs` here -- with `write_record`
        mocked out like the other three tools, per this module's shared
        fixture -- would never actually touch this test's isolated
        `db_session`. Persisting directly via `repository.create_lead()`
        instead (exactly as `write_record` itself would) keeps this test
        from writing into the real `server/data/app.db`, while still
        genuinely exercising `GET /api/leads`'s own mapping and response
        shape end-to-end."""
        repository.create_lead(
            db_session,
            dedupe_hash="hash-1",
            name="Jane Doe",
            email="jane@example.com",
            status="accepted",
            score=80,
        )

        response = api_client.get("/api/leads")

        assert response.status_code == 200
        [lead] = response.json()
        assert lead["dedupe_hash"] == "hash-1"
        assert lead["status"] == "accepted"
        assert lead["score"] == 80

    def test_list_leads_filters_by_status(self, api_client: TestClient, db_session: Session) -> None:
        repository.create_lead(db_session, dedupe_hash="h-accepted", email="a@example.com", status="accepted")
        repository.create_lead(
            db_session, dedupe_hash="h-quarantined", email="q@example.com", status="quarantined"
        )

        response = api_client.get("/api/leads", params={"status": "quarantined"})

        assert response.status_code == 200
        [lead] = response.json()
        assert lead["dedupe_hash"] == "h-quarantined"

    def test_list_leads_rejects_an_invalid_status_with_422(self, api_client: TestClient) -> None:
        response = api_client.get("/api/leads", params={"status": "bogus"})
        assert response.status_code == 422


class TestHarness:
    def test_harness_summary_is_null_when_no_batch_has_run_yet(self, api_client: TestClient) -> None:
        response = api_client.get("/api/harness/summary")
        assert response.status_code == 200
        assert response.json() is None

    def test_harness_runs_is_empty_when_no_batch_has_run_yet(self, api_client: TestClient) -> None:
        response = api_client.get("/api/harness/runs")
        assert response.status_code == 200
        assert response.json() == []

    def test_harness_summary_and_runs_reflect_the_latest_persisted_batch(
        self, api_client: TestClient, db_session: Session
    ) -> None:
        batch = repository.create_harness_batch(db_session, n_runs=1, config_snapshot={"model_name": "x"})
        repository.finish_harness_batch(db_session, batch.id, metrics=_EMPTY_METRICS)
        created = api_client.post("/api/runs", json={"enquiry_text": ENQUIRY_TEXT}).json()
        run_row = repository.get_run(db_session, created["id"])
        run_row.harness_batch_id = batch.id
        db_session.commit()

        summary_response = api_client.get("/api/harness/summary")
        runs_response = api_client.get("/api/harness/runs")

        assert summary_response.status_code == 200
        summary = summary_response.json()
        assert summary["id"] == batch.id
        assert summary["metrics"]["n_runs"] == 0

        assert runs_response.status_code == 200
        run_ids = [row["id"] for row in runs_response.json()]
        assert run_ids == [created["id"]]

    def test_lists_harness_datasets(self, api_client: TestClient) -> None:
        response = api_client.get("/api/harness/datasets")
        assert response.status_code == 200
        ids = {row["id"] for row in response.json()}
        assert "standard" in ids
        assert "manual_e01_e14" in ids
        assert "custom" in ids

    def test_batch_dashboard_and_timeline(
        self, api_client: TestClient, db_session: Session
    ) -> None:
        batch = repository.create_harness_batch(
            db_session,
            n_runs=1,
            config_snapshot={
                "dataset_id": "standard",
                "dataset_label": "Standard Evaluation",
                "n_repeats": 1,
            },
        )
        repository.finish_harness_batch(db_session, batch.id, metrics=_EMPTY_METRICS)
        created = api_client.post("/api/runs", json={"enquiry_text": ENQUIRY_TEXT}).json()
        run_row = repository.get_run(db_session, created["id"])
        run_row.harness_batch_id = batch.id
        db_session.commit()

        dash = api_client.get(f"/api/harness/batches/{batch.id}/dashboard")
        assert dash.status_code == 200
        body = dash.json()
        assert body["batch_id"] == batch.id
        assert body["statistics"]["total"] == 1
        assert "pipeline_health" in body
        assert len(body["runs"]) == 1

        timeline = api_client.get(f"/api/harness/runs/{created['id']}/timeline")
        assert timeline.status_code == 200
        stages = [event["stage"] for event in timeline.json()]
        assert "planner" in stages
        assert "verifier" in stages

    def test_start_job_rejects_invalid_repeats(self, api_client: TestClient) -> None:
        response = api_client.post(
            "/api/harness/jobs",
            json={"dataset_id": "standard", "n_repeats": 2},
        )
        assert response.status_code == 422

    def test_parse_plain_text_dataset(self, api_client: TestClient) -> None:
        response = api_client.post(
            "/api/harness/parse",
            json={
                "text": "E01\nFirst case.\n\nE02\nSecond case.",
                "mode": "auto",
                "filename": "hidden.md",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["n_enquiries"] == 2
        assert body["enquiries"][0]["id"] == "E01"

    def test_parse_single_mode(self, api_client: TestClient) -> None:
        response = api_client.post(
            "/api/harness/parse",
            json={"text": "One enquiry only.", "mode": "single", "filename": "solo.txt"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["n_enquiries"] == 1
        assert body["format_detected"] == "single"
        assert body["enquiries"][0]["id"] == "solo"
