"""Unit tests for evaluation/harness.py's own orchestration logic (fixture
loading/validation, resumability, batch bookkeeping, report writing) using a
fake `Pipeline` -- never a real LLM adapter or real tools. See
test_harness_integration.py for the full-stack version using the real
`Pipeline`/`ToolRegistry` with mock tools.
"""

from __future__ import annotations

import json

import pytest
from app.db import repository
from app.schemas.run import RunResult

from evaluation import harness as harness_module


class _FakePipeline:
    """Duck-typed stand-in for `app.agent.orchestrator.Pipeline`: records
    every call it receives and persists a minimal, deterministic
    `RunResult` via the real repository, so `run_harness()`'s resumability
    check and `compute_harness_metrics()` (which both read straight from
    the DB) see exactly what a real `Pipeline.run()` would have written."""

    def __init__(self, *, final_status: str = "completed") -> None:
        self.calls: list[tuple[str, int]] = []
        self._final_status = final_status

    def run(self, enquiry_text, *, enquiry_id, repeat_index, db, harness_batch_id, **_ignored):
        self.calls.append((enquiry_id, repeat_index))
        result = RunResult(
            id=f"run-{enquiry_id}-{repeat_index}",
            enquiry_text=enquiry_text,
            enquiry_id=enquiry_id,
            repeat_index=repeat_index,
            final_status=self._final_status,
            total_latency_ms=42.0,
            total_tokens=10,
            total_cost_usd=0.001,
        )
        repository.create_run(db, result, harness_batch_id=harness_batch_id)
        return result


def _enquiries(n: int) -> list[dict]:
    return [{"id": f"enq-{i}", "text": f"Enquiry number {i}."} for i in range(1, n + 1)]


@pytest.fixture(autouse=True)
def _reports_dir(tmp_path, monkeypatch) -> None:
    """Never write to the real evaluation/reports/ during tests."""
    monkeypatch.setattr(harness_module, "REPORTS_DIR", tmp_path / "reports")


class TestEnquiryValidation:
    def test_raises_when_no_enquiries_supplied(self, db_session) -> None:
        with pytest.raises(ValueError, match="No enquiries"):
            harness_module.run_harness(
                enquiries=[], pipeline=_FakePipeline(), db=db_session, expected_n_enquiries=None
            )

    def test_raises_when_enquiry_count_does_not_match_expected(self, db_session) -> None:
        with pytest.raises(ValueError, match="Expected exactly 15"):
            harness_module.run_harness(enquiries=_enquiries(3), pipeline=_FakePipeline(), db=db_session)

    def test_expected_n_enquiries_none_disables_the_check(self, db_session) -> None:
        summary = harness_module.run_harness(
            enquiries=_enquiries(2),
            n_repeats=1,
            expected_n_enquiries=None,
            pipeline=_FakePipeline(),
            db=db_session,
        )
        assert summary.n_runs_expected == 2


class TestRunCounts:
    def test_executes_exactly_n_enquiries_times_n_repeats_runs(self, db_session) -> None:
        fake = _FakePipeline()
        summary = harness_module.run_harness(
            enquiries=_enquiries(3), n_repeats=3, expected_n_enquiries=None, pipeline=fake, db=db_session
        )

        assert summary.n_runs_expected == 9
        assert summary.n_runs_executed == 9
        assert summary.n_runs_skipped == 0
        assert len(fake.calls) == 9
        assert len(set(fake.calls)) == 9  # every (enquiry_id, repeat_index) pair is unique

    def test_every_repeat_uses_the_same_enquiry_text_and_a_distinct_repeat_index(self, db_session) -> None:
        fake = _FakePipeline()
        harness_module.run_harness(
            enquiries=_enquiries(1), n_repeats=3, expected_n_enquiries=None, pipeline=fake, db=db_session
        )
        assert fake.calls == [("enq-1", 1), ("enq-1", 2), ("enq-1", 3)]


class TestResumability:
    def test_resuming_the_same_batch_skips_already_completed_pairs(self, db_session) -> None:
        enquiries = _enquiries(2)
        fake1 = _FakePipeline()
        first = harness_module.run_harness(
            enquiries=enquiries, n_repeats=2, expected_n_enquiries=None, pipeline=fake1, db=db_session
        )
        assert first.n_runs_executed == 4

        fake2 = _FakePipeline()
        second = harness_module.run_harness(
            enquiries=enquiries,
            n_repeats=2,
            expected_n_enquiries=None,
            pipeline=fake2,
            db=db_session,
            harness_batch_id=first.harness_batch_id,
        )

        assert second.harness_batch_id == first.harness_batch_id
        assert second.n_runs_executed == 0
        assert second.n_runs_skipped == 4
        assert fake2.calls == []

    def test_resuming_a_partially_completed_batch_only_runs_the_missing_pairs(self, db_session) -> None:
        enquiries = _enquiries(2)
        batch = repository.create_harness_batch(db_session, n_runs=4)
        # Manually complete exactly one of the four (enquiry_id, repeat_index)
        # pairs before the harness ever runs, simulating an interruption.
        repository.create_run(
            db_session,
            RunResult(id="pre-existing", enquiry_text="x", enquiry_id="enq-1", repeat_index=1),
            harness_batch_id=batch.id,
        )

        fake = _FakePipeline()
        summary = harness_module.run_harness(
            enquiries=enquiries,
            n_repeats=2,
            expected_n_enquiries=None,
            pipeline=fake,
            db=db_session,
            harness_batch_id=batch.id,
        )

        assert summary.n_runs_executed == 3
        assert summary.n_runs_skipped == 1
        assert ("enq-1", 1) not in fake.calls

    def test_resuming_an_unknown_batch_id_raises(self, db_session) -> None:
        with pytest.raises(ValueError, match="No harness_batches row"):
            harness_module.run_harness(
                enquiries=_enquiries(1),
                n_repeats=1,
                expected_n_enquiries=None,
                pipeline=_FakePipeline(),
                db=db_session,
                harness_batch_id="does-not-exist",
            )


