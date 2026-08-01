"""Unit tests for deterministic closed-enum support classification."""

from __future__ import annotations

import pytest

from app.agent.extracted_support import classify_budget_band, classify_urgency
from app.schemas.verifier import ExtractedFieldLabel


SUPPORTED = ExtractedFieldLabel.SUPPORTED
CONTRADICTED = ExtractedFieldLabel.CONTRADICTED
INSUFFICIENT = ExtractedFieldLabel.INSUFFICIENT_EVIDENCE


@pytest.mark.parametrize(
    ("enquiry", "value", "expected"),
    [
        ("Just browsing — under £5,000 someday. No rush at all.", "low", SUPPORTED),
        ("Budget around AUD 120,000 this month.", "high", SUPPORTED),
        ("around AUD 120k for a premium portfolio", "high", SUPPORTED),
        ("approximately £95,000 available", "high", SUPPORTED),
        ("£95,000", "high", SUPPORTED),
        ("approximately £95,000", "medium", CONTRADICTED),
        ("CAD 25–40k mid-range cask", "medium", SUPPORTED),
        ("roughly CAD 25-40k", "medium", SUPPORTED),
        ("Interested, no budget mentioned.", "unknown", INSUFFICIENT),
        ("AUD 120,000 — phone +61 412 555 018", "high", SUPPORTED),
    ],
)
def test_classify_budget_band(enquiry: str, value: str, expected: ExtractedFieldLabel) -> None:
    assert classify_budget_band(enquiry, value) is expected


@pytest.mark.parametrize(
    ("enquiry", "value", "expected"),
    [
        ("No rush at all.", "low", SUPPORTED),
        ("Not urgent.", "low", SUPPORTED),
        ("just browsing for now", "low", SUPPORTED),
        ("sometime in the next few months (not urgent)", "low", SUPPORTED),
        ("sometime in the next few months (not urgent)", "medium", INSUFFICIENT),
        ("Please call me urgently this month.", "high", SUPPORTED),
        ("Need it this week", "high", SUPPORTED),
        ("Need it this week", "low", CONTRADICTED),
        ("within 30 days", "medium", SUPPORTED),
        ("within 30 days", "high", SUPPORTED),
        ("ASAP please", "high", SUPPORTED),
        ("ASAP please", "low", CONTRADICTED),
        ("Hi, interested in casks.", "unknown", INSUFFICIENT),
    ],
)
def test_classify_urgency(enquiry: str, value: str, expected: ExtractedFieldLabel) -> None:
    assert classify_urgency(enquiry, value) is expected
