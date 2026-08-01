"""Unit tests for agent/plan_utils.py."""

from __future__ import annotations

from app.agent.plan_utils import strip_write_record
from app.schemas.plan import Plan, PlanStep, ToolName


def test_strip_write_record_removes_and_renumbers() -> None:
    plan = Plan(
        steps=[
            PlanStep(step=1, tool=ToolName.PARSE_ENQUIRY, args={}, rationale="a"),
            PlanStep(step=2, tool=ToolName.WRITE_RECORD, args={}, rationale="b"),
            PlanStep(step=3, tool=ToolName.SCORE_LEAD, args={}, rationale="c"),
        ]
    )
    stripped = strip_write_record(plan)
    assert [s.tool for s in stripped.steps] == [ToolName.PARSE_ENQUIRY, ToolName.SCORE_LEAD]
    assert [s.step for s in stripped.steps] == [1, 2]


def test_strip_write_record_noop_when_absent() -> None:
    plan = Plan(
        steps=[
            PlanStep(step=1, tool=ToolName.PARSE_ENQUIRY, args={}, rationale="a"),
            PlanStep(step=2, tool=ToolName.SCORE_LEAD, args={}, rationale="b"),
        ]
    )
    stripped = strip_write_record(plan)
    assert len(stripped.steps) == 2
    assert stripped.steps[0].tool is ToolName.PARSE_ENQUIRY
