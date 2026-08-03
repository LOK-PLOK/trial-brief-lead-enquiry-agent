"""Unit tests for deterministic closed-enum support classification."""

from __future__ import annotations

import pytest

from app.agent.extracted_support import (
    classify_asset_interest,
    classify_budget_band,
    classify_closed_enum,
    classify_urgency,
)
from app.schemas.verifier import ExtractedFieldLabel


SUPPORTED = ExtractedFieldLabel.SUPPORTED
CONTRADICTED = ExtractedFieldLabel.CONTRADICTED
INSUFFICIENT = ExtractedFieldLabel.INSUFFICIENT_EVIDENCE


@pytest.mark.parametrize(
    ("enquiry", "value", "expected"),
    [
        # Official bands by approximate USD value: A<10k, B 10k-49,999,
        # C 50k-249,999, D>=250k.
        ("Just browsing — under £5,000 someday. No rush at all.", "A", SUPPORTED),
        ("Budget around AUD 120,000 this month.", "C", SUPPORTED),
        ("around AUD 120k for a premium portfolio", "C", SUPPORTED),
        ("approximately £95,000 available", "C", SUPPORTED),
        ("£95,000", "C", SUPPORTED),
        ("approximately £95,000", "B", CONTRADICTED),
        ("CAD 25–40k mid-range cask", "B", SUPPORTED),
        ("roughly CAD 25-40k", "B", SUPPORTED),
        ("Interested, no budget mentioned.", "Unknown", INSUFFICIENT),
        ("AUD 120,000 — phone +61 412 555 018", "C", SUPPORTED),
        # New currencies added by the official-enum migration.
        ("SGD 120,000 available this month", "C", SUPPORTED),
        ("NZD 30,000 for one cask", "B", SUPPORTED),
        ("200000 baht available now", "A", SUPPORTED),
        ("USD 300,000 to invest right away", "D", SUPPORTED),
        ("150,000 US dollars ready to go", "C", SUPPORTED),
        # Unknown is only fabricatable when the enquiry clearly has a band.
        ("AUD 120,000 this month", "Unknown", CONTRADICTED),
    ],
)
def test_classify_budget_band(enquiry: str, value: str, expected: ExtractedFieldLabel) -> None:
    assert classify_budget_band(enquiry, value) is expected


@pytest.mark.parametrize(
    ("enquiry", "value", "expected"),
    [
        ("No rush at all.", "Exploratory", SUPPORTED),
        ("Not urgent.", "Exploratory", SUPPORTED),
        ("just browsing for now", "Exploratory", SUPPORTED),
        ("sometime in the next few months (not urgent)", "Exploratory", SUPPORTED),
        ("sometime in the next few months (not urgent)", "Within three months", INSUFFICIENT),
        ("Please call me urgently this month.", "Immediate", SUPPORTED),
        ("Need it this week", "Immediate", SUPPORTED),
        ("Need it this week", "Exploratory", CONTRADICTED),
        ("within 30 days", "Within three months", SUPPORTED),
        ("within 30 days", "Immediate", SUPPORTED),
        ("ASAP please", "Immediate", SUPPORTED),
        ("ASAP please", "Exploratory", CONTRADICTED),
        ("Hi, interested in casks.", "Unknown", INSUFFICIENT),
        # E11 flip-flop regression: a bare calendar deadline with no other
        # anchor ("before the end of the year") must decisively SUPPORT
        # Within three months / Immediate, and never depend on the
        # Verifier LLM's own unguided guess about how far away that is.
        ("I'd want to have it done before the end of the year.", "Within three months", SUPPORTED),
        ("I'd want to have it done before the end of the year.", "Immediate", SUPPORTED),
        ("I'd want to have it done before the end of the year.", "Exploratory", INSUFFICIENT),
        ("Hoping to sort this out by year end, no rush though.", "Exploratory", SUPPORTED),
    ],
)
def test_classify_urgency(enquiry: str, value: str, expected: ExtractedFieldLabel) -> None:
    assert classify_urgency(enquiry, value) is expected


@pytest.mark.parametrize(
    ("enquiry", "value", "expected"),
    [
        ("Interested in a Scotch whisky cask.", "Whisky cask", SUPPORTED),
        ("Looking at a Speyside single malt.", "Whisky cask", SUPPORTED),
        ("I'd like a tequila barrel allocation.", "Tequila barrel", SUPPORTED),
        ("Interested in a vintage wine collection.", "Wine", SUPPORTED),
        # A specific type is named but the wrong category was extracted.
        ("Interested in a tequila barrel.", "Whisky cask", CONTRADICTED),
        ("Interested in a Scotch whisky cask.", "Wine", CONTRADICTED),
        # A specific type is named but the extractor said Unspecified.
        ("Interested in a Scotch whisky cask.", "Unspecified", CONTRADICTED),
        # No specific type mentioned at all — not enough evidence either way
        # (mirrors budget_band/urgency: "Unknown" with no signal is
        # INSUFFICIENT_EVIDENCE, not SUPPORTED).
        ("Interested in collectible assets, no specifics yet.", "Unspecified", INSUFFICIENT),
        ("Interested in collectible assets, no specifics yet.", "Whisky cask", INSUFFICIENT),
        # Multiple asset types genuinely mentioned.
        ("Interested in both whisky casks and tequila barrels.", "Multiple", SUPPORTED),
        # Only one type mentioned but extractor said Multiple: not enough
        # evidence to call it a clear contradiction either way.
        ("Interested in a Scotch whisky cask.", "Multiple", INSUFFICIENT),
        # Issue 2 fix: a bare, unattached "cask"/"casks" reference is this
        # (whisky cask) company's own default term for its product, so it
        # is itself a Whisky cask signal even with no whisky/region word.
        ("I'd like to put money into casks this quarter.", "Whisky cask", SUPPORTED),
        ("I would like information on cask investment please.", "Whisky cask", SUPPORTED),
        # The soft generic-cask signal never CONTRADICTS Unspecified -- only
        # a strongly-named spirit/region/distillery does that.
        ("I would like information on cask investment please.", "Unspecified", INSUFFICIENT),
        # A generic "cask" mention paired with a different, explicitly
        # named spirit does NOT default to whisky.
        ("I'd like to put money into a wine cask this quarter.", "Whisky cask", CONTRADICTED),
        ("I'd like to put money into a wine cask this quarter.", "Wine", SUPPORTED),
    ],
)
def test_classify_asset_interest(enquiry: str, value: str, expected: ExtractedFieldLabel) -> None:
    assert classify_asset_interest(enquiry, value) is expected


def test_classify_closed_enum_dispatches_all_three_fields() -> None:
    assert classify_closed_enum("under £5,000", "budget_band", "A") is SUPPORTED
    assert classify_closed_enum("No rush at all.", "urgency", "Exploratory") is SUPPORTED
    assert (
        classify_closed_enum("Interested in a whisky cask.", "asset_interest", "Whisky cask")
        is SUPPORTED
    )
    assert classify_closed_enum("hi", "email", "x@example.com") is None
