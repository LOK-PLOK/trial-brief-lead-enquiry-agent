"""Plan helpers shared by the Orchestrator and Repair Loop.

`write_record` is a post-verifier persistence step (docs/architecture.md
section 10), not part of the Executor plan the Verifier judges. These
helpers keep Planner/Repair outputs free of that side-effectful tool.
"""

from __future__ import annotations

from app.schemas.plan import Plan, PlanStep, ToolName


def strip_write_record(plan: Plan) -> Plan:
    """Return a copy of `plan` with every `write_record` step removed.

    Steps are re-numbered 1..N so the result remains a valid `Plan`. Used
    before Executor / Repair so persistence cannot run until the Verifier
    has passed (orchestrator commits via `write_record` separately).
    """
    kept: list[PlanStep] = [
        PlanStep(
            step=index,
            tool=step.tool,
            args=dict(step.args),
            rationale=step.rationale,
        )
        for index, step in enumerate(
            (s for s in plan.steps if s.tool is not ToolName.WRITE_RECORD),
            start=1,
        )
    ]
    return Plan(steps=kept)
