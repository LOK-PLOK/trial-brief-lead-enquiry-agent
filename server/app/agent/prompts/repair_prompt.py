"""Prompt augmentation helpers for the one-shot repair pass.

See docs/architecture.md section 10. These wrap the *existing* planner /
parse_enquiry prompts with the verifier's failure reason appended — they do
not introduce a new independent prompt, since repair is a corrective retry
of planning/extraction, not a verification step.
"""

from __future__ import annotations


def augment_for_plan_deviation(base_prompt: str, deviation_details: str) -> str:
    """TODO(agent/repair): implement. Ask the planner to produce a corrected
    plan given what went wrong last time."""
    raise NotImplementedError


def augment_for_fabrication(base_prompt: str, fabricated_fields: list[str]) -> str:
    """TODO(agent/repair): implement. Ask parse_enquiry to re-extract with the
    flagged fields explicitly called out, defaulting to null rather than
    guessing."""
    raise NotImplementedError
