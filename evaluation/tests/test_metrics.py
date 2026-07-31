"""Unit tests for evaluation/metrics.py. Most tests construct bare `Run` ORM
objects directly (no session needed for plain attribute access) to exercise
`_compute_metrics_from_runs` precisely, without needing a full pipeline run
per scenario. `compute_harness_metrics` itself (the DB-reading public
function) gets its own, smaller test against a real session.
"""

from __future__ import annotations

import pytest
from app.db import repository
from app.db.models import Run

from evaluation.metrics import (
    _compute_metrics_from_runs,
    _percentile,
    _stdev,
    _tool_sequence_matches_plan,
    _variance_by_enquiry,
    compute_harness_metrics,
)


def _run(**overrides) -> Run:
    defaults: dict = dict(
        id=overrides.get("id", "run-1"),
        enquiry_text="Hi, interested in whisky casks.",
        final_status="completed",
        total_tokens=100,
        total_cost_usd=0.01,
        total_latency_ms=1000.0,
        verifier_pass=True,
        fabrication_detected=False,
        repair_attempted=False,
        repair_succeeded=None,
        plan=None,
        tool_call_trace=None,
        final_record=None,
        enquiry_id=None,
    )
    defaults.update(overrides)
    return Run(**defaults)


_PLAN_FOUR_STEPS = {
    "steps": [
        {"step": 1, "tool": "parse_enquiry", "args": {}, "rationale": "x"},
        {"step": 2, "tool": "lookup_jurisdiction_rule", "args": {}, "rationale": "x"},
        {"step": 3, "tool": "score_lead", "args": {}, "rationale": "x"},
        {"step": 4, "tool": "write_record", "args": {}, "rationale": "x"},
    ]
}


def _trace(tools: list[str]) -> dict:
    return {"calls": [{"step": i + 1, "tool": t, "args": {}} for i, t in enumerate(tools)]}


class TestPercentile:
    def test_empty_list_is_zero(self) -> None:
        assert _percentile([], 95) == 0.0

    def test_single_value_returns_that_value(self) -> None:
        assert _percentile([42.0], 95) == 42.0

    def test_p50_of_evenly_spaced_values_is_the_median(self) -> None:
        assert _percentile([10.0, 20.0, 30.0], 50) == 20.0

    def test_p95_of_a_larger_sample_interpolates(self) -> None:
        data = [float(i) for i in range(1, 11)]  # 1..10
        # rank = (10-1) * 0.95 = 8.55 -> interpolate between index 8 (9.0) and 9 (10.0)
        assert _percentile(data, 95) == pytest.approx(9.55)


class TestStdev:
    def test_empty_and_singleton_are_zero(self) -> None:
        assert _stdev([]) == 0.0
        assert _stdev([5.0]) == 0.0

    def test_identical_values_have_zero_variance(self) -> None:
        assert _stdev([3.0, 3.0, 3.0]) == 0.0

    def test_differing_values_have_positive_variance(self) -> None:
        assert _stdev([1.0, 2.0, 3.0]) > 0.0


class TestToolSequenceMatchesPlan:
    def test_none_when_no_plan_or_no_trace(self) -> None:
        assert _tool_sequence_matches_plan(_run(plan=None, tool_call_trace=_trace(["parse_enquiry"]))) is None
        assert _tool_sequence_matches_plan(_run(plan=_PLAN_FOUR_STEPS, tool_call_trace=None)) is None

    def test_true_for_an_exact_match(self) -> None:
        trace = _trace(["parse_enquiry", "lookup_jurisdiction_rule", "score_lead", "write_record"])
        assert _tool_sequence_matches_plan(_run(plan=_PLAN_FOUR_STEPS, tool_call_trace=trace)) is True

    def test_true_for_a_shorter_trace_that_stopped_early_without_deviating(self) -> None:
        """A trace shorter than the plan because execution stopped after a
        failure is NOT a deviation (docs/contracts.md section 2) -- only a
        mismatch/reorder/substitution/extra step is."""
        trace = _trace(["parse_enquiry", "lookup_jurisdiction_rule"])
        assert _tool_sequence_matches_plan(_run(plan=_PLAN_FOUR_STEPS, tool_call_trace=trace)) is True

    def test_false_for_a_substituted_step(self) -> None:
        trace = _trace(["parse_enquiry", "score_lead", "score_lead", "write_record"])
        assert _tool_sequence_matches_plan(_run(plan=_PLAN_FOUR_STEPS, tool_call_trace=trace)) is False

    def test_false_for_a_longer_trace_than_the_plan(self) -> None:
        trace = _trace(
            ["parse_enquiry", "lookup_jurisdiction_rule", "score_lead", "write_record", "score_lead"]
        )
        assert _tool_sequence_matches_plan(_run(plan=_PLAN_FOUR_STEPS, tool_call_trace=trace)) is False


