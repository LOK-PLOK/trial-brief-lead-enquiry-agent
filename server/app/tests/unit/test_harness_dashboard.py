"""Unit tests for harness dashboard aggregates."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.services.harness_dashboard import build_dashboard, build_run_timeline


def _run(**overrides):
    base = dict(
        id="r1",
        enquiry_id="enq-01",
        repeat_index=1,
        enquiry_text="hello",
        plan={"steps": [{"step": 1, "tool": "parse_enquiry"}]},
        plan_schema_valid=True,
        tool_call_trace={
            "calls": [
                {
                    "tool": "parse_enquiry",
                    "status": "success",
                    "latency_ms": 10,
                    "error": None,
                }
            ]
        },
        final_record={"score": 70},
        verifier_pass=True,
        verifier_confidence=0.9,
        verifier_reason="ok",
        fabrication_detected=False,
        fabricated_fields=[],
        plan_deviation_detected=False,
        repair_attempted=False,
        repair_succeeded=None,
        final_status="completed",
        error_type=None,
        error_message=None,
        traceback=None,
        total_latency_ms=100.0,
        total_cost_usd=0.01,
        total_tokens=50,
        llm_calls=[
            SimpleNamespace(stage="planner", cost_usd=0.004),
            SimpleNamespace(stage="verifier", cost_usd=0.006),
        ],
        created_at=datetime.now(UTC),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_build_dashboard_statistics_and_health() -> None:
    runs = [
        _run(),
        _run(
            id="r2",
            enquiry_id="enq-02",
            final_status="quarantined",
            verifier_pass=False,
            fabrication_detected=True,
            fabricated_fields=["urgency", "budget_band"],
            repair_attempted=True,
            repair_succeeded=False,
        ),
    ]
    dash = build_dashboard(
        batch_id="b1",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        dataset_id="standard",
        dataset_label="Official Trial A Dataset (E01–E15)",
        n_repeats=1,
        metrics=None,
        runs=runs,  # type: ignore[arg-type]
    )
    assert dash["statistics"]["total"] == 2
    assert dash["statistics"]["passed"] == 1
    assert dash["statistics"]["quarantined"] == 1
    assert dash["failure_breakdown"]["urgency"] == 1
    assert dash["failure_breakdown"]["budget_band"] == 1
    assert dash["pipeline_health"]["planner"] in {"green", "yellow", "red", "unknown"}
    assert len(dash["runs"]) == 2


def test_timeline_includes_core_stages() -> None:
    events = build_run_timeline(_run())  # type: ignore[arg-type]
    stages = {e["stage"] for e in events}
    assert "planner" in stages
    assert "verifier" in stages
    assert "outcome" in stages


def test_build_run_row_prefers_error_message_over_verifier_reason() -> None:
    from app.services.harness_dashboard import build_run_row

    row = build_run_row(
        _run(  # type: ignore[arg-type]
            final_status="error",
            verifier_pass=None,
            verifier_reason=None,
            error_type="HTTPStatusError",
            error_message="Client error '402 Payment Required'",
            traceback="Traceback (most recent call last):\n...",
        )
    )
    assert row["error_type"] == "HTTPStatusError"
    assert row["error_message"] == "Client error '402 Payment Required'"
    assert row["reason"] == "Client error '402 Payment Required'"
    assert "Traceback" in (row["traceback"] or "")
