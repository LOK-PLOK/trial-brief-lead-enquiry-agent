"""Unit tests for agent/repair.py and prompts/repair_prompt.py."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from app.agent.prompts.repair_prompt import augment_for_fabrication, augment_for_plan_deviation
from app.agent.repair import RepairResult, attempt_repair
from app.llm.base import ModelAdapter, StructuredCompletionRequest, StructuredCompletionResponse
from app.schemas.extraction import ExtractedFields
from app.schemas.plan import Plan, PlanStep, ToolName
from app.schemas.tool_trace import ToolCall, ToolCallStatus, ToolCallTrace
from app.schemas.verifier import VerifierDecision
from app.tools.base import Tool, ToolResult

ENQUIRY = "Hi, I'm Sam Lee sam@example.com +65 9123 4567 from Singapore, budget around 20k, soon."

_CANONICAL = {t.value for t in ToolName}


class _Permissive(BaseModel):
    model_config = ConfigDict(extra="allow")


def _plan() -> Plan:
    return Plan(
        steps=[
            PlanStep(step=1, tool=ToolName.PARSE_ENQUIRY, args={}, rationale="extract"),
            PlanStep(step=2, tool=ToolName.LOOKUP_JURISDICTION_RULE, args={}, rationale="lookup"),
            PlanStep(step=3, tool=ToolName.SCORE_LEAD, args={}, rationale="score"),
            PlanStep(step=4, tool=ToolName.WRITE_RECORD, args={}, rationale="write"),
        ]
    )


def _trace() -> ToolCallTrace:
    now = datetime.now(UTC)
    return ToolCallTrace(
        calls=[
            ToolCall(
                step=1,
                tool=ToolName.PARSE_ENQUIRY,
                args={},
                status=ToolCallStatus.SUCCESS,
                result={"name": "Sam Lee"},
                error=None,
                latency_ms=1.0,
                started_at=now,
                finished_at=now,
            )
        ]
    )


def _decision(**overrides) -> VerifierDecision:
    data = dict(
        passed=False,
        confidence=0.7,
        fabrication_detected=True,
        fabricated_fields=["phone"],
        plan_deviation_detected=False,
        deviation_details=None,
        reason="phone not in text",
    )
    data.update(overrides)
    return VerifierDecision(**data)


class _RepairFakeAdapter(ModelAdapter):
    provider_name = "fake"

    def __init__(
        self,
        *,
        extract: ExtractedFields,
        decision: VerifierDecision,
        plan: Plan | None = None,
    ) -> None:
        self._extract = extract
        self._decision = decision
        self._plan = plan or _plan()
        self.schemas: list[type] = []

    def complete_structured(self, request: StructuredCompletionRequest) -> StructuredCompletionResponse:
        self.schemas.append(request.response_schema)
        if request.response_schema is ExtractedFields:
            parsed: BaseModel = self._extract
        elif request.response_schema is VerifierDecision:
            parsed = self._decision
        elif request.response_schema is Plan:
            parsed = self._plan
        else:
            raise AssertionError(request.response_schema)
        return StructuredCompletionResponse(
            parsed=parsed,
            raw_response={},
            prompt_tokens=10,
            completion_tokens=5,
            latency_ms=1.0,
            model="fake",
        )


def _isolate_and_register_mocks() -> dict:
    saved = {name: Tool._registry.pop(name) for name in list(_CANONICAL) if name in Tool._registry}

    def _ok(data):
        return lambda self, args: ToolResult(success=True, data=data, error=None, latency_ms=1.0)

    specs = {
        "parse_enquiry": _ok(
            {
                "name": "Sam Lee",
                "email": "sam@example.com",
                "phone": "+6591234567",
                "country": "Singapore",
                "budget_band": "B",
                "asset_interest": "Whisky cask",
                "urgency": "Immediate",
            }
        ),
        "lookup_jurisdiction_rule": _ok(
            {
                "country": "Singapore",
                "requires_disclaimer": False,
                "restricted": False,
                "handling_note": "ok",
            }
        ),
        "score_lead": _ok({"score": 70, "breakdown": {"budget": 25}}),
        "write_record": _ok({"lead_id": "L1", "dedupe_hash": "h1"}),
    }
    for name, impl in specs.items():
        type(
            f"Mock_{name}",
            (Tool,),
            {
                "name": name,
                "description": name,
                "args_schema": _Permissive,
                "result_schema": _Permissive,
                "run": impl,
            },
        )
    return saved


def _restore(saved: dict) -> None:
    for name in list(_CANONICAL):
        Tool._registry.pop(name, None)
    Tool._registry.update(saved)


def test_augment_for_fabrication_names_fields() -> None:
    text = augment_for_fabrication("BASE", ["phone", "email"])
    assert "BASE" in text
    assert "phone" in text
    assert "email" in text


def test_augment_for_plan_deviation_includes_details() -> None:
    text = augment_for_plan_deviation("BASE", "step 2 skipped")
    assert "BASE" in text
    assert "step 2 skipped" in text


def test_reextract_strategy_succeeds_when_reverify_passes() -> None:
    saved = _isolate_and_register_mocks()
    try:
        adapter = _RepairFakeAdapter(
            extract=ExtractedFields(
                name="Sam Lee",
                email="sam@example.com",
                phone="+65 9123 4567",
                country="Singapore",
                budget_band="B",  # "around 20k"
                urgency="Within three months",  # "soon"
            ),
            decision=_decision(passed=True, fabrication_detected=False, fabricated_fields=[], reason="ok"),
        )
        result = attempt_repair(ENQUIRY, _plan(), _trace(), None, _decision(), adapter)
        assert isinstance(result, RepairResult)
        assert result.strategy_used == "reextract"
        assert result.succeeded is True
        assert result.new_record is not None
        repair_calls = result.new_trace.calls if result.new_trace else []
        assert all(c.tool != ToolName.WRITE_RECORD for c in repair_calls)
        assert ExtractedFields in adapter.schemas
        assert VerifierDecision in adapter.schemas
    finally:
        _restore(saved)


def test_reexecute_strategy_selected_for_plan_deviation() -> None:
    saved = _isolate_and_register_mocks()
    try:
        failing = _decision(
            fabrication_detected=False,
            plan_deviation_detected=True,
            deviation_details="mismatch",
            reason="deviation",
        )
        adapter = _RepairFakeAdapter(extract=ExtractedFields(name="Sam"), decision=failing)
        result = attempt_repair(ENQUIRY, _plan(), _trace(), None, failing, adapter)
        assert result.strategy_used == "reexecute"
        assert result.succeeded is False
    finally:
        _restore(saved)
