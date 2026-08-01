"""Computes the harness metrics required by the brief (docs/architecture.md
section 11 / docs/Trial_Brief_Paul_Detablan.md section 3.6):

- end-to-end task completion rate
- schema breach rate at the planner and at the extractor
- fabrication rate, and the proportion the verifier caught
- tool selection accuracy against the plan
- repair loop success rate
- mean tokens and mean cost per run
- mean latency per run (mean, median, p95)
- run-to-run variance on identical input

Computed directly from persisted `runs` rows (via server/app/db/repository.py)
-- not recomputed ad hoc elsewhere -- so the numbers shown in any harness
summary API and any offline report always agree.

**Honesty notes** (see the `"notes"` key on the returned dict, and
docs/architecture.md sections 11/15): two of the brief's requested metrics
are reported as `None` rather than guessed at, because nothing in the
current, committed contracts (docs/contracts.md) makes them measurable yet:

- `fabrication_caught_rate` needs a *ground truth* fabrication rate
  independent of the Verifier's own opinion (e.g. seeded unanswerable
  fields) -- this harness has no such ground truth today.
- `planner_schema_breach_rate` / `extractor_schema_breach_rate` need to know
  whether an LLM call's *first* attempt failed schema validation before an
  internal retry recovered it. `ModelAdapter.complete_structured()`'s own
  contract (docs/contracts.md section 5) says this retry happens internally
  and opaquely; the interface has no field surfacing it. Making this
  genuinely measurable would mean extending a documented, immutable contract
  (`StructuredCompletionResponse`).

Reporting `None` with a stated reason is preferred over a fabricated number,
per this project's established honesty policy (docs/architecture.md
section 15's fabrication-rate caveat is the precedent).
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any

from app.db import repository
from app.db.models import Run
from sqlalchemy.orm import Session

_HONESTY_NOTES: tuple[str, ...] = (
    "fabrication_caught_rate is None: it requires a ground-truth fabrication "
    "label independent of the Verifier's own opinion (e.g. seeded unanswerable "
    "fields), which this harness does not have -- see docs/architecture.md "
    "section 11.",
    "planner_schema_breach_rate / extractor_schema_breach_rate are None: "
    "ModelAdapter.complete_structured() retries a schema-invalid first "
    "attempt internally and opaquely per its own contract (docs/contracts.md "
    "section 5); no field on StructuredCompletionResponse currently surfaces "
    "whether that happened. Extending that contract would be a deliberate "
    "contracts.md change -- named here rather than guessed at, per "
    "docs/architecture.md section 15's honesty policy.",
    "repair_success_rate is None whenever repair_attempted_rate is 0.0 "
    "(no verifier failures triggered the Repair Loop in this batch).",
)


def compute_harness_metrics(db: Session, harness_batch_id: str) -> dict[str, Any]:
    """Pull every `runs` row for `harness_batch_id` and compute the full
    metrics dict. Read-only; never mutates state."""
    runs = repository.list_runs_for_batch(db, harness_batch_id)
    return _compute_metrics_from_runs(runs)


def _percentile(data: list[float], pct: float) -> float:
    """Linear-interpolation percentile (the same method numpy's default
    `interpolation="linear"` uses), so `p95` lines up with the number
    anyone re-checking the report by hand/spreadsheet would expect.
    Deterministic, dependency-free. `0.0` for an empty list."""
    if not data:
        return 0.0
    ordered = sorted(data)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[int(rank)]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def _stdev(data: list[float]) -> float:
    """Population standard deviation -- these 3 (or 15x3) repeats are the
    *entire* population being measured for this run, not a sample drawn
    from a larger one, so `pstdev` (not the sample `stdev`) is the correct
    statistic. `0.0` for 0 or 1 data points (no variance to speak of)."""
    return statistics.pstdev(data) if len(data) > 1 else 0.0


def _tool_sequence_matches_plan(run: Run) -> bool | None:
    """Did the executed tool sequence match the plan's tool sequence,
    position for position, for however much of the plan actually ran
    (docs/contracts.md section 2: a trace shorter than the plan because
    execution stopped after a failure is not itself a deviation -- only a
    mismatched/reordered/substituted/extra step is)?

    `None` (excluded from the accuracy denominator) when there's no plan or
    no trace to compare at all (e.g. the Planner itself failed).
    """
    if run.plan is None or run.tool_call_trace is None:
        return None
    planned = [step["tool"] for step in run.plan.get("steps", [])]
    executed = [call["tool"] for call in run.tool_call_trace.get("calls", [])]
    if len(executed) > len(planned):
        return False
    return all(p == e for p, e in zip(planned, executed, strict=False))


def _variance_by_enquiry(runs: list[Run]) -> dict[str, Any]:
    """Groups `runs` by `enquiry_id` and computes run-to-run variance across
    the repeats of each one -- directly answering the brief's "run to run
    variance on identical input" (section 3.6). Runs with no `enquiry_id`
    (ad hoc/live-API runs, not part of a repeated-enquiry harness sweep) are
    excluded from the grouping entirely.
    """
    by_enquiry: dict[str, list[Run]] = defaultdict(list)
    for run in runs:
        if run.enquiry_id is not None:
            by_enquiry[run.enquiry_id].append(run)

    per_enquiry: dict[str, Any] = {}
    for enquiry_id, group in sorted(by_enquiry.items()):
        latencies = [r.total_latency_ms for r in group]
        costs = [r.total_cost_usd for r in group]
        tokens = [float(r.total_tokens) for r in group]
        scores = [r.final_record["score"] for r in group if r.final_record and "score" in r.final_record]
        # A cheap, order-independent structural fingerprint of each repeat's
        # final_record -- distinct records show up as > 1 distinct string,
        # without needing a field-by-field diff implementation.
        distinct_records = {_stable_repr(r.final_record) for r in group}

        per_enquiry[enquiry_id] = {
            "n_repeats": len(group),
            "final_statuses": sorted({r.final_status for r in group}),
            "latency_ms_stdev": _stdev(latencies),
            "cost_usd_stdev": _stdev(costs),
            "tokens_stdev": _stdev(tokens),
            "score_stdev": _stdev(scores) if len(scores) > 1 else None,
            "distinct_final_record_count": len(distinct_records),
        }

    return {
        "by_enquiry": per_enquiry,
        "overall_latency_ms_stdev": _stdev([r.total_latency_ms for r in runs]),
        "overall_cost_usd_stdev": _stdev([r.total_cost_usd for r in runs]),
        "overall_tokens_stdev": _stdev([float(r.total_tokens) for r in runs]),
    }


def _stable_repr(value: Any) -> str:
    """Order-independent-enough string form of a JSON-ish value, used only
    to count *distinct* `final_record` shapes across repeats -- not for
    display or persistence."""
    import json

    return json.dumps(value, sort_keys=True, default=str)


def _rate(numerator: int, denominator: int) -> float | None:
    """`numerator / denominator`, or `None` (not `0.0` or a `ZeroDivisionError`)
    when `denominator` is 0 -- "no runs reached this stage" is a different,
    honestly-distinguishable fact from "0% of them passed"."""
    return (numerator / denominator) if denominator else None


def _compute_metrics_from_runs(runs: list[Run]) -> dict[str, Any]:
    n = len(runs)
    if n == 0:
        return {
            "n_runs": 0,
            "completion_rate": None,
            "quarantined_rate": None,
            "error_rate": None,
            "pass_rate": None,
            "fabrication_rate": None,
            "fabrication_caught_rate": None,
            "planner_schema_breach_rate": None,
            "extractor_schema_breach_rate": None,
            "tool_selection_accuracy": None,
            "repair_attempted_rate": None,
            "repair_success_rate": None,
            "mean_latency_ms": 0.0,
            "median_latency_ms": 0.0,
            "p95_latency_ms": 0.0,
            "mean_tokens": 0.0,
            "mean_cost_usd": 0.0,
            "variance": {"by_enquiry": {}, "overall_latency_ms_stdev": 0.0, "overall_cost_usd_stdev": 0.0},
            "notes": list(_HONESTY_NOTES) + ["n_runs is 0: nothing to aggregate."],
        }

    completed = [r for r in runs if r.final_status == "completed"]
    quarantined = [r for r in runs if r.final_status == "quarantined"]
    errored = [r for r in runs if r.final_status == "error"]

    verified = [r for r in runs if r.verifier_pass is not None]
    passed = [r for r in verified if r.verifier_pass]
    fabrication_flagged = [r for r in verified if r.fabrication_detected]

    tool_selection_results = [_tool_sequence_matches_plan(r) for r in runs]
    tool_selection_comparable = [m for m in tool_selection_results if m is not None]

    repair_attempted = [r for r in runs if r.repair_attempted]
    repair_succeeded = [r for r in repair_attempted if r.repair_succeeded]

    latencies = [r.total_latency_ms for r in runs]
    tokens = [float(r.total_tokens) for r in runs]
    costs = [r.total_cost_usd for r in runs]

    return {
        "n_runs": n,
        "completion_rate": len(completed) / n,
        "quarantined_rate": len(quarantined) / n,
        "error_rate": len(errored) / n,
        "pass_rate": _rate(len(passed), len(verified)),
        "fabrication_rate": _rate(len(fabrication_flagged), len(verified)),
        "fabrication_caught_rate": None,
        "planner_schema_breach_rate": None,
        "extractor_schema_breach_rate": None,
        "tool_selection_accuracy": _rate(sum(tool_selection_comparable), len(tool_selection_comparable)),
        "repair_attempted_rate": len(repair_attempted) / n,
        "repair_success_rate": _rate(len(repair_succeeded), len(repair_attempted)),
        "mean_latency_ms": statistics.mean(latencies),
        "median_latency_ms": statistics.median(latencies),
        "p95_latency_ms": _percentile(latencies, 95),
        "mean_tokens": statistics.mean(tokens),
        "mean_cost_usd": statistics.mean(costs),
        "variance": _variance_by_enquiry(runs),
        "notes": list(_HONESTY_NOTES),
    }
