"""The Planner. See docs/architecture.md section 6.

Rule (docs/development-rules.md): the Planner never executes tools. This
module's only job is to produce a validated `Plan`; it must not import from
`agent/executor.py` or `tools/`.
"""

from __future__ import annotations

from app.llm.base import ModelAdapter
from app.schemas.plan import Plan


def build_plan(enquiry_text: str, tool_manifest: list[dict], adapter: ModelAdapter) -> Plan:
    """Call the Planner LLM and return a validated `Plan`.

    TODO(agent/planner):
    - Build prompts via prompts/planner_prompt.py.
    - Call `adapter.complete_structured(...)` with `response_schema=Plan`.
    - On schema validation failure, retry at most once (see llm/base.py); the
      *first-attempt* outcome must still be reported for the harness's
      planner schema-breach-rate metric regardless of retry success.
    - Return the validated `Plan` alongside enough call metadata (tokens,
      latency, cost) for the caller (agent/orchestrator.py) to log an
      `LlmCallUsage` entry with stage="planner".
    """
    raise NotImplementedError
