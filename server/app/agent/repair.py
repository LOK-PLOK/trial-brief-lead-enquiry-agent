"""The one-shot Repair Loop. See docs/architecture.md section 10.

Triggered only when the Verifier fails a run, or the Executor reports a tool
failure. Exactly one repair attempt, then quarantine — a submission must
never be silently dropped (docs/development-rules.md).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.llm.base import ModelAdapter
from app.schemas.plan import Plan
from app.schemas.tool_trace import ToolCallTrace
from app.schemas.verifier import VerifierDecision


@dataclass
class RepairResult:
    succeeded: bool
    new_plan: Plan | None
    new_trace: ToolCallTrace | None
    new_record: dict | None
    new_verifier_decision: VerifierDecision
    strategy_used: str  # "reextract" | "reexecute" | "replan"


def attempt_repair(
    enquiry_text: str,
    plan: Plan,
    tool_call_trace: ToolCallTrace,
    final_record: dict | None,
    verifier_decision: VerifierDecision,
    adapter: ModelAdapter,
) -> RepairResult:
    """Run exactly one repair pass, then re-verify.

    TODO(agent/repair): implement strategy selection per
    docs/architecture.md section 10:
    - plan_deviation_detected -> re-run the Executor strictly against the
      original Plan, or ask the Planner to re-plan if the deviation stemmed
      from a genuine tool error.
    - fabrication_detected -> re-run `parse_enquiry` with the flagged fields
      and an explicit "leave unknown fields null" instruction
      (see prompts/repair_prompt.py).
    - tool execution error (no verifier_decision yet) -> retry from the
      failed step, or request a corrected plan.
    Must call agent/verifier.py's `verify()` again afterward — a fresh,
    independent check, never a self-check — to determine `succeeded`.
    """
    raise NotImplementedError
