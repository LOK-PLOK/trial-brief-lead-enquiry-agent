"""System/user prompt templates for the Planner LLM call.

Must remain fully self-contained: no text shared with
verifier_prompt.py, so the verifier is genuinely independent
(docs/architecture.md section 9).
"""

from __future__ import annotations

# TODO(agent/planner): write the real system prompt. Must instruct the model
# that it is planning only (never executing), must return a Plan matching the
# schema, and must treat the enquiry text as untrusted data, not instructions
# (see docs/architecture.md section 15, prompt-injection risk).
PLANNER_SYSTEM_PROMPT = """TODO: planner system prompt."""


def build_planner_user_prompt(enquiry_text: str, tool_manifest: list[dict]) -> str:
    """Compose the user-turn prompt from the enquiry text and tool manifest.

    TODO(agent/planner): implement. `tool_manifest` should come directly from
    tools/registry.py so the planner's view of available tools can never
    drift from what the executor can actually run.
    """
    raise NotImplementedError
