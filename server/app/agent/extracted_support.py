"""Deterministic support classification for closed extracted enums.

This is NOT a second extractor and does NOT invent better enum values.
It only answers: given the enquiry text and the parser's chosen value,
is that value SUPPORTED, CONTRADICTED, or INSUFFICIENT_EVIDENCE?

Used by the Verifier's contradiction gate so a supported extraction can
never be quarantined because the Verifier LLM preferred another band.
Thresholds / phrase sets mirror the documented parse_enquiry bands
(docs/manual_testing.md, parse_enquiry_prompt.py) — verification-side
only; the parser itself is unchanged.
"""

from __future__ import annotations

import re
from enum import Enum

from app.schemas.verifier import ExtractedFieldLabel


class ClosedEnumField(str, Enum):
    BUDGET_BAND = "budget_band"
    URGENCY = "urgency"


_LOW_URGENCY_PHRASES: tuple[str, ...] = (
    "not urgent",
    "no rush at all",
    "no rush",
    "whenever convenient",
    "just browsing",
    "take your time",
    "low urgency",
    "someday",
)

_HIGH_URGENCY_PHRASES: tuple[str, ...] = (
    "as soon as possible",
    "right away",
    "immediately",
    "this week",
    "this month",
    "urgently",
    "urgent",
    "asap",
    "today",
    "expedite",
)

# Timeframes that support medium; high may also be reasonable for some.
_MEDIUM_URGENCY_PHRASES: tuple[str, ...] = (
    "within 30 days",
    "next few weeks",
    "next few months",
    "next quarter",
    "over the next quarter",
    "in the coming months",
    "coming months",
    "soon",
)

# Amount patterns intentionally require a currency cue, a thousands comma,
# a trailing k, or an under/less-than budget phrase — never bare phone
# fragments like "412" / "555" from "+61 412 555 018".

_RANGE_RE = re.compile(
    r"(?i)"
    r"(\d+(?:\.\d+)?)\s*[–\-]\s*(\d+(?:\.\d+)?)\s*([kK])\b"
)

_UNDER_RE = re.compile(
    r"(?i)\b(?:under|less than|below)\s+(?:(?:aud|cad|usd|gbp|eur)\s*)?(?:£|€|\$)?\s*"
    r"(\d{1,3}(?:,\d{3})+|\d+)([kK])?"
)

# Negative lookahead avoids treating the left side of "CAD 25–40k" as a
# bare 25 (which would wrongly imply a low band alongside the range).
_CURRENCY_SYMBOL_AMOUNT_RE = re.compile(
    r"(?i)(?:£|€|\$)\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)([kK])?(?!\s*[–\-]\s*\d)"
)

_CURRENCY_CODE_AMOUNT_RE = re.compile(
    r"(?i)\b(?:aud|cad|usd|gbp|eur)\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)([kK])?"
    r"(?!\s*[–\-]\s*\d)"
)

_COMMA_THOUSANDS_RE = re.compile(r"\b(\d{1,3}(?:,\d{3})+)\b")

_K_AMOUNT_RE = re.compile(r"(?i)\b(\d+(?:\.\d+)?)\s*[kK]\b")


def _parse_number(raw: str, *, k_suffix: bool) -> float:
    n = float(raw.replace(",", ""))
    if k_suffix:
        n *= 1000.0
    return n


def _amounts_in_enquiry(enquiry_text: str) -> list[float]:
    text = enquiry_text or ""
    amounts: list[float] = []

    for match in _RANGE_RE.finditer(text):
        amounts.append(_parse_number(match.group(1), k_suffix=True))
        amounts.append(_parse_number(match.group(2), k_suffix=True))

    for match in _UNDER_RE.finditer(text):
        amounts.append(_parse_number(match.group(1), k_suffix=bool(match.group(2))))

    for match in _CURRENCY_SYMBOL_AMOUNT_RE.finditer(text):
        amounts.append(_parse_number(match.group(1), k_suffix=bool(match.group(2))))

    for match in _CURRENCY_CODE_AMOUNT_RE.finditer(text):
        amounts.append(_parse_number(match.group(1), k_suffix=bool(match.group(2))))

    for match in _COMMA_THOUSANDS_RE.finditer(text):
        amounts.append(_parse_number(match.group(1), k_suffix=False))

    for match in _K_AMOUNT_RE.finditer(text):
        amounts.append(_parse_number(match.group(1), k_suffix=True))

    # Deduplicate while preserving order.
    seen: set[float] = set()
    ordered: list[float] = []
    for amount in amounts:
        if amount not in seen:
            seen.add(amount)
            ordered.append(amount)
    return ordered


def _band_for_amount(amount: float) -> str:
    if amount >= 70_000:
        return "high"
    if amount >= 20_000:
        return "medium"
    return "low"


def _normalize_enum(value: str | None) -> str | None:
    if value is None:
        return None
    v = str(value).strip().lower()
    return v or None


