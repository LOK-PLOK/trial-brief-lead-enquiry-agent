"""The Executor. See docs/architecture.md section 7.

Rule (docs/development-rules.md): the Executor is deterministic. This module
must contain no LLM calls of its own — it only dispatches to
`tools/registry.py`, one of which (`parse_enquiry`) happens to be LLM-backed
internally, but that call is opaque to the executor's control flow.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.lead_record import LeadRecord
from app.schemas.plan import Plan
from app.schemas.tool_trace import ToolCallTrace


class ToolExecutionError(Exception):
    """Raised when a tool reports failure; the executor must report this
    upward rather than continuing blindly (docs/architecture.md section 7)."""


@dataclass
class ExecutionResult:
    trace: ToolCallTrace
    final_record: LeadRecord | None
    error: str | None = None


def run_plan(plan: Plan, enquiry_text: str) -> ExecutionResult:
    """Execute `plan.steps` in order via real function calls (never by
    parsing model prose — see docs/architecture.md section 7).

    TODO(agent/executor):
    - For each PlanStep: look up the tool in tools/registry.py by
      `step.tool` (enum-constrained dispatch, not string parsing of prose).
    - Validate `step.args` against that tool's own args_schema (separate
      from the Plan schema check) before invoking it.
    - Call the tool, record a ToolCall (status/result/error/latency_ms) into
      the trace regardless of outcome.
    - On tool failure: stop, raise ToolExecutionError (or return an
      ExecutionResult with `error` set) rather than continuing with the
      remaining steps.
    - On success of all steps: assemble the final LeadRecord from the
      individual tool outputs.
    """
    raise NotImplementedError
