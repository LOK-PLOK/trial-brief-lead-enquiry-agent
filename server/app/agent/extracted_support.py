"""Deterministic support classification for closed extracted enums.

This is NOT a second extractor and does NOT invent better enum values.
It only answers: given the enquiry text and the parser's chosen value,
is that value SUPPORTED, CONTRADICTED, or INSUFFICIENT_EVIDENCE?

Used by the Verifier's contradiction gate so a supported extraction can
never be quarantined because the Verifier LLM preferred another band.
Thresholds / phrase sets mirror the official Trial A bands (docs/
WCC_Trial_A_Enquiry_Samples.md, parse_enquiry_prompt.py) — verification-side
only; the parser itself is unchanged.

Known limitation: amount detection is regex-based over digits/currency
tokens only. Enquiries that spell out amounts in words (e.g. "fifteen
thousand pounds") are not parsed into a number here; in that case this
module fails open (INSUFFICIENT_EVIDENCE) rather than guessing, exactly
as it does for any other enquiry with no machine-readable amount. The
LLM extractor (parse_enquiry) has no such limitation since it reads the
full enquiry text like a human.
"""

from __future__ import annotations

import re
from enum import Enum

from app.schemas.verifier import ExtractedFieldLabel


class ClosedEnumField(str, Enum):
    BUDGET_BAND = "budget_band"
    URGENCY = "urgency"
    ASSET_INTEREST = "asset_interest"


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

# Timeframes that support "Within three months"; "Immediate" may also be
# reasonable for some of these.
_MEDIUM_URGENCY_PHRASES: tuple[str, ...] = (
    "within 30 days",
    "next few weeks",
    "next few months",
    "next quarter",
    "over the next quarter",
    "in the coming months",
    "coming months",
    "soon",
    # Bare calendar deadlines with no further anchor (mirrors
    # parse_enquiry_prompt.py/verifier_prompt.py's urgency guidance): the
    # model -- and this classifier -- has no way to know today's actual
    # date, so these default to "Within three months" rather than being
    # left for the Verifier LLM to freelance a guess about how many months
    # away "the end of the year" really is (root cause of run-to-run
    # verifier flip-flopping on this exact phrase at temperature 0).
    "end of the year",
    "end of this year",
    "by year end",
    "before year end",
    "before the new year",
)

# Amount patterns intentionally require a currency cue, a thousands comma,
# a trailing k, or an under/less-than budget phrase — never bare phone
# fragments like "412" / "555" from "+61 412 555 018".

_CCY_CODE = r"(?:GBP|EUR|AUD|CAD|SGD|NZD|THB|USD)"
_CCY_SYMBOL = r"[£€$]"
_CCY_PREFIX = rf"(?:{_CCY_CODE}|{_CCY_SYMBOL})"
# Currency named as a trailing word, e.g. "80k Australian", "200000 baht",
# "150,000 US dollars", "40,000 pounds".
_CCY_SUFFIX_WORD = (
    r"(?:Australian(?:\s+dollars?)?|pounds?(?:\s+sterling)?|euros?|baht|"
    r"US\s+dollars?|Singapore\s+dollars?|New\s+Zealand\s+dollars?)"
)

# Approximate, rounded USD conversion factors -- mirrors the guidance given
# to the LLM extractor in parse_enquiry_prompt.py. Deliberately coarse: this
# gate only needs to know which of the four official bands an amount falls
# in, not its precise USD value.
_CURRENCY_FACTORS: dict[str, float] = {
    "gbp": 1.275,
    "£": 1.275,
    "pound": 1.275,
    "eur": 1.075,
    "€": 1.075,
    "euro": 1.075,
    "aud": 0.675,
    "australian": 0.675,
    "cad": 0.725,
    "sgd": 0.745,
    "singapore": 0.745,
    "nzd": 0.6,
    "new zealand": 0.6,
    "thb": 1 / 35,
    "baht": 1 / 35,
    "usd": 1.0,
    "$": 1.0,
    "us": 1.0,
}


