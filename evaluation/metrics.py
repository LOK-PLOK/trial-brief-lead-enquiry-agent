"""Computes the harness metrics required by the brief (docs/architecture.md
section 11 / docs/Trial_Brief_Paul_Detablan.md section 3.6):

- end-to-end task completion rate
- schema breach rate at the planner and at the extractor
- fabrication rate, and the proportion the verifier caught
- tool selection accuracy against the plan
- repair loop success rate
- mean tokens and mean cost per run
- mean latency per run
- run-to-run variance on identical input

Computed directly from persisted `runs`/`llm_calls` rows (via
server/app/db/repository.py) — not recomputed ad hoc elsewhere — so the
numbers shown in the harness summary API and any offline report always
agree.
"""

from __future__ import annotations

from sqlalchemy.orm import Session


def compute_harness_metrics(db: Session, harness_batch_id: str) -> dict:
    """TODO(evaluation/metrics): implement, pulling all `runs` (+ their
    `llm_calls`) for `harness_batch_id` and computing:

    - completion_rate: fraction with final_status == "completed"
    - planner_schema_breach_rate / extractor_schema_breach_rate: fraction of
      *first-attempt* structured-output validation failures (count these
      regardless of whether an internal retry recovered them — see
      docs/architecture.md section 6, "honesty over optics")
    - fabrication_rate + fabrication_caught_rate: see docs/architecture.md
      section 11's explicit honesty flag — this needs either seeded
      unanswerable-field test cases or manual review to have a ground truth
      independent of the verifier's own opinion
    - tool_selection_accuracy: fraction of runs whose tool_call_trace order
      matches the submitted plan
    - repair_success_rate: repair_succeeded count / repair_attempted count
    - mean_tokens / mean_cost_usd / mean_latency_ms: use
      services/cost.aggregate_usage() semantics, not a separate calculation
    - variance: group runs by enquiry_id, compute e.g. stddev of score,
      latency, cost across the 3 repeats, plus a field-level diff count
      between repeats' final_record
    """
    raise NotImplementedError
