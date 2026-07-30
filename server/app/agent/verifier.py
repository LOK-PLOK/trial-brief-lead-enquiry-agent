"""The independent Verifier. See docs/architecture.md section 9.

Rule (docs/development-rules.md): the Verifier is completely independent.
This module must NOT import anything from agent/planner.py's or
tools/parse_enquiry.py's prompt modules, and must issue its own, freshly
constructed, stateless LLM call — never a self-check appended to a prior
call's context.
"""

from __future__ import annotations

from app.llm.base import ModelAdapter
from app.schemas.plan import Plan
from app.schemas.tool_trace import ToolCallTrace
from app.schemas.verifier import VerifierDecision


def verify(
    enquiry_text: str,
    plan: Plan,
    tool_call_trace: ToolCallTrace,
    final_record: dict,
    adapter: ModelAdapter,
) -> VerifierDecision:
    """Call the Verifier LLM and return a validated `VerifierDecision`.

    TODO(agent/verifier):
    - Build prompts via prompts/verifier_prompt.py ONLY (no shared prompt
      text with planner/parse_enquiry).
    - Call `adapter.complete_structured(...)` with
      `response_schema=VerifierDecision`, using `settings.resolved_model(
      "verifier")` so a distinct verifier model can be configured
      independently of the planner/extractor model.
    - Must genuinely check both failure classes from the brief: fabrication
      (any final_record field not present/inferable in enquiry_text) and
      plan deviation (any skip/reorder/substitution between `plan` and
      `tool_call_trace`).
    """
    raise NotImplementedError
