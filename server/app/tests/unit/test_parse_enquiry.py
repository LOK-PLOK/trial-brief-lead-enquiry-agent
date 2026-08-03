"""Unit tests for parse_enquiry prompt + tool (mocked adapter only).

Architecture preserved: single structured LLM extraction — no deterministic
budget/urgency/asset parsers. These tests lock the prompt guidance (official
WCC-TRIAL-A enums: BudgetBand A/B/C/D/Unknown, Urgency Immediate/Within
three months/Exploratory/Unknown, AssetInterest Whisky cask/Tequila
barrel/Wine/Multiple/Unspecified) and the tool's adapter wiring.
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
from app.schemas.extraction import AssetInterest, BudgetBand, ExtractedFields, Urgency
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
        for band in ('"A"', '"B"', '"C"', '"D"', '"Unknown"'):
            assert band in prompt

    def test_budget_band_field_bullet_lists_only_official_values(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        bullet = next(line for line in prompt.splitlines() if line.strip().startswith("- budget_band:"))
        assert '"A"' in bullet and '"B"' in bullet and '"C"' in bullet and '"D"' in bullet
        assert '"low"' not in bullet
        assert '"medium"' not in bullet
        assert '"high"' not in bullet

    def test_recognizes_common_currency_formats(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        for token in ("GBP", "AUD", "SGD", "EUR", "NZD", "CAD", "THB", "USD"):
            assert token in prompt

    def test_approximate_conversion_is_explicit_not_exact(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        assert "approximate" in prompt.lower()
        assert "do NOT attempt" in prompt or "not a precise" in prompt.lower()
        assert "Commas are thousands separators" in prompt or "thousands separators" in prompt
        assert "approximately" in prompt
        assert "around" in prompt
        assert "roughly" in prompt

    def test_maps_usd_thresholds(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        assert "USD 10,000" in prompt
        assert "USD 50,000" in prompt
        assert "USD 250,000" in prompt

    def test_unknown_only_when_insufficient_evidence(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        assert "ONLY when" in prompt
        assert "no usable size signal" in prompt or "insufficient evidence" in prompt.lower()

    def test_user_prompt_reminds_thresholds_and_approximators(self) -> None:
        text = build_parse_enquiry_user_prompt("approximately GBP 95,000 within 30 days")
        assert "approximately GBP 95,000" in text
        assert "10k / " in text or "10,000" in text
        assert "approximately/around" in text


class TestParseEnquiryBudgetBandRegressionExamples:
    """Prompt-contract regressions using the official Trial A samples
    (docs/WCC_Trial_A_Enquiry_Samples.md). Still LLM extraction only — these
    lock the guidance the model must follow, plus tool wiring when a
    scripted adapter returns the expected band.
    """

    @staticmethod
    def _extract_with(enquiry: str, budget_band: BudgetBand) -> tuple[dict, StructuredCompletionRequest]:
        extracted = ExtractedFields(
            name="Example",
            email="ex@example.com",
            phone=None,
            country="United Kingdom",
            budget_band=budget_band,
            asset_interest=AssetInterest.WHISKY_CASK,
            urgency=Urgency.WITHIN_THREE_MONTHS,
        )
        adapter = _ScriptedAdapter(responses=[extracted])
        tool = ParseEnquiryTool(adapter=adapter)
        result = tool.execute({"enquiry_text": enquiry})
        assert result.success is True
        assert result.data is not None
        return result.data, adapter.requests[0]

    def test_e03_usd_300000_maps_to_d(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        assert "USD 250,000 and above" in prompt
        data, request = self._extract_with(
            "I am considering an allocation in the region of USD 300,000 across two or "
            "three casks, with a preference for Islay.",
            BudgetBand.D,
        )
        assert data["budget_band"] == "D"
        assert "USD 300,000" in request.user_prompt

    def test_e14_eur_250000_maps_to_d(self) -> None:
        assert "EUR" in PARSE_ENQUIRY_SYSTEM_PROMPT
        data, _ = self._extract_with(
            "Two hundred and fifty thousand euros, possibly more.", BudgetBand.D
        )
        assert data["budget_band"] == "D"

    def test_e01_gbp_40000_maps_to_c(self) -> None:
        assert "GBP" in PARSE_ENQUIRY_SYSTEM_PROMPT
        data, _ = self._extract_with(
            "I am looking to commit around GBP 40,000 to a first purchase.", BudgetBand.C
        )
        assert data["budget_band"] == "C"

    def test_e10_usd_150000_maps_to_c(self) -> None:
        data, _ = self._extract_with(
            "Budget in the region of 150,000 US dollars.", BudgetBand.C
        )
        assert data["budget_band"] == "C"

    def test_e08_under_10k_us_maps_to_a(self) -> None:
        data, _ = self._extract_with(
            "We'd start small, maybe eight or nine thousand US.", BudgetBand.A
        )
        assert data["budget_band"] == "A"

    def test_e13_baht_200000_maps_to_a(self) -> None:
        assert "THB" in PARSE_ENQUIRY_SYSTEM_PROMPT
        data, _ = self._extract_with(
            "Maybe I can spend 200000 baht but I am not sure.", BudgetBand.A
        )
        assert data["budget_band"] == "A"

    def test_e15_usd_20000_maps_to_b(self) -> None:
        data, _ = self._extract_with("I'd said around USD 20,000 for an oloroso butt.", BudgetBand.B)
        assert data["budget_band"] == "B"

    def test_e11_nzd_30000_maps_to_b(self) -> None:
        assert "NZD" in PARSE_ENQUIRY_SYSTEM_PROMPT
        data, _ = self._extract_with("Looking at maybe NZD 30,000 for a first cask.", BudgetBand.B)
        assert data["budget_band"] == "B"


class TestParseEnquiryPromptAssetInterestGuidance:
    def test_defines_asset_interest_enum(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        assert "asset_interest guidance" in prompt.lower() or "## asset_interest guidance" in prompt
        for value in ('"Whisky cask"', '"Tequila barrel"', '"Wine"', '"Multiple"', '"Unspecified"'):
            assert value in prompt

    def test_never_returns_free_text(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        assert "Never return free text for this field" in prompt

    def test_mentions_whisky_and_tequila_and_wine_signals(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        assert "Scotch" in prompt or "Speyside" in prompt
        assert "tequila" in prompt.lower()
        assert "wine" in prompt.lower()

    def test_e06_tequila_barrels_maps_to_tequila_barrel(self) -> None:
        extracted = ExtractedFields(
            name=None,
            email="sam.reyes@example.com",
            asset_interest=AssetInterest.TEQUILA_BARREL,
        )
        adapter = _ScriptedAdapter(responses=[extracted])
        tool = ParseEnquiryTool(adapter=adapter)
        result = tool.execute({"enquiry_text": "Interested. Tequila barrels. sam.reyes@example.com"})
        assert result.success is True
        assert result.data["asset_interest"] == "Tequila barrel"


class TestParseEnquiryPromptUrgencyGuidance:
    def test_defines_urgency_bands(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        assert "## urgency guidance" in prompt
        for band in ('"Immediate"', '"Within three months"', '"Exploratory"', '"Unknown"'):
            assert band in prompt

    def test_never_mentions_legacy_low_medium_high_urgency(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        # The urgency-guidance section itself must not use the old vocabulary as values.
        section = prompt.split("## urgency guidance", 1)[1]
        section = section.split("## Hard requirements", 1)[0]
        assert '"low"' not in section
        assert '"medium"' not in section
        assert '"high"' not in section

    def test_includes_common_urgency_phrases(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        for phrase in (
            "no rush",
            "ASAP",
            "this month",
            "this quarter",
            "just exploring",
            "no particular hurry",
        ):
            assert phrase in prompt

    def test_negation_prefers_exploratory(self) -> None:
        prompt = PARSE_ENQUIRY_SYSTEM_PROMPT
        assert "no rush" in prompt
        assert "prefer Exploratory" in prompt


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
        text = build_parse_enquiry_user_prompt("SGD 120,000, this quarter")
        assert "SGD 120,000, this quarter" in text
        assert "untrusted" in text.lower()
        assert "budget_band" in text
        assert "urgency" in text
        assert "asset_interest" in text
        assert "unknown/unspecified only when evidence is genuinely insufficient" in text.lower()


class TestParseEnquiryTool:
    def test_successful_extraction_returns_structured_fields(self) -> None:
        extracted = ExtractedFields(
            name="Noah Berger",
            email="noah.berger@example.ca",
            phone="+1 416 555 7721",
            country="Canada",
            budget_band=BudgetBand.B,
            asset_interest=AssetInterest.WHISKY_CASK,
            urgency=Urgency.EXPLORATORY,
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

    def test_default_extracted_fields_are_unknown_and_unspecified(self) -> None:
        defaults = ExtractedFields()
        assert defaults.budget_band == BudgetBand.UNKNOWN
        assert defaults.urgency == Urgency.UNKNOWN
        assert defaults.asset_interest == AssetInterest.UNSPECIFIED

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
        adapter = _ScriptedAdapter(responses=[ExtractedFields(budget_band=BudgetBand.D)])
        tool = ParseEnquiryTool(adapter=adapter)
        tool.execute({"enquiry_text": "AUD 500,000 urgently"})
        assert len(adapter.requests) == 1
