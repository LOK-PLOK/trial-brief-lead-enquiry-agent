"""System/user prompt templates for the independent Verifier LLM call.

Must remain fully self-contained: no text shared with planner_prompt.py or
parse_enquiry_prompt.py, no shared message history, no self-check framing.
This separation is "the substance of the exercise" per the brief.
"""

from __future__ import annotations

# TODO(agent/verifier): write the real system prompt. Must instruct the model
# to independently re-derive judgments from the *original enquiry text*, and
# explicitly check for the two failure classes: (1) fabrication — any final
# record field not present in or reasonably inferable from the source text;
# (2) plan deviation — any skip/reorder/substitution between the submitted
# plan and the actual tool call trace.
VERIFIER_SYSTEM_PROMPT = """TODO: verifier system prompt."""


def build_verifier_user_prompt(
    enquiry_text: str, plan: dict, tool_call_trace: dict, final_record: dict
) -> str:
    """TODO(agent/verifier): implement."""
    raise NotImplementedError
