"""Unit tests proving each of the four tools does exactly what the brief for
this stage requires: validate input via `Tool.execute()`, then raise
`NotImplementedError` -- no business logic. Invalid input must never reach
that point; it must raise `ToolValidationError` instead.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.llm.base import ModelAdapter, StructuredCompletionRequest, StructuredCompletionResponse
from app.tools.base import ToolValidationError
from app.tools.lookup_jurisdiction_rule import LookupJurisdictionRuleTool
from app.tools.parse_enquiry import ParseEnquiryTool
from app.tools.score_lead import ScoreLeadTool
from app.tools.write_record import WriteRecordTool

VALID_JURISDICTION_RULE = {
    "country": "Singapore",
    "requires_disclaimer": True,
    "restricted": False,
    "handling_note": "Standard disclosure required.",
}

VALID_SCORE_RESULT = {"score": 78, "breakdown": {"budget": 40, "urgency": 20, "jurisdiction_risk": 18}}


@dataclass
class _FakeAdapter(ModelAdapter):
    provider_name: str = "fake"

    def complete_structured(
        self, request: StructuredCompletionRequest
    ) -> StructuredCompletionResponse:
        raise NotImplementedError


class TestParseEnquiryTool:
    def test_valid_args_reach_run_and_raise_not_implemented(self) -> None:
        tool = ParseEnquiryTool(adapter=_FakeAdapter())
        with pytest.raises(NotImplementedError):
            tool.execute({"enquiry_text": "Hi, I'm interested in whisky casks..."})

    def test_missing_required_field_raises_validation_error(self) -> None:
        tool = ParseEnquiryTool(adapter=_FakeAdapter())
        with pytest.raises(ToolValidationError):
            tool.execute({})


class TestLookupJurisdictionRuleTool:
    def test_valid_args_reach_run_and_raise_not_implemented(self) -> None:
        tool = LookupJurisdictionRuleTool()
        with pytest.raises(NotImplementedError):
            tool.execute({"country": "Singapore"})

    def test_missing_required_field_raises_validation_error(self) -> None:
        tool = LookupJurisdictionRuleTool()
        with pytest.raises(ToolValidationError):
            tool.execute({})

    def test_wrong_type_raises_validation_error(self) -> None:
        tool = LookupJurisdictionRuleTool()
        with pytest.raises(ToolValidationError):
            tool.execute({"country": 12345})


class TestScoreLeadTool:
    def test_valid_args_reach_run_and_raise_not_implemented(self) -> None:
        tool = ScoreLeadTool()
        with pytest.raises(NotImplementedError):
            tool.execute({"extracted": {}, "jurisdiction_rule": VALID_JURISDICTION_RULE})

    def test_missing_required_field_raises_validation_error(self) -> None:
        tool = ScoreLeadTool()
        with pytest.raises(ToolValidationError):
            tool.execute({})

    def test_incomplete_nested_jurisdiction_rule_raises_validation_error(self) -> None:
        tool = ScoreLeadTool()
        with pytest.raises(ToolValidationError):
            tool.execute({"extracted": {}, "jurisdiction_rule": {"country": "Singapore"}})


class TestWriteRecordTool:
    def test_valid_args_reach_run_and_raise_not_implemented(self) -> None:
        tool = WriteRecordTool()
        with pytest.raises(NotImplementedError):
            tool.execute(
                {
                    "extracted": {},
                    "jurisdiction_rule": VALID_JURISDICTION_RULE,
                    "score": VALID_SCORE_RESULT,
                }
            )

    def test_missing_required_field_raises_validation_error(self) -> None:
        tool = WriteRecordTool()
        with pytest.raises(ToolValidationError):
            tool.execute({})

    def test_missing_score_raises_validation_error(self) -> None:
        tool = WriteRecordTool()
        with pytest.raises(ToolValidationError):
            tool.execute({"extracted": {}, "jurisdiction_rule": VALID_JURISDICTION_RULE})
