"""Unit tests for evaluation/adversarial.py — no real LLM calls."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.schemas.extraction import BudgetBand, ExtractedFields, Urgency
from app.schemas.lead_record import LeadRecord
from app.schemas.plan import Plan, PlanStep, ToolName
from app.schemas.run import RunResult
from app.schemas.tool_trace import ToolCall, ToolCallStatus, ToolCallTrace
from app.schemas.verifier import VerifierDecision
from evaluation import adversarial as adversarial_module
from evaluation.adversarial import _injection_observations, load_adversarial_enquiries, run_adversarial


class _FakePipeline:
    def __init__(self, results: list[RunResult]) -> None:
        self._results = list(results)
        self.calls: list[dict] = []

    def run(self, enquiry_text: str, **kwargs) -> RunResult:
        self.calls.append({"enquiry_text": enquiry_text, **kwargs})
        return self._results.pop(0)


def _plan(*tools: ToolName) -> Plan:
    return Plan(
        steps=[
            PlanStep(step=i, tool=tool, args={}, rationale=f"step {i}")
            for i, tool in enumerate(tools, start=1)
        ]
    )


def _result(
    *,
    enquiry_text: str = "sample",
    plan: Plan | None = None,
    final_status: str = "quarantined",
    passed: bool = False,
    budget_band: BudgetBand = BudgetBand.MEDIUM,
) -> RunResult:
    record = LeadRecord(
        extracted=ExtractedFields(
            name="Alex",
            email="alex@example.com",
            phone="14155550199",
            country="United States",
            budget_band=budget_band,
            urgency=Urgency.HIGH,
        ),
        jurisdiction_rule={
            "country": "United States",
            "requires_disclaimer": True,
            "restricted": True,
            "handling_note": "test",
        },
        score=40,
        score_breakdown={"budget": 25, "urgency": 15, "jurisdiction_risk": 0},
        dedupe_hash="abc",
    )
    return RunResult(
        id="run-1",
        enquiry_text=enquiry_text,
        plan=plan,
        plan_schema_valid=plan is not None,
        tool_call_trace=ToolCallTrace(
            calls=[
                ToolCall(
                    step=1,
                    tool=ToolName.PARSE_ENQUIRY,
                    args={},
                    status=ToolCallStatus.SUCCESS,
                    result={},
                    error=None,
                    validation_errors=None,
                    latency_ms=1.0,
                    started_at=datetime.now(UTC),
                    finished_at=datetime.now(UTC),
                )
            ]
        )
        if plan is not None
        else None,
        final_record=record,
        verifier_decision=VerifierDecision(
            **{
                "pass": passed,
                "confidence": 0.9,
                "fabrication_detected": not passed,
                "fabricated_fields": [],
                "plan_deviation_detected": False,
                "reason": "test",
            }
        ),
        final_status=final_status,
        created_at=datetime.now(UTC),
    )


def test_fixtures_contain_exactly_three_adversarial_enquiries() -> None:
    enquiries = load_adversarial_enquiries()
    assert len(enquiries) == 3
    assert all("id" in e and "text" in e for e in enquiries)
    assert any("skip" in e["text"].lower() or "lookup_jurisdiction" in e["text"].lower() for e in enquiries)


def test_empty_enquiries_raise() -> None:
    with pytest.raises(ValueError, match="empty"):
        run_adversarial(enquiries=[], pipeline=_FakePipeline([]))


def test_run_adversarial_marks_is_adversarial_and_writes_report(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(adversarial_module, "REPORTS_DIR", tmp_path / "reports")
    plan = _plan(
        ToolName.PARSE_ENQUIRY,
        ToolName.LOOKUP_JURISDICTION_RULE,
        ToolName.SCORE_LEAD,
        ToolName.WRITE_RECORD,
    )
    pipeline = _FakePipeline([_result(plan=plan, passed=False)])
    reports = run_adversarial(
        enquiries=[{"id": "adv-x", "text": "hello"}],
        pipeline=pipeline,
    )
    assert len(reports) == 1
    assert pipeline.calls[0]["is_adversarial"] is True
    assert pipeline.calls[0]["enquiry_id"] == "adv-x"
    assert reports[0]["enquiry_id"] == "adv-x"


def test_injection_observations_flag_missing_jurisdiction_in_plan() -> None:
    plan = _plan(ToolName.PARSE_ENQUIRY, ToolName.SCORE_LEAD, ToolName.WRITE_RECORD)
    result = _result(plan=plan, passed=True)
    observations = _injection_observations(result)
    assert any("omitted lookup_jurisdiction_rule" in o for o in observations)
    assert any("UNCAUGHT" in o for o in observations)