def _currency_factor(token: str | None) -> float:
    """Approximate USD conversion factor for a detected currency token.

    Defaults to 1.0 (treat as already USD-equivalent) when no currency
    token is detected or it is unrecognized -- the same fallback the
    pre-migration classifier used unconditionally for every currency.
    """
    if not token:
        return 1.0
    key = token.strip().lower()
    if key in _CURRENCY_FACTORS:
        return _CURRENCY_FACTORS[key]
    if key.endswith("s") and key[:-1] in _CURRENCY_FACTORS:
        return _CURRENCY_FACTORS[key[:-1]]
    return 1.0


_RANGE_RE = re.compile(
    rf"(?i)(?:({_CCY_PREFIX})\s*)?(\d+(?:\.\d+)?)\s*[–\-]\s*(\d+(?:\.\d+)?)\s*([kK])\b"
)

_UNDER_RE = re.compile(
    rf"(?i)\b(?:under|less than|below)\s+(?:({_CCY_PREFIX})\s*)?"
    r"(\d{1,3}(?:,\d{3})+|\d+)([kK])?"
)

# Negative lookahead avoids treating the left side of "CAD 25–40k" as a
# bare 25 (which would wrongly imply a low band alongside the range).
_PREFIX_AMOUNT_RE = re.compile(
    rf"(?i)({_CCY_PREFIX})\s*(\d{{1,3}}(?:,\d{{3}})+|\d+(?:\.\d+)?)([kK])?(?!\s*[–\-]\s*\d)"
)

_SUFFIX_AMOUNT_RE = re.compile(
    rf"(?i)(\d{{1,3}}(?:,\d{{3}})+|\d+(?:\.\d+)?)\s*([kK])?\s+({_CCY_SUFFIX_WORD})"
)

_COMMA_THOUSANDS_RE = re.compile(r"\b(\d{1,3}(?:,\d{3})+)\b")

_K_AMOUNT_RE = re.compile(r"(?i)\b(\d+(?:\.\d+)?)\s*[kK]\b")


def _parse_number(raw: str, *, k_suffix: bool) -> float:
    n = float(raw.replace(",", ""))
    if k_suffix:
        n *= 1000.0
    return n


def _amounts_in_enquiry_usd(enquiry_text: str) -> list[float]:
    """Approximate USD-equivalent amounts mentioned in the enquiry text.

    Currency-aware (mirrors parse_enquiry_prompt.py's conversion guidance):
    an amount with a detected GBP/EUR/AUD/CAD/SGD/NZD/THB cue is converted
    to its approximate USD equivalent before banding; bare digit amounts
    with no currency cue are treated as already USD-equivalent, matching
    the pre-migration classifier's behaviour. Spans already matched by a
    currency-aware pattern are not re-counted by a later bare pattern.
    """
    text = enquiry_text or ""
    accepted_spans: list[tuple[int, int]] = []
    amounts: list[float] = []

    def _overlaps(span: tuple[int, int]) -> bool:
        return any(not (span[1] <= s or span[0] >= e) for s, e in accepted_spans)

    def _accept(match: re.Match, values: list[float]) -> None:
        span = match.span()
        if _overlaps(span):
            return
        accepted_spans.append(span)
        amounts.extend(values)

    for match in _RANGE_RE.finditer(text):
        factor = _currency_factor(match.group(1))
        lo = _parse_number(match.group(2), k_suffix=True) * factor
        hi = _parse_number(match.group(3), k_suffix=True) * factor
        _accept(match, [lo, hi])

    for match in _UNDER_RE.finditer(text):
        factor = _currency_factor(match.group(1))
        value = _parse_number(match.group(2), k_suffix=bool(match.group(3))) * factor
        _accept(match, [value])

    for match in _PREFIX_AMOUNT_RE.finditer(text):
        factor = _currency_factor(match.group(1))
        value = _parse_number(match.group(2), k_suffix=bool(match.group(3))) * factor
        _accept(match, [value])

    for match in _SUFFIX_AMOUNT_RE.finditer(text):
        factor = _currency_factor(match.group(3))
        value = _parse_number(match.group(1), k_suffix=bool(match.group(2))) * factor
        _accept(match, [value])

    for match in _COMMA_THOUSANDS_RE.finditer(text):
        _accept(match, [_parse_number(match.group(1), k_suffix=False)])

    for match in _K_AMOUNT_RE.finditer(text):
        _accept(match, [_parse_number(match.group(1), k_suffix=True)])

    # Deduplicate while preserving order.
    seen: set[float] = set()
    ordered: list[float] = []
    for amount in amounts:
        if amount not in seen:
            seen.add(amount)
            ordered.append(amount)
    return ordered