class TestBatchPersistence:
    def test_batch_is_stamped_finished_with_metrics_on_completion(self, db_session) -> None:
        summary = harness_module.run_harness(
            enquiries=_enquiries(2),
            n_repeats=1,
            expected_n_enquiries=None,
            pipeline=_FakePipeline(),
            db=db_session,
        )

        batch = repository.get_harness_batch(db_session, summary.harness_batch_id)
        assert batch is not None
        assert batch.finished_at is not None
        assert batch.metrics == summary.metrics
        assert batch.metrics["n_runs"] == 2

    def test_config_snapshot_excludes_credentials(self, db_session) -> None:
        summary = harness_module.run_harness(
            enquiries=_enquiries(1),
            n_repeats=1,
            expected_n_enquiries=None,
            pipeline=_FakePipeline(),
            db=db_session,
        )
        batch = repository.get_harness_batch(db_session, summary.harness_batch_id)
        assert "openai_api_key" not in batch.config_snapshot
        assert "anthropic_api_key" not in batch.config_snapshot
        assert "model_provider" in batch.config_snapshot
        assert batch.config_snapshot["dataset_id"] == "standard"
        assert batch.config_snapshot["dataset_label"] == "Official Trial A Dataset (E01–E15)"


class TestHarnessExceptionCapture:
    def test_pipeline_raise_is_persisted_with_traceback(self, db_session) -> None:
        class _BoomPipeline:
            def run(self, *args, **kwargs):
                raise RuntimeError("openrouter 402 Payment Required for chat/completions")

        summary = harness_module.run_harness(
            enquiries=_enquiries(1),
            n_repeats=1,
            expected_n_enquiries=None,
            pipeline=_BoomPipeline(),  # type: ignore[arg-type]
            db=db_session,
        )
        assert summary.n_runs_executed == 1
        runs = repository.list_runs_for_batch(db_session, summary.harness_batch_id)
        assert len(runs) == 1
        assert runs[0].final_status == "error"
        assert runs[0].error_type == "RuntimeError"
        assert "402 Payment Required" in (runs[0].error_message or "")
        assert runs[0].traceback and "Traceback" in runs[0].traceback


class TestProgressAndCancel:
    def test_on_progress_is_invoked_for_each_run(self, db_session) -> None:
        events: list[tuple[int, int, str | None, str]] = []
        harness_module.run_harness(
            enquiries=_enquiries(2),
            n_repeats=1,
            expected_n_enquiries=None,
            pipeline=_FakePipeline(),
            db=db_session,
            on_progress=lambda c, t, e, p: events.append((c, t, e, p)),
        )
        phases = [e[3] for e in events]
        assert "starting" in phases
        assert "finished" in phases
        assert any(e[3] == "running" for e in events)

    def test_cooperative_cancel_stops_before_remaining_runs(self, db_session) -> None:
        fake = _FakePipeline()
        calls = {"n": 0}

        def should_cancel() -> bool:
            # Cancel after the first completed run (second should_cancel check
            # before the second enquiry).
            return calls["n"] >= 1

        original_run = fake.run

        def counting_run(*args, **kwargs):
            result = original_run(*args, **kwargs)
            calls["n"] += 1
            return result

        fake.run = counting_run  # type: ignore[method-assign]
        summary = harness_module.run_harness(
            enquiries=_enquiries(3),
            n_repeats=1,
            expected_n_enquiries=None,
            pipeline=fake,
            db=db_session,
            should_cancel=should_cancel,
        )
        assert summary.n_runs_executed == 1
        assert len(fake.calls) == 1


class TestReportWriting:
    def test_writes_a_json_report_with_the_full_metrics(self, db_session) -> None:
        summary = harness_module.run_harness(
            enquiries=_enquiries(2),
            n_repeats=1,
            expected_n_enquiries=None,
            pipeline=_FakePipeline(),
            db=db_session,
        )

        assert summary.json_report_path.exists()
        payload = json.loads(summary.json_report_path.read_text())
        assert payload["harness_batch_id"] == summary.harness_batch_id
        assert payload["n_runs_executed"] == 2
        assert payload["metrics"]["n_runs"] == 2

    def test_writes_a_human_readable_markdown_report(self, db_session) -> None:
        summary = harness_module.run_harness(
            enquiries=_enquiries(2),
            n_repeats=1,
            expected_n_enquiries=None,
            pipeline=_FakePipeline(),
            db=db_session,
        )

        assert summary.markdown_report_path.exists()
        text = summary.markdown_report_path.read_text()
        assert "# Evaluation Harness Report" in text
        assert summary.harness_batch_id in text
        assert "Completion rate" in text
        assert "Honesty notes" in text
