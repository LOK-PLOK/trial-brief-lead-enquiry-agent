"""Unit tests for parse_enquiry prompt + tool (mocked adapter only).

Architecture preserved: single structured LLM extraction — no deterministic
budget/urgency parsers. These tests lock the prompt guidance and the tool's
adapter wiring.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agent.prompts import parse_enquiry_prompt, planner_prompt, verifier_prompt
from app.agent.prompts.parse_enquiry_prompt import (
    PARSE_ENQUIRY_SYSTEM_PROMPT,
    build_parse_enquiry_user_prompt,
)
from app.llm.base import (
    LLMProviderError,
    LLMSchemaValidationError,
    ModelAdapter,
    StructuredCompletionRequest,
    StructuredCompletionResponse,
)
from app.schemas.extraction import BudgetBand, ExtractedFields, Urgency
from app.tools.parse_enquiry import ParseEnquiryTool


@dataclass
class _ScriptedAdapter(ModelAdapter):
    provider_name: str = "fake"
    responses: list[ExtractedFields | Exception] = field(default_factory=list)
    requests: list[StructuredCompletionRequest] = field(default_factory=list)

    def complete_structured(self, request: StructuredCompletionRequest) -> StructuredCompletionResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("no scripted responses left")
        next_item = self.responses.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return StructuredCompletionResponse(
            parsed=next_item,
            raw_response={},
            model="fake-model",
            prompt_tokens=10,
            completion_tokens=5,
            latency_ms=12.0,
        )


class TestParseEnquiryPromptBudgetGuidance:
    def test_defines_budget_bands(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        assert "budget_band guidance" in prompt.lower() or "## budget_band guidance" in prompt
        for band in ("low", "medium", "high", "unknown"):
            assert band in prompt

    def test_recognizes_common_currency_formats(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        for token in (
            "AUD 55k",
            "CAD 80,000",
            "USD 150k",
            "£5,000",
            "£95,000",
            "25–40k",
            "EUR",
            "under £5,000",
            "less than €10k",
        ):
            assert token in prompt

    def test_currency_symbols_commas_and_approximators_are_explicit(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        assert "equivalent evidence" in prompt or "equivalent" in prompt
        assert "£" in prompt and "€" in prompt and "$" in prompt
        assert "Commas are thousands separators" in prompt or "thousands separators" in prompt
        assert "approximately" in prompt
        assert "around" in prompt
        assert "roughly" in prompt
        assert "about" in prompt
        assert "do NOT change the band" in prompt or "do not change the band" in prompt.lower()

    def test_maps_high_medium_low_thresholds(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        assert "≥ 70,000" in prompt or "≥ 70,000" in prompt.replace("≥", ">=") or "70,000" in prompt
        assert "20,000–69,999" in prompt or "20,000" in prompt
        assert "under 20,000" in prompt or "under £5,000" in prompt

    def test_worked_examples_map_large_amounts_to_high(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        assert '"approximately £95,000" → high' in prompt or "approximately £95,000" in prompt
        assert "£95,000" in prompt and "→ high" in prompt
        assert "around AUD 120k" in prompt
        assert "roughly CAD 80,000" in prompt
        assert "Never return medium for these" in prompt or "never return medium" in prompt.lower()

    def test_unknown_only_when_insufficient_evidence(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        assert "ONLY when" in prompt
        assert "insufficient evidence" in prompt.lower() or "no usable size signal" in prompt

    def test_user_prompt_reminds_thresholds_and_approximators(self) -> None:
        text = build_parse_enquiry_user_prompt("approximately £95,000 within 30 days")
        assert "approximately £95,000" in text
        assert "≥70k → high" in text or "70k → high" in text
        assert "approximately/around/roughly/about" in text
        assert "approximately £95,000 → high" in text


class TestParseEnquiryBudgetBandRegressionExamples:
    """Prompt-contract regressions for E06-style amounts. Still LLM extraction
    only — these lock the guidance the model must follow, plus tool wiring
    when a scripted adapter returns the expected band.
    """

    @staticmethod
    def _extract_with(enquiry: str, budget_band: BudgetBand) -> tuple[dict, StructuredCompletionRequest]:
        extracted = ExtractedFields(
            name="Example",
            email="ex@example.com",
            phone=None,
            country="United Kingdom",
            budget_band=budget_band,
            asset_interest="cask allocation",
            urgency=Urgency.MEDIUM,
        )
        adapter = _ScriptedAdapter(responses=[extracted])
        tool = ParseEnquiryTool(adapter=adapter)
        result = tool.execute({"enquiry_text": enquiry})
        assert result.success is True
        assert result.data is not None
        return result.data, adapter.requests[0]

    def test_pound_95000_maps_to_high_in_prompt_and_tool_passthrough(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        assert "£95,000" in prompt
        data, request = self._extract_with("I'd like to place £95,000 into a cask.", BudgetBand.HIGH)
        assert data["budget_band"] == "high"
        assert "£95,000" in request.user_prompt
        assert "→ high" in request.system_prompt or "high" in request.system_prompt

    def test_approximately_pound_95000_maps_to_high(self) -> None:
        assert "approximately £95,000" in PARSE_ENQUIRY_SYSTEM_PROMPT
        data, request = self._extract_with(
            "approximately £95,000 into a cask allocation within 30 days",
            BudgetBand.HIGH,
        )
        assert data["budget_band"] == "high"
        assert "approximately £95,000 → high" in request.system_prompt or (
            "approximately £95,000" in request.system_prompt and "→ high" in request.system_prompt
        )

    def test_around_aud_120k_maps_to_high(self) -> None:
        assert "around AUD 120k" in PARSE_ENQUIRY_SYSTEM_PROMPT
        data, _ = self._extract_with("around AUD 120k for a premium portfolio", BudgetBand.HIGH)
        assert data["budget_band"] == "high"

    def test_roughly_cad_80000_maps_to_high(self) -> None:
        assert "roughly CAD 80,000" in PARSE_ENQUIRY_SYSTEM_PROMPT
        data, _ = self._extract_with("roughly CAD 80,000 in bonded Scotch casks", BudgetBand.HIGH)
        assert data["budget_band"] == "high"

    def test_under_pound_5000_maps_to_low(self) -> None:
        assert "under £5,000" in PARSE_ENQUIRY_SYSTEM_PROMPT
        data, _ = self._extract_with("maybe a small cask under £5,000 someday", BudgetBand.LOW)
        assert data["budget_band"] == "low"

    def test_cad_25_to_40k_maps_to_medium(self) -> None:
        assert "CAD 25–40k" in PARSE_ENQUIRY_SYSTEM_PROMPT
        data, _ = self._extract_with("roughly CAD 25–40k, not urgent", BudgetBand.MEDIUM)
        assert data["budget_band"] == "medium"


class TestParseEnquiryPromptUrgencyGuidance:
    def test_defines_urgency_bands(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        assert "## urgency guidance" in prompt
        for band in ("low", "medium", "high", "unknown"):
            assert f"- {band}:" in prompt or f"{band}:" in prompt

    def test_includes_common_urgency_phrases(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        for phrase in (
            "not urgent",
            "no rush",
            "ASAP",
            "this month",
            "next quarter",
            "urgently",
            "immediately",
            "whenever convenient",
        ):
            assert phrase in prompt

    def test_negation_prefers_low(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        assert "not urgent" in prompt
        assert "prefer low" in prompt.lower()


class TestParseEnquiryPromptIndependence:
    def test_shares_no_nontrivial_lines_with_planner_or_verifier(self) -> None:
        own = {line.strip() for line in PARSE_ENQUIRY_SYSTEM_PROMPT.splitlines() if line.strip()}
        planner = {line.strip() for line in planner_prompt.PLANNER_SYSTEM_PROMPT.splitlines() if line.strip()}
        verifier = {
            line.strip() for line in verifier_prompt.VERIFIER_SYSTEM_PROMPT.splitlines() if line.strip()
        }
        # Allow short shared function words only — no identical instructional lines.
        nontrivial = {line for line in own if len(line) > 40}
        assert nontrivial.isdisjoint(planner)
        assert nontrivial.isdisjoint(verifier)

    def test_user_prompt_embeds_enquiry_and_reminds_guidance(self) -> None:
        text = build_parse_enquiry_user_prompt("CAD 25–40k, not urgent")
        assert "CAD 25–40k, not urgent" in text
        assert "untrusted" in text.lower()
        assert "budget_band" in text
        assert "urgency" in text
        assert "unknown only when evidence is insufficient" in text.lower()


class TestParseEnquiryTool:
    def test_successful_extraction_returns_structured_fields(self) -> None:
        extracted = ExtractedFields(
            name="Noah Berger",
            email="noah.berger@example.ca",
            phone="+1 416 555 7721",
            country="Canada",
            budget_band=BudgetBand.MEDIUM,
            asset_interest="mid-range cask",
            urgency=Urgency.LOW,
        )
        adapter = _ScriptedAdapter(responses=[extracted])
        tool = ParseEnquiryTool(adapter=adapter)

        result = tool.execute(
            {"enquiry_text": ("Noah Berger, Canada, noah.berger@example.ca. CAD 25–40k, not urgent.")}
        )

        assert result.success is True
        assert result.data == extracted.model_dump(mode="json")
        assert len(adapter.requests) == 1
        assert adapter.requests[0].system_prompt == PARSE_ENQUIRY_SYSTEM_PROMPT
        assert adapter.requests[0].response_schema is ExtractedFields
        assert "CAD 25–40k" in adapter.requests[0].user_prompt

    def test_uses_parse_enquiry_prompt_module_not_planner_or_verifier(self) -> None:
        adapter = _ScriptedAdapter(responses=[ExtractedFields()])
        tool = ParseEnquiryTool(adapter=adapter)
        tool.execute({"enquiry_text": "hi"})
        request = adapter.requests[0]
        assert request.system_prompt == parse_enquiry_prompt.PARSE_ENQUIRY_SYSTEM_PROMPT
        assert request.system_prompt != planner_prompt.PLANNER_SYSTEM_PROMPT
        assert request.system_prompt != verifier_prompt.VERIFIER_SYSTEM_PROMPT

    def test_provider_error_is_controlled_tool_failure(self) -> None:
        adapter = _ScriptedAdapter(responses=[LLMProviderError("rate limited")])
        tool = ParseEnquiryTool(adapter=adapter)
        result = tool.execute({"enquiry_text": "hi"})
        assert result.success is False
        assert "LLMProviderError" in (result.error or "")

    def test_schema_validation_error_is_controlled_tool_failure(self) -> None:
        adapter = _ScriptedAdapter(responses=[LLMSchemaValidationError("bad schema")])
        tool = ParseEnquiryTool(adapter=adapter)
        result = tool.execute({"enquiry_text": "hi"})
        assert result.success is False
        assert "LLMSchemaValidationError" in (result.error or "")

    def test_makes_exactly_one_adapter_call(self) -> None:
        adapter = _ScriptedAdapter(responses=[ExtractedFields(budget_band=BudgetBand.HIGH)])
        tool = ParseEnquiryTool(adapter=adapter)
        tool.execute({"enquiry_text": "AUD 120,000 urgently"})
        assert len(adapter.requests) == 1
