"""The Pipeline: ties Planner -> Executor -> Verifier -> Repair -> persistence
together. See docs/architecture.md section 1 and the sequence diagrams in
section 14.

Critical property: both the live API (server/app/api/routes_runs.py) and the
standalone evaluation harness (evaluation/harness.py) must call this same
`Pipeline.run()` — no duplicate/parallel code path — so harness numbers are
reproducible against real system behaviour (docs/architecture.md section 11).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.llm.base import ModelAdapter
from app.schemas.run import RunResult


@dataclass
class Pipeline:
    adapter: ModelAdapter
    settings: Settings

    def run(
        self,
        enquiry_text: str,
        *,
        enquiry_id: str | None = None,
        repeat_index: int | None = None,
        is_adversarial: bool = False,
    ) -> RunResult:
        """Execute one full pipeline pass and return a `RunResult`.

        TODO(agent/orchestrator): implement the sequence:
        1. `agent.planner.build_plan(...)` -> Plan (log llm_calls stage="planner").
        2. `agent.executor.run_plan(...)` -> ExecutionResult
           (or catch ToolExecutionError and route straight to repair).
        3. `agent.verifier.verify(...)` -> VerifierDecision
           (log llm_calls stage="verifier").
        4. If verifier fails or the executor reported a tool failure:
           `agent.repair.attempt_repair(...)` -> RepairResult (exactly one
           attempt; log llm_calls for whichever stages it re-runs).
        5. Assemble and return a `RunResult` with `final_status` set to
           "completed" (verifier passed, first try or after repair),
           "quarantined" (repair attempted and still failed — never silently
           dropped), or "error" (unrecoverable executor error).
        6. Persist the run via db/repository.py — the API route and the
           harness should not duplicate persistence logic; do it here.
        """
        raise NotImplementedError