class TestVarianceByEnquiry:
    def test_groups_by_enquiry_id_and_computes_stdev(self) -> None:
        runs = [
            _run(id="r1", enquiry_id="enq-1", total_latency_ms=100.0, total_cost_usd=0.01),
            _run(id="r2", enquiry_id="enq-1", total_latency_ms=200.0, total_cost_usd=0.02),
            _run(id="r3", enquiry_id="enq-2", total_latency_ms=300.0, total_cost_usd=0.03),
        ]

        variance = _variance_by_enquiry(runs)

        assert set(variance["by_enquiry"].keys()) == {"enq-1", "enq-2"}
        assert variance["by_enquiry"]["enq-1"]["n_repeats"] == 2
        assert variance["by_enquiry"]["enq-1"]["latency_ms_stdev"] > 0.0
        assert variance["by_enquiry"]["enq-2"]["n_repeats"] == 1
        assert variance["by_enquiry"]["enq-2"]["latency_ms_stdev"] == 0.0

    def test_runs_without_an_enquiry_id_are_excluded_from_grouping(self) -> None:
        runs = [_run(id="r1", enquiry_id=None), _run(id="r2", enquiry_id="enq-1")]
        variance = _variance_by_enquiry(runs)
        assert list(variance["by_enquiry"].keys()) == ["enq-1"]

    def test_distinct_final_record_count_reflects_differing_records(self) -> None:
        runs = [
            _run(id="r1", enquiry_id="enq-1", final_record={"score": 80}),
            _run(id="r2", enquiry_id="enq-1", final_record={"score": 90}),
            _run(id="r3", enquiry_id="enq-1", final_record={"score": 80}),
        ]
        variance = _variance_by_enquiry(runs)
        assert variance["by_enquiry"]["enq-1"]["distinct_final_record_count"] == 2


class TestComputeMetricsFromRuns:
    def test_empty_run_list_returns_zeroed_metrics_with_honesty_notes(self) -> None:
        metrics = _compute_metrics_from_runs([])
        assert metrics["n_runs"] == 0
        assert metrics["completion_rate"] is None
        assert metrics["mean_latency_ms"] == 0.0
        assert metrics["notes"]

    def test_completion_quarantined_and_error_rates(self) -> None:
        runs = [
            _run(id="r1", final_status="completed"),
            _run(id="r2", final_status="completed"),
            _run(id="r3", final_status="quarantined"),
            _run(id="r4", final_status="error"),
        ]
        metrics = _compute_metrics_from_runs(runs)
        assert metrics["n_runs"] == 4
        assert metrics["completion_rate"] == 0.5
        assert metrics["quarantined_rate"] == 0.25
        assert metrics["error_rate"] == 0.25

    def test_pass_rate_and_fabrication_rate_only_count_runs_with_a_decision(self) -> None:
        runs = [
            _run(id="r1", verifier_pass=True, fabrication_detected=False),
            _run(id="r2", verifier_pass=False, fabrication_detected=True),
            # No verifier decision reached at all (e.g. executor failure) --
            # must not count in either denominator.
            _run(id="r3", verifier_pass=None, fabrication_detected=False, final_status="error"),
        ]
        metrics = _compute_metrics_from_runs(runs)
        assert metrics["pass_rate"] == 0.5
        assert metrics["fabrication_rate"] == 0.5

    def test_pass_rate_is_none_when_no_run_ever_reached_a_verifier_decision(self) -> None:
        runs = [_run(id="r1", verifier_pass=None, final_status="error")]
        metrics = _compute_metrics_from_runs(runs)
        assert metrics["pass_rate"] is None
        assert metrics["fabrication_rate"] is None

    def test_known_unmeasurable_metrics_are_honestly_none(self) -> None:
        metrics = _compute_metrics_from_runs([_run()])
        assert metrics["fabrication_caught_rate"] is None
        assert metrics["planner_schema_breach_rate"] is None
        assert metrics["extractor_schema_breach_rate"] is None

    def test_repair_success_rate_is_none_when_repair_never_attempted(self) -> None:
        metrics = _compute_metrics_from_runs([_run(repair_attempted=False)])
        assert metrics["repair_attempted_rate"] == 0.0
        assert metrics["repair_success_rate"] is None

    def test_repair_success_rate_computed_when_attempted(self) -> None:
        runs = [
            _run(id="r1", repair_attempted=True, repair_succeeded=True),
            _run(id="r2", repair_attempted=True, repair_succeeded=False),
            _run(id="r3", repair_attempted=False),
        ]
        metrics = _compute_metrics_from_runs(runs)
        assert metrics["repair_attempted_rate"] == 2 / 3
        assert metrics["repair_success_rate"] == 0.5

    def test_tool_selection_accuracy_ignores_runs_with_no_plan_or_trace(self) -> None:
        trace = _trace(["parse_enquiry", "lookup_jurisdiction_rule", "score_lead", "write_record"])
        runs = [
            _run(id="r1", plan=_PLAN_FOUR_STEPS, tool_call_trace=trace),
            _run(id="r2", plan=None, tool_call_trace=None, final_status="error"),
        ]
        metrics = _compute_metrics_from_runs(runs)
        assert metrics["tool_selection_accuracy"] == 1.0

    def test_latency_and_cost_aggregates(self) -> None:
        runs = [
            _run(id="r1", total_latency_ms=100.0, total_tokens=50, total_cost_usd=0.001),
            _run(id="r2", total_latency_ms=300.0, total_tokens=150, total_cost_usd=0.003),
        ]
        metrics = _compute_metrics_from_runs(runs)
        assert metrics["mean_latency_ms"] == 200.0
        assert metrics["median_latency_ms"] == 200.0
        assert metrics["mean_tokens"] == 100.0
        assert metrics["mean_cost_usd"] == 0.002


class TestComputeHarnessMetrics:
    def test_reads_runs_scoped_to_the_given_batch_only(self, db_session) -> None:
        from app.schemas.run import RunResult

        batch_a = repository.create_harness_batch(db_session, n_runs=1)
        batch_b = repository.create_harness_batch(db_session, n_runs=1)
        repository.create_run(
            db_session,
            RunResult(id="run-a", enquiry_text="a", final_status="completed"),
            harness_batch_id=batch_a.id,
        )
        repository.create_run(
            db_session,
            RunResult(id="run-b", enquiry_text="b", final_status="error"),
            harness_batch_id=batch_b.id,
        )

        metrics = compute_harness_metrics(db_session, batch_a.id)

        assert metrics["n_runs"] == 1
        assert metrics["completion_rate"] == 1.0
