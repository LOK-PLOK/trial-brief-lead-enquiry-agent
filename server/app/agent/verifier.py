"""The independent Verifier. See docs/architecture.md section 9 and
docs/contracts.md section 3.

Rule (docs/development-rules.md): the Verifier is completely independent.
This module must NOT import anything from agent/planner.py or
agent/prompts/planner_prompt.py, and must issue its own, freshly
constructed, stateless LLM call -- never a self-check appended to a prior
call's context (enforced structurally -- see
tests/unit/test_verifier.py's import-boundary test).

Public surface: exactly one function, `verify()`. Everything else here is a
private (underscore-prefixed) helper.
"""

from __future__ import annotations

import re

from app.agent.extracted_support import classify_closed_enum
from app.agent.prompts.verifier_prompt import VERIFIER_SYSTEM_PROMPT, build_verifier_user_prompt
from app.core.config import get_settings
from app.core.logging import get_logger, run_id_ctx, stage_ctx
from app.llm.base import ModelAdapter, StructuredCompletionRequest
from app.schemas.plan import Plan, ToolName
from app.schemas.tool_trace import ToolCallStatus, ToolCallTrace
from app.schemas.verifier import ExtractedFieldLabel, ExtractedFieldVerdict, VerifierDecision
from app.services.dedupe import compute_dedupe_hash

logger = get_logger(__name__)

# Extracted-field claims are enquiry-only; deterministic code never clears them.
_EXTRACTED_FIELD_TOKENS: frozenset[str] = frozenset(
    {
        "name",
        "email",
        "phone",
        "country",
        "budget_band",
        "asset_interest",
        "urgency",
    }
)


class VerifierValidationError(Exception):
    """Raised only when the adapter itself violates its own contract by
    returning something that isn't a `VerifierDecision` (docs/contracts.md
    section 5's structured-output guarantee) -- a bug in the adapter, not a
    normal verification outcome. Not eligible for any extra retry: per this
    task's requirement, `verify()` retries only according to the adapter's
    own internal contract (see llm/base.py), never with a Verifier-level
    retry loop layered on top (unlike the Planner's guardrail retry, there
    is no deterministic business rule here for the Verifier itself to
    re-attempt against)."""


def _successful_results_by_tool(tool_call_trace: ToolCallTrace) -> dict[str, dict]:
    """Latest successful result per tool name (last write wins)."""
    out: dict[str, dict] = {}
    for call in tool_call_trace.calls:
        if call.status == ToolCallStatus.SUCCESS and isinstance(call.result, dict):
            out[call.tool.value] = call.result
    return out


