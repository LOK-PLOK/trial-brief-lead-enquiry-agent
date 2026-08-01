"""Prompt augmentation helpers for the one-shot repair pass.

See docs/architecture.md section 10. These wrap the *existing* planner /
parse_enquiry prompts with the verifier's failure reason appended — they do
not introduce a new independent prompt, since repair is a corrective retry
of planning/extraction, not a verification step.
"""

from __future__ import annotations


def augment_for_plan_deviation(base_prompt: str, deviation_details: str) -> str:
    """Ask the planner to produce a corrected plan given what went wrong."""
    return (
        f"{base_prompt}\n\n"
        "CORRECTION REQUIRED — the previous plan was rejected because the "
        "executor's tool-call trace did not match it:\n"
        f"{deviation_details}\n\n"
        "Return a corrected Plan that uses only tools from the manifest, "
        "includes exactly one lookup_jurisdiction_rule and exactly one "
        "score_lead step, and is executable without skipping or substituting "
        "tools."
    )


def augment_for_fabrication(base_prompt: str, fabricated_fields: list[str]) -> str:
    """Ask parse_enquiry to re-extract with flagged fields called out."""
    fields = ", ".join(fabricated_fields) if fabricated_fields else "(unspecified fields)"
    return (
        f"{base_prompt}\n\n"
        "CORRECTION REQUIRED — an independent verifier rejected the previous "
        "extraction for fabrication. The following field(s) were flagged as "
        f"not present in or reasonably inferable from the enquiry text: {fields}.\n"
        "Re-extract carefully. For every flagged field, leave the value null "
        '(or "unknown" for budget_band/urgency) unless the enquiry text '
        "unambiguously supports it. Do not guess. Do not invent contact "
        "details, budget, or urgency."
    )
