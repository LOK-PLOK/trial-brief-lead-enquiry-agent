"""Structured output of the `parse_enquiry` tool. See docs/architecture.md section 8.

This is the schema the `parse_enquiry` LLM call must return. Every field is
optional/nullable: the extractor must be able to say "not present" rather
than guess, since guessing is exactly the fabrication failure mode the
verifier is designed to catch (docs/architecture.md section 9).

Enum values are the official WCC-TRIAL-A field definitions from
docs/WCC_Trial_A_Enquiry_Samples.md (issued 3 August 2026). These are the
project's single source of truth for band vocabulary -- every layer
(prompts, tools, DB, API, frontend) uses these exact strings, never a
lowercase/legacy variant.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class BudgetBand(str, Enum):
    """Official Trial A budget bands (docs/WCC_Trial_A_Enquiry_Samples.md)."""

    A = "A"  # under USD 10,000
    B = "B"  # USD 10,000 to 49,999
    C = "C"  # USD 50,000 to 249,999
    D = "D"  # USD 250,000 and above
    UNKNOWN = "Unknown"


class Urgency(str, Enum):
    """Official Trial A urgency bands (docs/WCC_Trial_A_Enquiry_Samples.md)."""

    IMMEDIATE = "Immediate"
    WITHIN_THREE_MONTHS = "Within three months"
    EXPLORATORY = "Exploratory"
    UNKNOWN = "Unknown"


class AssetInterest(str, Enum):
    """Official Trial A asset-interest categories
    (docs/WCC_Trial_A_Enquiry_Samples.md). Was a free-text field prior to
    the official-samples migration; now a closed enum like budget_band/
    urgency so it is subject to the same fabrication check."""

    WHISKY_CASK = "Whisky cask"
    TEQUILA_BARREL = "Tequila barrel"
    WINE = "Wine"
    MULTIPLE = "Multiple"
    UNSPECIFIED = "Unspecified"


class ExtractedFields(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    country: str | None = None
    budget_band: BudgetBand = BudgetBand.UNKNOWN
    asset_interest: AssetInterest = AssetInterest.UNSPECIFIED
    urgency: Urgency = Urgency.UNKNOWN

    # TODO(tools/parse_enquiry): decide whether to carry a per-field
    # "evidence span" (verbatim substring from the source text) alongside
    # each value, to make the verifier's fabrication check cheaper/more
    # reliable. Not required by the brief, but worth considering.
