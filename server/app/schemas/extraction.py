"""Structured output of the `parse_enquiry` tool. See docs/architecture.md section 8.

This is the schema the `parse_enquiry` LLM call must return. Every field is
optional/nullable: the extractor must be able to say "not present" rather
than guess, since guessing is exactly the fabrication failure mode the
verifier is designed to catch (docs/architecture.md section 9).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class BudgetBand(str, Enum):
    # TODO(schemas): confirm exact band boundaries against the 15 supplied
    # enquiry samples once available; placeholder bands for now.
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class Urgency(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class ExtractedFields(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    country: str | None = None
    budget_band: BudgetBand = BudgetBand.UNKNOWN
    asset_interest: str | None = None
    urgency: Urgency = Urgency.UNKNOWN

    # TODO(tools/parse_enquiry): decide whether to carry a per-field
    # "evidence span" (verbatim substring from the source text) alongside
    # each value, to make the verifier's fabrication check cheaper/more
    # reliable. Not required by the brief, but worth considering.