def classify_budget_band(enquiry_text: str, extracted_value: str | None) -> ExtractedFieldLabel:
    """Classify whether extracted budget_band is supported by the enquiry."""
    value = _normalize_enum(extracted_value)
    if value is None or value == "unknown":
        # unknown is only fabricatable when the enquiry clearly has a band —
        # that case is CONTRADICTED below when amounts/phrases are present.
        amounts = _amounts_in_enquiry(enquiry_text)
        text = (enquiry_text or "").lower()
        qualitative = any(
            p in text
            for p in (
                "mid-range",
                "medium-budget",
                "medium budget",
                "high budget",
                "low budget",
                "six figures",
                "small cask",
            )
        )
        if amounts or qualitative:
            return ExtractedFieldLabel.CONTRADICTED
        return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE

    text = (enquiry_text or "").lower()
    amounts = _amounts_in_enquiry(enquiry_text)

    if "under" in text or "less than" in text or "below" in text:
        under_amounts = [
            _parse_number(m.group(1), k_suffix=bool(m.group(2)))
            for m in _UNDER_RE.finditer(enquiry_text or "")
        ]
        if under_amounts and max(under_amounts) < 20_000:
            if value == "low":
                return ExtractedFieldLabel.SUPPORTED
            if value in {"medium", "high"}:
                return ExtractedFieldLabel.CONTRADICTED

    if amounts:
        bands = {_band_for_amount(a) for a in amounts}
        # Range spanning bands (e.g. only medium amounts, or mix): accept any
        # band that appears among the amount-derived bands.
        if value in bands:
            return ExtractedFieldLabel.SUPPORTED
        # Single clear high amount → medium/low contradicted.
        if bands == {"high"} and value != "high":
            return ExtractedFieldLabel.CONTRADICTED
        if bands == {"low"} and value != "low":
            return ExtractedFieldLabel.CONTRADICTED
        if bands == {"medium"} and value == "high":
            return ExtractedFieldLabel.CONTRADICTED
        if bands == {"medium"} and value == "low":
            return ExtractedFieldLabel.CONTRADICTED
        # Mixed amounts → prefer fail-open if value is one of the extremes.
        return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE

    if "six figures" in text or "high budget" in text:
        return (
            ExtractedFieldLabel.SUPPORTED
            if value == "high"
            else ExtractedFieldLabel.CONTRADICTED
        )
    if "mid-range" in text or "medium-budget" in text or "medium budget" in text:
        if value == "medium":
            return ExtractedFieldLabel.SUPPORTED
        if value == "high":
            return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE
        return ExtractedFieldLabel.CONTRADICTED
    if "low budget" in text or "small cask" in text:
        return (
            ExtractedFieldLabel.SUPPORTED
            if value == "low"
            else ExtractedFieldLabel.CONTRADICTED
        )

    return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE


def classify_urgency(enquiry_text: str, extracted_value: str | None) -> ExtractedFieldLabel:
    """Classify whether extracted urgency is supported by the enquiry."""
    value = _normalize_enum(extracted_value)
    text = (enquiry_text or "").lower()

    has_low = any(p in text for p in _LOW_URGENCY_PHRASES)
    has_high = any(p in text for p in _HIGH_URGENCY_PHRASES)
    has_medium = any(p in text for p in _MEDIUM_URGENCY_PHRASES)

    # Explicit low language wins when paired with a softer timeframe
    # ("next few months (not urgent)" → low is SUPPORTED).
    if has_low:
        if value == "low":
            return ExtractedFieldLabel.SUPPORTED
        if value == "high":
            return ExtractedFieldLabel.CONTRADICTED
        # medium may be a reasonable alternate reading of the timeframe —
        # difference of interpretation is NOT fabrication.
        if value == "medium":
            return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE
        if value == "unknown":
            return ExtractedFieldLabel.CONTRADICTED
        return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE

    if has_high:
        if value == "high":
            return ExtractedFieldLabel.SUPPORTED
        if value == "low":
            return ExtractedFieldLabel.CONTRADICTED
        # "within 30 days" also appears in medium list; high signal present
        # still makes medium a possible alternate (INSUFFICIENT), not fab.
        if value == "medium":
            return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE
        if value == "unknown":
            return ExtractedFieldLabel.CONTRADICTED
        return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE

    if has_medium:
        if value in {"medium", "high"}:
            # within 30 days / next few months: medium or high both ok.
            return ExtractedFieldLabel.SUPPORTED
        if value == "low":
            # Soft timeframe without "not urgent" — low is a stretch; treat
            # as insufficient (not a clear contradiction) unless we want
            # hard fail. User example: "Need it this week" is high phrase.
            return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE
        if value == "unknown":
            return ExtractedFieldLabel.CONTRADICTED
        return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE

    if value is None or value == "unknown":
        return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE

    # Extracted a band with no time-sensitivity language at all.
    return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE


def classify_closed_enum(
    enquiry_text: str,
    field: str,
    extracted_value: str | None,
) -> ExtractedFieldLabel | None:
    """Return a label for budget_band/urgency, or None for other fields."""
    key = field.strip().lower().rsplit(".", 1)[-1]
    if key == ClosedEnumField.BUDGET_BAND.value:
        return classify_budget_band(enquiry_text, extracted_value)
    if key == ClosedEnumField.URGENCY.value:
        return classify_urgency(enquiry_text, extracted_value)
    return None
