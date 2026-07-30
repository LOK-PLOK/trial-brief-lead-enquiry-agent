"""System/user prompt templates for the `parse_enquiry` tool's LLM call.

Distinct from both the planner and verifier prompts (docs/architecture.md
section 8) — this is the extraction step's own, isolated call.
"""

from __future__ import annotations

# TODO(tools/parse_enquiry): write the real system prompt. Must instruct the
# model to leave a field null/unknown rather than guess when the source text
# doesn't support a value (fabrication is exactly what the verifier checks
# for downstream).
PARSE_ENQUIRY_SYSTEM_PROMPT = """TODO: parse_enquiry system prompt."""


def build_parse_enquiry_user_prompt(enquiry_text: str) -> str:
    """TODO(tools/parse_enquiry): implement."""
    raise NotImplementedError