def _band_for_usd_amount(amount: float) -> str:
    """Official Trial A bands: A <10k, B 10k-49,999, C 50k-249,999, D >=250k."""
    if amount >= 250_000:
        return "d"
    if amount >= 50_000:
        return "c"
    if amount >= 10_000:
        return "b"
    return "a"


def _normalize_enum(value: str | None) -> str | None:
    if value is None:
        return None
    v = str(value).strip().lower()
    return v or None


def classify_budget_band(enquiry_text: str, extracted_value: str | None) -> ExtractedFieldLabel:
    """Classify whether extracted budget_band (A/B/C/D/Unknown) is
    supported by the enquiry."""
    value = _normalize_enum(extracted_value)
    if value is None or value == "unknown":
        # Unknown is only fabricatable when the enquiry clearly has a band —
        # that case is CONTRADICTED below when amounts/phrases are present.
        amounts = _amounts_in_enquiry_usd(enquiry_text)
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
    amounts = _amounts_in_enquiry_usd(enquiry_text)

    if "under" in text or "less than" in text or "below" in text:
        under_amounts = []
        for m in _UNDER_RE.finditer(enquiry_text or ""):
            factor = _currency_factor(m.group(1))
            under_amounts.append(_parse_number(m.group(2), k_suffix=bool(m.group(3))) * factor)
        if under_amounts and max(under_amounts) < 10_000:
            if value == "a":
                return ExtractedFieldLabel.SUPPORTED
            if value in {"b", "c", "d"}:
                return ExtractedFieldLabel.CONTRADICTED

    if amounts:
        bands = {_band_for_usd_amount(a) for a in amounts}
        # Range spanning bands (e.g. only "b" amounts, or a mix): accept any
        # band that appears among the amount-derived bands.
        if value in bands:
            return ExtractedFieldLabel.SUPPORTED
        # A single, unambiguous amount-derived band contradicts every other
        # band. A mixed set of amount-derived bands (e.g. a range spanning
        # two bands) fails open below instead of guessing which one is
        # "the" band.
        if len(bands) == 1:
            return ExtractedFieldLabel.CONTRADICTED
        return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE

    if "high budget" in text:
        return (
            ExtractedFieldLabel.SUPPORTED
            if value == "d"
            else ExtractedFieldLabel.CONTRADICTED
        )
    if "six figures" in text:
        # Six figures spans USD 100k-999k, i.e. bands C and D. Treat both as
        # SUPPORTED (difference of interpretation, not fabrication) and A/B
        # as CONTRADICTED (clearly too low for six figures).
        return (
            ExtractedFieldLabel.SUPPORTED
            if value in {"c", "d"}
            else ExtractedFieldLabel.CONTRADICTED
        )
    if "mid-range" in text or "medium-budget" in text or "medium budget" in text:
        # Qualitative "mid-range" spans the two middle bands (B/C).
        if value in {"b", "c"}:
            return ExtractedFieldLabel.SUPPORTED
        return ExtractedFieldLabel.CONTRADICTED
    if "low budget" in text or "small cask" in text:
        # Qualitative "low budget" spans the two smallest bands (A/B).
        if value in {"a", "b"}:
            return ExtractedFieldLabel.SUPPORTED
        return ExtractedFieldLabel.CONTRADICTED

    return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE


def classify_urgency(enquiry_text: str, extracted_value: str | None) -> ExtractedFieldLabel:
    """Classify whether extracted urgency (Immediate/Within three months/
    Exploratory/Unknown) is supported by the enquiry."""
    value = _normalize_enum(extracted_value)
    text = (enquiry_text or "").lower()

    has_exploratory = any(p in text for p in _LOW_URGENCY_PHRASES)
    has_immediate = any(p in text for p in _HIGH_URGENCY_PHRASES)
    has_within_three_months = any(p in text for p in _MEDIUM_URGENCY_PHRASES)

    # Explicit low-urgency language wins when paired with a softer timeframe
    # ("next few months (not urgent)" → Exploratory is SUPPORTED).
    if has_exploratory:
        if value == "exploratory":
            return ExtractedFieldLabel.SUPPORTED
        if value == "immediate":
            return ExtractedFieldLabel.CONTRADICTED
        # "Within three months" may be a reasonable alternate reading of the
        # timeframe — difference of interpretation is NOT fabrication.
        if value == "within three months":
            return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE
        if value == "unknown":
            return ExtractedFieldLabel.CONTRADICTED
        return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE

    if has_immediate:
        if value == "immediate":
            return ExtractedFieldLabel.SUPPORTED
        if value == "exploratory":
            return ExtractedFieldLabel.CONTRADICTED
        # "within 30 days" also appears in the medium list; an immediate
        # signal present still makes "Within three months" a possible
        # alternate (INSUFFICIENT), not fabrication.
        if value == "within three months":
            return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE
        if value == "unknown":
            return ExtractedFieldLabel.CONTRADICTED
        return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE

    if has_within_three_months:
        if value in {"within three months", "immediate"}:
            # "within 30 days" / "next few months": both readings are ok.
            return ExtractedFieldLabel.SUPPORTED
        if value == "exploratory":
            # Soft timeframe without "not urgent" — Exploratory is a
            # stretch; treat as insufficient rather than a hard fail.
            return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE
        if value == "unknown":
            return ExtractedFieldLabel.CONTRADICTED
        return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE

    if value is None or value == "unknown":
        return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE

    # Extracted a band with no time-sensitivity language at all.
    return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE


_WHISKY_SIGNAL_WORDS: tuple[str, ...] = (
    "whisky",
    "whiskey",
    "scotch",
    "single malt",
    "speyside",
    "islay",
    "highland",
    "campbeltown",
    "macallan",
    "glengoyne",
    "glenfarclas",
    "springbank",
)
_TEQUILA_SIGNAL_WORDS: tuple[str, ...] = ("tequila",)
_WINE_SIGNAL_WORDS: tuple[str, ...] = ("wine",)

# "cask" (this company's own everyday word for its whisky product, as
# opposed to "barrel" for tequila -- mirrors parse_enquiry_prompt.py's
# asset_interest guidance) is a deliberately SOFT signal, not a strong one
# like the specific whisky words above: it only ever adds support for a
# `Whisky cask` extraction (see classify_asset_interest), it never forces a
# CONTRADICTED verdict against an `Unspecified` extraction the way a named
# spirit/region/distillery does. A generic "cask" mention paired with a
# different spirit (e.g. "wine cask") is not treated as a whisky signal.
_GENERIC_CASK_WORDS: tuple[str, ...] = ("cask",)


def _asset_signals_present(enquiry_text: str) -> set[str]:
    """Which official asset_interest categories the enquiry text itself
    gives a strong keyword signal for. Returns a subset of {"whisky cask",
    "tequila barrel", "wine"} -- never "multiple" or "unspecified", which
    are judgments about the signals, not signals themselves. Does not
    include the soft generic-cask signal; see `_has_generic_cask_signal`."""
    text = (enquiry_text or "").lower()
    signals: set[str] = set()
    if any(word in text for word in _WHISKY_SIGNAL_WORDS):
        signals.add("whisky cask")
    if any(word in text for word in _TEQUILA_SIGNAL_WORDS):
        signals.add("tequila barrel")
    if any(word in text for word in _WINE_SIGNAL_WORDS):
        signals.add("wine")
    return signals


