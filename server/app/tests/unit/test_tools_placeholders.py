"""Unit tests proving each of the four tools' `Tool.execute()` validation
boundary still behaves correctly now that real business logic is
implemented: invalid input must never reach `run()` -- it must raise
`ToolValidationError` instead. Deeper business-logic coverage for each
tool's real behavior lives in its own dedicated test module:
test_parse_enquiry.py, test_lookup_jurisdiction_rule.py, test_score_lead.py,
test_write_record.py.
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

    def complete_structured(self, request: StructuredCompletionRequest) -> StructuredCompletionResponse:
        raise NotImplementedError


class TestParseEnquiryTool:
    def test_valid_args_reach_run_and_return_a_controlled_failure_for_a_broken_adapter(self) -> None:
        """The fake adapter here always raises `NotImplementedError`, which
        is not one of `ParseEnquiryTool.run()`'s caught adapter-failure
        types (`LLMProviderError`/`LLMSchemaValidationError`) -- confirming
        validation passed (`run()` was reached at all) without asserting
        anything about a *real* adapter's behavior, which
        test_parse_enquiry.py covers directly."""
        tool = ParseEnquiryTool(adapter=_FakeAdapter())
        with pytest.raises(NotImplementedError):
            tool.execute({"enquiry_text": "Hi, I'm interested in whisky casks..."})

    def test_missing_required_field_raises_validation_error(self) -> None:
        tool = ParseEnquiryTool(adapter=_FakeAdapter())
        with pytest.raises(ToolValidationError):
            tool.execute({})


class TestLookupJurisdictionRuleTool:
    def test_valid_args_reach_run_and_return_a_real_result(self) -> None:
        tool = LookupJurisdictionRuleTool()
        result = tool.execute({"country": "Singapore"})
        assert result.success is True
        assert result.data["country"] == "Singapore"

    def test_missing_required_field_raises_validation_error(self) -> None:
        tool = LookupJurisdictionRuleTool()
        with pytest.raises(ToolValidationError):
            tool.execute({})

    def test_wrong_type_raises_validation_error(self) -> None:
        tool = LookupJurisdictionRuleTool()
        with pytest.raises(ToolValidationError):
            tool.execute({"country": 12345})


class TestScoreLeadTool:
    def test_valid_args_reach_run_and_return_a_real_score(self) -> None:
        tool = ScoreLeadTool()
        result = tool.execute({"extracted": {}, "jurisdiction_rule": VALID_JURISDICTION_RULE})
        assert result.success is True
        assert isinstance(result.data["score"], int)

    def test_missing_required_field_raises_validation_error(self) -> None:
        tool = ScoreLeadTool()
        with pytest.raises(ToolValidationError):
            tool.execute({})

    def test_incomplete_nested_jurisdiction_rule_raises_validation_error(self) -> None:
        tool = ScoreLeadTool()
        with pytest.raises(ToolValidationError):
            tool.execute({"extracted": {}, "jurisdiction_rule": {"country": "Singapore"}})


class TestWriteRecordTool:
    def test_missing_required_field_raises_validation_error(self) -> None:
        tool = WriteRecordTool()
        with pytest.raises(ToolValidationError):
            tool.execute({})

    def test_missing_score_raises_validation_error(self) -> None:
        tool = WriteRecordTool()
        with pytest.raises(ToolValidationError):
            tool.execute({"extracted": {}, "jurisdiction_rule": VALID_JURISDICTION_RULE})

    # `test_valid_args_reach_run_and_persist_a_record` deliberately lives in
    # test_write_record.py instead of here: unlike the other three tools,
    # exercising `run()` at all requires a real (throwaway, file-based)
    # database via the shared `db_session`/`test_settings` fixtures
    # (conftest.py), which this module's other tests don't need.