def _normalize_field_claim(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def _is_extracted_field_claim(name: str) -> bool:
    """True for LLM fabricated_fields entries that refer to enquiry-extracted
    fields (never cleared by deterministic reconciliation)."""
    n = _normalize_field_claim(name)
    # e.g. "email", "extracted.email", "final_record.extracted.phone"
    for token in _EXTRACTED_FIELD_TOKENS:
        if n == token or n.endswith(f".{token}") or f".{token}." in n:
            # Avoid treating "country" inside "jurisdiction_rule.country" as extracted:
            if "jurisdiction" in n and token == "country":
                return False
            return True
    return False


def _is_dedupe_hash_claim(name: str) -> bool:
    return "dedupe_hash" in _normalize_field_claim(name)


def _is_tool_derived_field_claim(name: str) -> bool:
    """True for claims about jurisdiction_rule / score outputs (clearable when
    deterministic tool comparison proves a match)."""
    if _is_extracted_field_claim(name) or _is_dedupe_hash_claim(name):
        return False
    n = _normalize_field_claim(name)
    markers = (
        "jurisdiction_rule",
        "handling_note",
        "requires_disclaimer",
        "score_breakdown",
        "restricted",
    )
    if any(m in n for m in markers):
        return True
    # Bare "score" / "*.score" but not score_breakdown (already handled).
    if n == "score" or n.endswith(".score"):
        return True
    return False


def _tool_derived_claim_still_mismatched(claim: str, mismatches: set[str]) -> bool:
    """Whether an LLM tool-derived claim should be kept given deterministic mismatches."""
    n = _normalize_field_claim(claim)
    if "handling_note" in n:
        return "handling_note" in mismatches or "jurisdiction_rule" in mismatches
    if "requires_disclaimer" in n:
        return "requires_disclaimer" in mismatches or "jurisdiction_rule" in mismatches
    if "score_breakdown" in n or n.endswith(".breakdown") or n == "breakdown":
        return "score_breakdown" in mismatches
    if n == "score" or n.endswith(".score"):
        return "score" in mismatches
    if "jurisdiction" in n or "restricted" in n:
        return "jurisdiction_rule" in mismatches or bool(
            mismatches.intersection({"handling_note", "requires_disclaimer"})
        )
    # Unknown tool-derived wording: clear only when every tool-derived field matched.
    return bool(mismatches)


def _detect_extracted_vs_parse_mismatches(
    tool_call_trace: ToolCallTrace,
    final_record: dict,
) -> list[str]:
    """Return extracted field names where final_record differs from successful
    parse_enquiry output.

    Planner placeholders are irrelevant: the successful parse result is the
    authoritative extracted payload the Executor should have assembled into
    `final_record.extracted`. Enquiry-truthfulness remains an LLM judgment;
    this check only catches assembly/tampering vs parse.
    """
    parse = _successful_results_by_tool(tool_call_trace).get(ToolName.PARSE_ENQUIRY.value)
    extracted = final_record.get("extracted")
    if not isinstance(parse, dict) or not isinstance(extracted, dict):
        return []
    fabricated: list[str] = []
    for token in _EXTRACTED_FIELD_TOKENS:
        # Only keys the parse tool actually returned — LeadRecord/ExtractedFields
        # may fill enum defaults (e.g. unknown) for omitted keys, which is not
        # executor tampering relative to the parse payload.
        if token not in parse:
            continue
        if extracted.get(token) != parse.get(token):
            fabricated.append(token)
    return _unique_preserve(fabricated)


def _detect_tool_derived_mismatches(
    tool_call_trace: ToolCallTrace,
    final_record: dict,
) -> list[str]:
    """Return tool-derived field names that differ from successful tool results.

    Covers jurisdiction_rule (and nested handling_note / requires_disclaimer)
    plus score / score_breakdown. Does not cover dedupe_hash.
    """
    results = _successful_results_by_tool(tool_call_trace)
    fabricated: list[str] = []

    lookup = results.get(ToolName.LOOKUP_JURISDICTION_RULE.value)
    record_rule = final_record.get("jurisdiction_rule")
    if "jurisdiction_rule" in final_record:
        if lookup is not None:
            if record_rule != lookup:
                fabricated.append("jurisdiction_rule")
                if isinstance(lookup, dict) and isinstance(record_rule, dict):
                    if lookup.get("handling_note") != record_rule.get("handling_note"):
                        fabricated.append("handling_note")
                    if lookup.get("requires_disclaimer") != record_rule.get("requires_disclaimer"):
                        fabricated.append("requires_disclaimer")
        elif record_rule not in (None, {}):
            fabricated.append("jurisdiction_rule")

    score_tool = results.get(ToolName.SCORE_LEAD.value)
    record_score = final_record.get("score")
    record_breakdown = final_record.get("score_breakdown")
    has_score_fields = "score" in final_record or "score_breakdown" in final_record
    if has_score_fields:
        if score_tool is not None:
            if "score" in final_record and record_score != score_tool.get("score"):
                fabricated.append("score")
            if "score_breakdown" in final_record and record_breakdown != score_tool.get("breakdown"):
                fabricated.append("score_breakdown")
        elif record_score is not None or record_breakdown not in (None, {}):
            fabricated.append("score")

    return _unique_preserve(fabricated)


def _dedupe_hash_mismatches(final_record: dict, tool_call_trace: ToolCallTrace) -> bool:
    """True when final_record.dedupe_hash fails deterministic validation.

    Prefer matching a successful write_record result when present; otherwise
    recompute from extracted email/phone (verify runs before write_record).
    """
    if "dedupe_hash" not in final_record:
        return False
    record_hash = final_record.get("dedupe_hash")
    if record_hash is None:
        return False

    write = _successful_results_by_tool(tool_call_trace).get(ToolName.WRITE_RECORD.value)
    if write is not None and write.get("dedupe_hash") is not None:
        return record_hash != write.get("dedupe_hash")

    extracted = final_record.get("extracted") or {}
    if not isinstance(extracted, dict):
        return True
    expected = compute_dedupe_hash(extracted.get("email"), extracted.get("phone"))
    return record_hash != expected


def _detect_derived_field_fabrication(
    tool_call_trace: ToolCallTrace,
    final_record: dict,
) -> list[str]:
    """Deterministic mismatches for tool-derived + computed fields.

    Kept as a single list for tests/callers; reconciliation uses the split
    helpers above so LLM claims can be cleared per category.
    """
    fabricated = list(_detect_tool_derived_mismatches(tool_call_trace, final_record))
    if _dedupe_hash_mismatches(final_record, tool_call_trace):
        fabricated.append("dedupe_hash")
    return _unique_preserve(fabricated)


def _unique_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for name in items:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


# --- Extracted-field contradiction gate ----------------------------------
#
# Root cause of repeated urgency/budget false quarantines: the Verifier LLM
# was asked to both (a) judge support and (b) fill a flat `fabricated_fields`
# list from free text. It frequently produced self-contradictory pairs
# ("high is contradicted because AUD120k is high") or treated a preferred
# alternate enum as fabrication. Prompt-only fixes cannot make that reliable.
#
# Architecture:
# 1. LLM returns structured `extracted_field_verdicts` (SUPPORTED /
#    CONTRADICTED / INSUFFICIENT_EVIDENCE) — classify support, do not
#    re-extract.
# 2. Deterministic closed-enum classifier (`extracted_support.py`) independently
#    labels budget_band / urgency from enquiry + chosen value.
# 3. This gate rebuilds extracted fabrication claims: ONLY CONTRADICTED may
#    enter `fabricated_fields`. SUPPORTED and INSUFFICIENT_EVIDENCE never do.
#    Deterministic SUPPORTED wins over an LLM CONTRADICTED (fail-open toward
#    accepting a supported extraction). Deterministic CONTRADICTED forces a
#    fabrication claim even if the LLM said SUPPORTED (real contradictions).
#
# Re-running `parse_enquiry` was evaluated and rejected as the primary fix:
# a second LLM extraction is still non-deterministic, can disagree with the
# first parse even when both are reasonable, and does not answer "is the
# chosen value supported?" — it invents another value. The successful
# parse_enquiry result in the tool trace remains the assembly authority
# (`final_record.extracted` must match it); enquiry support for closed enums
# is answered deterministically here.
#
# A secondary reason-text self-contradiction stripper remains below for
# free-text fields where the LLM still only has `reason` + fabricated_fields.

_CLOSED_ENUM_FIELDS: frozenset[str] = frozenset({"budget_band", "urgency"})


def _extracted_token(name: str) -> str:
    return _normalize_field_claim(name).rsplit(".", 1)[-1]


def _verdicts_by_field(
    verdicts: list[ExtractedFieldVerdict],
) -> dict[str, ExtractedFieldVerdict]:
    out: dict[str, ExtractedFieldVerdict] = {}
    for verdict in verdicts:
        out[_extracted_token(verdict.field)] = verdict
    return out


def _merge_labels(
    llm_label: ExtractedFieldLabel | None,
    deterministic_label: ExtractedFieldLabel | None,
) -> ExtractedFieldLabel | None:
    """Combine LLM structured label with deterministic closed-enum label.

    Precedence:
    - Deterministic SUPPORTED always wins (supported extraction never fails
      for preference / LLM self-contradiction).
    - Deterministic CONTRADICTED wins over LLM SUPPORTED/INSUFFICIENT
      (actual contradiction with enquiry).
    - Otherwise prefer LLM label when present; else deterministic.
    """
    if deterministic_label is ExtractedFieldLabel.SUPPORTED:
        return ExtractedFieldLabel.SUPPORTED
    if deterministic_label is ExtractedFieldLabel.CONTRADICTED:
        return ExtractedFieldLabel.CONTRADICTED
    if llm_label is not None:
        return llm_label
    return deterministic_label


def _apply_extracted_contradiction_gate(
    decision: VerifierDecision,
    *,
    enquiry_text: str,
    final_record: dict,
) -> tuple[VerifierDecision, list[str]]:
    """Rebuild extracted fabrication claims from structured + deterministic labels.

    Only CONTRADICTED extracted fields may remain in `fabricated_fields`.
    Non-extracted claims (tool-derived / dedupe / unknown) are preserved for
    later reconciliation. Does not invent enum values.
    """
    warnings: list[str] = []
    extracted = final_record.get("extracted")
    if not isinstance(extracted, dict):
        extracted = {}

    verdict_map = _verdicts_by_field(decision.extracted_field_verdicts)
    non_extracted_claims = [
        claim for claim in decision.fabricated_fields if not _is_extracted_field_claim(claim)
    ]

    # Candidates: LLM fabricated extracted claims, structured verdicts, and
    # every present closed enum (always classified deterministically).
    candidates: set[str] = set()
    for claim in decision.fabricated_fields:
        if _is_extracted_field_claim(claim):
            candidates.add(_extracted_token(claim))
    candidates.update(verdict_map.keys())
    for field in _CLOSED_ENUM_FIELDS:
        if field in extracted and extracted.get(field) not in (None, ""):
            candidates.add(field)

    extracted_fabricated: list[str] = []
    merged_verdicts: list[ExtractedFieldVerdict] = []

    for field in sorted(candidates):
        if field not in _EXTRACTED_FIELD_TOKENS:
            continue
        value = extracted.get(field)
        value_str = value if isinstance(value, str) else (str(value) if value is not None else None)
        llm_verdict = verdict_map.get(field)
        llm_label = llm_verdict.label if llm_verdict is not None else None
        det_label = classify_closed_enum(enquiry_text, field, value_str)
        final_label = _merge_labels(llm_label, det_label)

        if final_label is None:
            # Free-text extracted field with no structured verdict: keep an
            # existing LLM fabrication claim (enquiry judgment still needed).
            if any(_extracted_token(c) == field for c in decision.fabricated_fields):
                extracted_fabricated.append(field)
            continue

        note = llm_verdict.note if llm_verdict is not None else ""
        llm_claimed_fabricated = any(
            _extracted_token(c) == field for c in decision.fabricated_fields
        )

        if det_label is not None and llm_label is not None and det_label != llm_label:
            warnings.append(
                f"{field}: LLM={llm_label.value} deterministic={det_label.value} "
                f"→ gate={final_label.value}"
            )
            if not note:
                note = f"gate merged LLM={llm_label.value} with deterministic={det_label.value}"
        elif det_label is not None and not note:
            note = f"deterministic {det_label.value}"

        merged_verdicts.append(
            ExtractedFieldVerdict(field=field, label=final_label, note=note)
        )

        if final_label is ExtractedFieldLabel.CONTRADICTED:
            extracted_fabricated.append(field)
            if not llm_claimed_fabricated:
                warnings.append(
                    f"added CONTRADICTED extracted fabrication claim on '{field}' "
                    f"(deterministic/structured gate)"
                )
        elif llm_claimed_fabricated:
            # LLM listed it as fabricated but gate says not CONTRADICTED.
            warnings.append(
                f"discarded non-CONTRADICTED extracted fabrication claim on '{field}' "
                f"(label={final_label.value})"
            )

    rebuilt = _unique_preserve(non_extracted_claims + extracted_fabricated)
    fabrication_detected = bool(rebuilt)
    updates: dict = {
        "fabricated_fields": rebuilt,
        "fabrication_detected": fabrication_detected,
        "extracted_field_verdicts": merged_verdicts or list(decision.extracted_field_verdicts),
    }
    if not fabrication_detected and not decision.plan_deviation_detected:
        # May still be flipped back to fail by derived-field reconciliation.
        updates["passed"] = True
        if (decision.fabrication_detected or not decision.passed) and warnings:
            updates["reason"] = (
                "Extracted-field contradiction gate: only CONTRADICTED labels "
                "may fabricate; cleared non-contradicted extracted claims. "
                + "; ".join(warnings[:5])
            )

    return decision.model_copy(update=updates), warnings


# --- Secondary: reason-text self-contradiction stripper ------------------
#
# Fallback for free-text fields when the LLM lists a field in
# fabricated_fields while its own reason affirms the same value. Closed
# enums are primarily handled by the contradiction gate above.

_AFFIRMING_TERMS: tuple[str, ...] = (
    "supported",
    "correctly maps",
    "correctly map",
    "correctly extracted",
    "correctly reflects",
    "correctly reflect",
    "accurately reflects",
    "accurately reflect",
    "clearly falls into",
    "clearly supports",
    "clearly support",
    "is consistent with",
    "reasonable interpretation",
    "reasonably supports",
    "reasonably support",
    "matches the enquiry",
    "correctly identifies",
    "is a reasonable",
)

# Mirrors the worked urgency examples already documented in
# agent/prompts/verifier_prompt.py ("ASAP"/"urgently" -> high; "not
# urgent"/"no rush" -> low). Used only to sanity-check whether the
# verifier's OWN justification quotes a phrase that is itself a signal for
# the exact value it just contradicted -- this is not a re-implementation
# of parse_enquiry's extraction logic, and it never assigns or changes a
# field's value, only whether a fabrication claim about it may be trusted.
_URGENCY_SIGNAL_PHRASES: dict[str, tuple[str, ...]] = {
    "high": ("asap", "urgently", "immediately", "right away", "as soon as possible"),
    "low": ("not urgent", "no rush", "someday", "whenever convenient", "just browsing"),
}

_CLAUSE_SPLIT_RE = re.compile(r"(?<=[.;!?])\s+|\s+\bbut\b\s+|\s+\bhowever\b\s+|\s+\byet\b\s+", re.IGNORECASE)


def _resolve_field_claim_value(final_record: dict, field: str) -> str | None:
    """Best-effort lookup of the final record's own value for a
    fabricated-field claim -- used only to sanity-check the verifier's
    reasoning against itself, never to re-derive or override the value."""
    key = _normalize_field_claim(field).rsplit(".", 1)[-1]
    extracted = final_record.get("extracted")
    if isinstance(extracted, dict) and key in extracted:
        value = extracted.get(key)
        if isinstance(value, str):
            return value
    value = final_record.get(key)
    return value if isinstance(value, str) else None


def _field_reason_clauses(reason: str, field: str) -> list[str]:
    """Clauses of `reason` that actually mention `field` (by its snake_case
    name or a spaced variant), so a multi-field reason doesn't let affirming
    language about one field clear a fabrication claim about another."""
    aliases = {field.lower(), field.lower().replace("_", " ")}
    clauses = _CLAUSE_SPLIT_RE.split(reason)
    return [clause for clause in clauses if any(alias in clause.lower() for alias in aliases)]


def _reason_self_contradicts_claim(reason: str, field: str, value: str) -> bool:
    """True when `reason` itself affirms `value` for `field` in the same
    breath as flagging it fabricated -- i.e. the explanation contradicts
    its own verdict. Scoped to clauses that mention `field` and `value`
    together so unrelated fields in a longer reason are not affected."""
    if not reason or not value:
        return False
    value_lower = value.lower()
    for clause in _field_reason_clauses(reason, field):
        lower = clause.lower()
        if value_lower not in lower:
            continue
        if any(term in lower for term in _AFFIRMING_TERMS):
            return True
        if field.lower() == "urgency":
            signals = _URGENCY_SIGNAL_PHRASES.get(value_lower, ())
            if any(signal in lower for signal in signals):
                return True
    return False


def _strip_self_contradictory_claims(
    decision: VerifierDecision,
    final_record: dict,
) -> tuple[VerifierDecision, list[str]]:
    """Discard any `fabricated_fields` claim whose own justification in
    `reason` affirms the disputed value, and report what was discarded.

    Fails open: a self-contradictory claim is never trusted in either
    direction, so it is dropped rather than kept. If dropping claims empties
    `fabricated_fields` and there is no (separately detected) plan
    deviation, the decision is corrected to `passed=True`. Deterministic
    reconciliation still runs afterwards and can independently re-add any
    field that genuinely mismatches tool output or the enquiry-derived
    parse result -- this only removes claims disproven by the model's own
    text, it does not grant blanket immunity to a field.
    """
    warnings: list[str] = []
    if not decision.fabricated_fields:
        return decision, warnings

    kept: list[str] = []
    for field in decision.fabricated_fields:
        value = _resolve_field_claim_value(final_record, field)
        if value and _reason_self_contradicts_claim(decision.reason, field, value):
            warnings.append(
                f"discarded self-contradictory fabrication claim on '{field}': reason "
                f"affirms '{value}' is supported/consistent while also listing it in "
                "fabricated_fields"
            )
            continue
        kept.append(field)

    if not warnings:
        return decision, warnings

    fabrication_detected = bool(kept)
    updates: dict = {
        "fabricated_fields": kept,
        "fabrication_detected": fabrication_detected,
    }
    if not fabrication_detected and not decision.plan_deviation_detected:
        updates["passed"] = True
        updates["reason"] = (
            "Verifier consistency check discarded self-contradictory fabrication "
            "claim(s): " + "; ".join(warnings)
        )
    return decision.model_copy(update=updates), warnings


def _reconcile_fabrication_claims(
    decision: VerifierDecision,
    *,
    tool_mismatches: list[str],
    parse_mismatches: list[str],
    dedupe_mismatch: bool,
) -> VerifierDecision:
    """Reconcile LLM fabrication claims with deterministic evidence.

    - Extracted-field claims from the LLM are kept (enquiry judgment), and
      deterministic parse-vs-final mismatches are added.
    - Tool-derived claims are cleared when the corresponding tool outputs match.
    - dedupe_hash claims are cleared when recomputation (or write_record) matches.
    - Deterministic mismatches the LLM missed are added.
    - If no fabricated fields remain and there is no plan deviation, pass.
    """
    mismatch_set = set(tool_mismatches)
    original_fields = list(decision.fabricated_fields)
    had_fabrication = decision.fabrication_detected or bool(original_fields)

    reconciled: list[str] = []
    cleared_derived_or_dedupe = False

    for claim in original_fields:
        if _is_extracted_field_claim(claim):
            reconciled.append(claim)
            continue
        if _is_dedupe_hash_claim(claim):
            if dedupe_mismatch:
                reconciled.append(claim)
            else:
                cleared_derived_or_dedupe = True
            continue
        if _is_tool_derived_field_claim(claim):
            if _tool_derived_claim_still_mismatched(claim, mismatch_set):
                reconciled.append(claim)
            else:
                cleared_derived_or_dedupe = True
            continue
        # Unknown claim: keep (do not invent clearance).
        reconciled.append(claim)

    for name in parse_mismatches:
        if name not in reconciled:
            reconciled.append(name)
    for name in tool_mismatches:
        if name not in reconciled:
            reconciled.append(name)
    if dedupe_mismatch and "dedupe_hash" not in reconciled:
        reconciled.append("dedupe_hash")

    reconciled = _unique_preserve(reconciled)
    fabrication_detected = bool(reconciled)

    updates: dict = {
        "fabrication_detected": fabrication_detected,
        "fabricated_fields": reconciled,
    }

    if fabrication_detected:
        updates["passed"] = False
        if not decision.fabrication_detected:
            # Deterministic mismatch the LLM missed. Do not clobber an existing
            # fail reason (e.g. plan deviation already explained).
            if decision.passed or not decision.reason:
                updates["reason"] = (
                    "final_record fields do not match successful tool outputs "
                    "or deterministic checks: " + ", ".join(reconciled)
                )
        elif not decision.reason:
            updates["reason"] = "Fabrication detected: " + ", ".join(reconciled)
    else:
        if had_fabrication and cleared_derived_or_dedupe:
            logger.info(
                "cleared LLM fabrication claims on tool-derived/computed fields that "
                "match deterministic evidence",
                extra={
                    "event": "verifier_fabrication_reconcile_clear",
                    "cleared_from": original_fields,
                },
            )
        if not decision.plan_deviation_detected and (
            decision.passed or (had_fabrication and cleared_derived_or_dedupe)
        ):
            updates["passed"] = True
            if cleared_derived_or_dedupe and not decision.passed:
                updates["reason"] = (
                    "Tool-derived and deterministically computed fields match successful "
                    "tool outputs / recomputation; LLM fabrication claims on those fields "
                    "were cleared."
                )

    return decision.model_copy(update=updates)


def _detect_plan_deviation(plan: Plan, tool_call_trace: ToolCallTrace) -> str | None:
    """Deterministic, non-LLM ground truth for one of the two failure
    classes the brief requires (docs/contracts.md section 3 / brief section
    3.4): "the executor skipped, reordered or substituted a tool relative
    to the plan". Unlike fabrication, this is mechanically checkable from
    `plan.steps` and `tool_call_trace.calls` alone, so it is never left to
    the model's judgment in isolation -- see `verify()` for how this
    overrides the LLM's own `plan_deviation_detected` claim whenever it
    disagrees with this ground truth, satisfying "the Verifier must never
    trust the Planner or Executor outputs" for the one dimension that does
    not require natural-language understanding to check.

    Returns a human-readable description of the first deviation found, or
    `None` if the trace is consistent with the plan. A trace that is simply
    *shorter* than the plan -- because execution legitimately stopped after
    a tool failure (docs/contracts.md section 2) -- is NOT a deviation;
    only a mismatched, reordered, substituted, or extra step is.
    """
    planned = [step.tool for step in plan.steps]
    executed = [call.tool for call in tool_call_trace.calls]

    if len(executed) > len(planned):
        return (
            f"Trace contains {len(executed)} tool call(s) but the plan only specified "
            f"{len(planned)} step(s) -- an unplanned step was executed."
        )
    for position, (planned_tool, executed_tool) in enumerate(zip(planned, executed), start=1):
        if planned_tool != executed_tool:
            return (
                f"Step {position}: the plan specified '{planned_tool.value}' but the trace "
                f"shows '{executed_tool.value}' executed in its place."
            )
    return None


def _log_call(
    *,
    model: str,
    user_prompt: str,
    decision: VerifierDecision,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: float,
) -> None:
    """Structured log of the one adapter call `verify()` ever makes:
    prompt, response, latency, and token usage (this task's explicit
    logging requirement). Ephemeral stdout tier (docs/architecture.md
    section 12) -- the durable per-run trace in SQLite is populated later
    by agent/orchestrator.py, not here."""
    logger.info(
        "verifier llm call completed",
        extra={
            "event": "verifier_llm_call",
            "model": model,
            "system_prompt": VERIFIER_SYSTEM_PROMPT,
            "user_prompt": user_prompt,
            "response": decision.model_dump(mode="json", by_alias=True),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "latency_ms": latency_ms,
        },
    )


def verify(
    enquiry_text: str,
    plan: Plan,
    tool_call_trace: ToolCallTrace,
    final_record: dict,
    adapter: ModelAdapter,
) -> VerifierDecision:
    """Call the Verifier LLM and return a validated `VerifierDecision`.

    - Uses only the provider-agnostic `ModelAdapter` interface (never a
      concrete provider class) -- docs/contracts.md section 5.
    - Uses structured outputs: the call sets `response_schema=
      VerifierDecision`, and the adapter's own contract guarantees
      `response.parsed` validates against it (or raises) before this
      function ever sees it.
    - Issues exactly one adapter call, built entirely from
      `agent/prompts/verifier_prompt.py` -- a fresh `StructuredCompletionRequest`
      with no messages/history field at all (docs/development-rules.md:
      "Each LLM call is isolated"), and no import from `agent/planner.py`
      or `agent/prompts/planner_prompt.py` anywhere in this module. This is
      what makes the Verifier genuinely independent, not merely a
      differently-worded self-check.
    - Makes no extra retry of its own on top of the one call above -- per
      this task's requirement, retries happen only inside the adapter, per
      its own contract (see `llm/base.py`); there is no Verifier-level
      guardrail-retry loop analogous to the Planner's, because there is no
      equivalent deterministic business rule for the Verifier itself to
      correct and resubmit.
    - Still validates the response itself rather than trusting the
      boundary blindly: confirms `response.parsed` really is a
      `VerifierDecision`, then runs the extracted-field contradiction gate
      (`_apply_extracted_contradiction_gate`): structured
      `extracted_field_verdicts` plus deterministic closed-enum support
      (`extracted_support.py`) ensure only CONTRADICTED extracted labels
      may enter `fabricated_fields` — SUPPORTED / INSUFFICIENT_EVIDENCE
      never quarantine a run. A secondary reason-text self-contradiction
      stripper remains for free-text fields. It then cross-checks the
      decision's `plan_deviation_detected` claim against
      `_detect_plan_deviation()` -- a deterministic
      recomputation from `plan` and `tool_call_trace` that the model cannot
      get wrong by hallucination, since it involves no natural-language
      judgment. If the deterministic check finds a deviation the model
      missed, the decision is corrected (forced to `passed=False`,
      `plan_deviation_detected=True`) before it is returned -- the Verifier
      never simply relays the model's own unchecked claim for the one
      dimension that can be checked in code.
    - Likewise reconciles fabrication claims with deterministic evidence:
      extracted fields stay an LLM judgment against the enquiry; tool-derived
      fields (`jurisdiction_rule` / `handling_note` / `requires_disclaimer`,
      `score` / `score_breakdown`) must match successful tool outputs;
      `dedupe_hash` must match recomputation (or a successful `write_record`
      result when present). Matching derived/computed fields clear any LLM
      fabrication claims on those fields; mismatches force
      `fabrication_detected=True`. If no fabricated fields remain and there
      is no plan deviation, the decision is updated to pass.
    - Logs prompt, response, latency, and token usage for the call.
    """
    model = get_settings().resolved_model("verifier")
    user_prompt = build_verifier_user_prompt(
        enquiry_text,
        plan.model_dump(mode="json"),
        tool_call_trace.model_dump(mode="json"),
        final_record,
    )
    request = StructuredCompletionRequest(
        system_prompt=VERIFIER_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        response_schema=VerifierDecision,
        model=model,
        max_tokens=768,
        metadata={"run_id": run_id_ctx.get(), "stage": "verifier"},
    )

    stage_token = stage_ctx.set("verifier")
    try:
        response = adapter.complete_structured(request)

        if not isinstance(response.parsed, VerifierDecision):
            # Defensive: the adapter contract guarantees `parsed` already
            # validates against `response_schema` -- but a boundary is
            # never trusted blindly ("the Verifier must never trust the
            # Planner or Executor outputs" applies equally to its own
            # adapter dependency).
            raise VerifierValidationError(
                f"adapter returned {type(response.parsed).__name__}, expected VerifierDecision"
            )
        decision = response.parsed

        decision, gate_warnings = _apply_extracted_contradiction_gate(
            decision,
            enquiry_text=enquiry_text,
            final_record=final_record,
        )
        if gate_warnings:
            logger.warning(
                "verifier extracted-field contradiction gate adjusted fabrication claims",
                extra={
                    "event": "verifier_extracted_contradiction_gate",
                    "gate_warnings": gate_warnings,
                    "fabricated_fields": decision.fabricated_fields,
                },
            )

        decision, consistency_warnings = _strip_self_contradictory_claims(decision, final_record)
        if consistency_warnings:
            logger.warning(
                "verifier consistency validator discarded self-contradictory fabrication "
                "claim(s) before reconciliation",
                extra={
                    "event": "verifier_consistency_override",
                    "consistency_warnings": consistency_warnings,
                },
            )

        llm_claimed_deviation = decision.plan_deviation_detected
        deviation = _detect_plan_deviation(plan, tool_call_trace)
        if deviation is not None:
            if not llm_claimed_deviation:
                logger.warning(
                    "verifier deterministic plan-deviation check caught a deviation the "
                    "model's own judgment missed",
                    extra={
                        "event": "verifier_plan_deviation_override",
                        "deterministic_deviation": deviation,
                    },
                )
            decision = decision.model_copy(
                update={
                    "passed": False,
                    "plan_deviation_detected": True,
                    "deviation_details": deviation,
                    "reason": decision.reason or deviation,
                }
            )

        tool_mismatches = _detect_tool_derived_mismatches(tool_call_trace, final_record)
        parse_mismatches = _detect_extracted_vs_parse_mismatches(tool_call_trace, final_record)
        dedupe_mismatch = _dedupe_hash_mismatches(final_record, tool_call_trace)
        if tool_mismatches or parse_mismatches or dedupe_mismatch:
            logger.warning(
                "verifier deterministic derived/computed/assembly-field check found mismatches",
                extra={
                    "event": "verifier_derived_fabrication_override",
                    "tool_mismatches": tool_mismatches,
                    "parse_mismatches": parse_mismatches,
                    "dedupe_mismatch": dedupe_mismatch,
                },
            )
        # Always reconcile: may add deterministic mismatches OR clear LLM claims
        # on tool-derived / dedupe_hash fields that match successful tool outputs /
        # recomputation. Extracted-field LLM claims are never cleared; parse-vs-
        # final assembly mismatches are added.
        decision = _reconcile_fabrication_claims(
            decision,
            tool_mismatches=tool_mismatches,
            parse_mismatches=parse_mismatches,
            dedupe_mismatch=dedupe_mismatch,
        )

        _log_call(
            model=response.model,
            user_prompt=user_prompt,
            decision=decision,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            latency_ms=response.latency_ms,
        )
        return decision
    finally:
        stage_ctx.reset(stage_token)