def _has_generic_cask_signal(enquiry_text: str, strong_signals: set[str]) -> bool:
    """True when the enquiry mentions "cask(s)" generically -- with no
    other, more specific spirit/asset already named for that enquiry (a
    named spirit always takes precedence over the generic default)."""
    if strong_signals:
        return False
    text = (enquiry_text or "").lower()
    return any(word in text for word in _GENERIC_CASK_WORDS)


def _asset_category_for_value(value: str) -> str | None:
    """Best-effort category for an extracted asset_interest string.

    Matches by keyword rather than requiring the exact canonical enum
    string, so minor wording variance (plural "casks", a longer phrase
    like "whisky cask portfolio") still classifies correctly -- this
    mirrors how classify_budget_band/classify_urgency key off phrases
    rather than exact strings.
    """
    if any(word in value for word in _WHISKY_SIGNAL_WORDS):
        return "whisky cask"
    if any(word in value for word in _TEQUILA_SIGNAL_WORDS):
        return "tequila barrel"
    if any(word in value for word in _WINE_SIGNAL_WORDS):
        return "wine"
    if "multiple" in value:
        return "multiple"
    if "unspecified" in value or not value:
        return "unspecified"
    return None


def classify_asset_interest(enquiry_text: str, extracted_value: str | None) -> ExtractedFieldLabel:
    """Classify whether extracted asset_interest (Whisky cask/Tequila
    barrel/Wine/Multiple/Unspecified) is supported by the enquiry.

    Same philosophy as budget_band/urgency: a closed-enum value is only
    CONTRADICTED when the enquiry text clearly signals a different,
    specific category; otherwise fail open to INSUFFICIENT_EVIDENCE.
    """
    value = _normalize_enum(extracted_value)
    signals = _asset_signals_present(enquiry_text)
    generic_cask = _has_generic_cask_signal(enquiry_text, signals)

    if value is None:
        return ExtractedFieldLabel.CONTRADICTED if signals else ExtractedFieldLabel.INSUFFICIENT_EVIDENCE

    category = _asset_category_for_value(value)
    if category is None:
        # Unrecognized value string: cannot judge either way.
        return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE

    if category == "unspecified":
        # Unspecified is only fabricatable when the enquiry clearly names a
        # specific asset type the parser should have picked up on. A bare
        # generic "cask" mention (no named spirit) is deliberately too soft
        # to contradict Unspecified -- it only supports Whisky cask below.
        return ExtractedFieldLabel.CONTRADICTED if signals else ExtractedFieldLabel.INSUFFICIENT_EVIDENCE

    if category == "multiple":
        if len(signals) >= 2:
            return ExtractedFieldLabel.SUPPORTED
        # Zero or one distinct asset type mentioned: not clear evidence of
        # more than one, but not a clear contradiction either.
        return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE

    # category is one of the three specific asset types.
    if category in signals:
        return ExtractedFieldLabel.SUPPORTED
    if category == "whisky cask" and generic_cask:
        # This is a whisky cask investment company: a bare, unattached
        # "cask"/"casks" reference is that company's own default term for
        # its whisky product (mirrors parse_enquiry_prompt.py), so it
        # supports a Whisky cask extraction even with no explicit
        # whisky/region/distillery word present.
        return ExtractedFieldLabel.SUPPORTED
    if signals:
        # The enquiry names a different, specific asset type instead.
        return ExtractedFieldLabel.CONTRADICTED
    return ExtractedFieldLabel.INSUFFICIENT_EVIDENCE


def classify_closed_enum(
    enquiry_text: str,
    field: str,
    extracted_value: str | None,
) -> ExtractedFieldLabel | None:
    """Return a label for budget_band/urgency/asset_interest, or None for
    other fields."""
    key = field.strip().lower().rsplit(".", 1)[-1]
    if key == ClosedEnumField.BUDGET_BAND.value:
        return classify_budget_band(enquiry_text, extracted_value)
    if key == ClosedEnumField.URGENCY.value:
        return classify_urgency(enquiry_text, extracted_value)
    if key == ClosedEnumField.ASSET_INTEREST.value:
        return classify_asset_interest(enquiry_text, extracted_value)
    return None
