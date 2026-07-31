"""Drives the full evaluation harness: 15 enquiries x 3 repeats = 45 runs.
See docs/architecture.md section 11 and docs/Trial_Brief_Paul_Detablan.md
section 3.6.

Run from the repo root with the `server` package on PYTHONPATH:

    PYTHONPATH=server python -m evaluation.harness

Must call the exact same `Pipeline.run()` used by the live API
(server/app/agent/orchestrator.py) — no separate/duplicate pipeline code —
so these numbers are reproducible by the assessor against real system
behaviour (docs/architecture.md section 11, hard gate 3 in the brief).

**Clean-state guarantee.** Each of the 45 runs is fully independent of every
other one. This is not something this module has to engineer on top of
`Pipeline.run()` -- it already holds by construction:

1. A fresh `run_id` (`uuid4()`) is generated *inside* `Pipeline.run()` for
   every call -- never reused or threaded through from a previous run.
2. No LLM conversation history crosses runs, or even stages within a run
   (`docs/development-rules.md`: "Each LLM call is isolated") -- every
   Planner/Verifier call is a brand-new `StructuredCompletionRequest` with
   no `messages`/history field at all.
3. `Pipeline.run()` constructs a brand-new `_UsageTrackingAdapter` (empty
   `.calls` list) and a brand-new `ToolRegistry` *inside* every call --
   token/cost accounting and tool instances from run N cannot leak into
   run N+1, because nothing is held across calls to begin with.
4. `run_id_ctx`/`stage_ctx` (server/app/core/logging.py) are `.set()` at
   the start and unconditionally `.reset()` in a `finally` block for every
   single `Pipeline.run()` call, so log correlation never bleeds from one
   run into the next, even when a run fails.
5. Persistence is append-only: `repository.create_run()` always `INSERT`s a
   brand-new `runs` row keyed by that run's own fresh UUID -- one run's
   outcome can never overwrite or be confused with another's.

What this module *does* add on top, at the harness (not per-run) level, is
resumability across separate *invocations* of `run_harness()` (see below) --
a distinct concern from clean state within a single run.

**Duplicate detection is deliberately not this module's concern.** The
brief's `write_record` tool rejects duplicates on a normalized email/phone
hash (docs/architecture.md section 8); if the 45 runs ever legitimately
produce two leads with the same contact details, `write_record` handling
that correctly is exactly what
`server/app/tests/integration/test_pipeline_duplicate_detection.py` verifies
-- as its own, independent test, run via pytest, never via this module.
`run_harness()` does not special-case, suppress, or work around duplicate
outcomes in any way: whatever `write_record` genuinely does is faithfully
recorded and reported like any other run, per this project's honesty policy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agent.orchestrator import Pipeline
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db import repository
from app.db.session import init_db, session_scope
from app.llm.factory import get_adapter
from sqlalchemy.orm import Session

from evaluation.metrics import compute_harness_metrics

logger = get_logger(__name__)

FIXTURES_PATH = Path(__file__).resolve().parent / "fixtures" / "enquiries.json"
REPORTS_DIR = Path(__file__).resolve().parent / "reports"
N_REPEATS = 3
# The brief's exact shape: 15 supplied enquiries x 3 repeats = 45 runs
# (docs/Trial_Brief_Paul_Detablan.md section 3.6). Enforced by default in
# `run_harness()` (see `expected_n_enquiries`), so a mismatched fixture file
# fails loudly rather than silently reporting numbers for the wrong sample
# size.
N_ENQUIRIES = 15


def load_enquiries() -> list[dict]:
    data = json.loads(FIXTURES_PATH.read_text())
    return data["enquiries"]


@dataclass
class HarnessRunSummary:
    """What `run_harness()` returns: enough to both assert against in tests
    and print a one-line human summary, without needing to re-open the
    written report files."""

    harness_batch_id: str
    n_enquiries: int
    n_repeats: int
    n_runs_expected: int
    n_runs_executed: int
    n_runs_skipped: int
    metrics: dict[str, Any]
    json_report_path: Path
    markdown_report_path: Path


def _config_snapshot(settings: Settings) -> dict[str, Any]:
    """A redacted snapshot of whatever configuration affects harness
    numbers, captured at batch start for reproducibility
    (docs/architecture.md section 5, `harness_batches.config_snapshot`).
    Deliberately excludes API keys/credentials -- this gets persisted to
    the database and to a JSON report file committed to the repo."""
    return {
        "model_provider": settings.model_provider.value,
        "model_name": settings.model_name,
        "planner_model": settings.planner_model,
        "extractor_model": settings.extractor_model,
        "verifier_model": settings.verifier_model,
    }


def run_harness(
    *,
    n_repeats: int = N_REPEATS,
    expected_n_enquiries: int | None = N_ENQUIRIES,
    enquiries: list[dict] | None = None,
    pipeline: Pipeline | None = None,
    db: Session | None = None,
    harness_batch_id: str | None = None,
) -> HarnessRunSummary:
    """Run exactly `len(enquiries) * n_repeats` independent runs (45 by
    default) through `Pipeline.run()`, compute the full metrics set, persist
    them onto the `harness_batches` row, and write a JSON + Markdown report.

    Idempotent/resumable (docs/architecture.md section 11): pass an
    existing `harness_batch_id` to resume it -- any `(enquiry_id,
    repeat_index)` pair already recorded against that batch is skipped
    rather than re-run, so an interrupted harness (rate limits, crash) can
    continue rather than restart from run 1. Omit it to start a fresh batch.

    `enquiries`/`pipeline`/`db` are optional, injectable dependencies
    (defaulting to the real fixtures file / a real configured `Pipeline` /
    an internally-managed `session_scope()`), so this function is fully
    testable with fakes -- see evaluation/tests/.

    `expected_n_enquiries` defaults to 15 (the brief's real fixture count)
    and raises `ValueError` if `enquiries` doesn't match it, so a
    mismatched or still-empty fixtures file fails loudly rather than
    silently reporting numbers for the wrong sample size. Pass `None` to
    disable this check (used by tests that exercise the harness's own
    logic against a small, synthetic enquiry list).
    """
    settings = get_settings()
    enquiries = enquiries if enquiries is not None else load_enquiries()
    if not enquiries:
        raise ValueError(
            "No enquiries to run: evaluation/fixtures/enquiries.json is empty. The 15 real "
            "enquiry samples must be supplied before the harness can run (see "
            "docs/Trial_Brief_Paul_Detablan.md section 3.1)."
        )
    if expected_n_enquiries is not None and len(enquiries) != expected_n_enquiries:
        raise ValueError(
            f"Expected exactly {expected_n_enquiries} enquiries, found {len(enquiries)} in "
            f"{FIXTURES_PATH} -- the brief requires {expected_n_enquiries} enquiries x "
            f"{n_repeats} repeats for a reproducible 45-run count."
        )

    init_db()
    pipeline = pipeline or Pipeline(adapter=get_adapter(), settings=settings)

    if db is not None:
        return _run_harness_with_session(
            db,
            enquiries=enquiries,
            n_repeats=n_repeats,
            pipeline=pipeline,
            settings=settings,
            harness_batch_id=harness_batch_id,
        )
    with session_scope() as scoped_db:
        return _run_harness_with_session(
            scoped_db,
            enquiries=enquiries,
            n_repeats=n_repeats,
            pipeline=pipeline,
            settings=settings,
            harness_batch_id=harness_batch_id,
        )


def _run_harness_with_session(
    db: Session,
    *,
    enquiries: list[dict],
    n_repeats: int,
    pipeline: Pipeline,
    settings: Settings,
    harness_batch_id: str | None,
) -> HarnessRunSummary:
    n_runs_expected = len(enquiries) * n_repeats

    if harness_batch_id is not None:
        batch = repository.get_harness_batch(db, harness_batch_id)
        if batch is None:
            raise ValueError(f"No harness_batches row with id {harness_batch_id!r} to resume.")
    else:
        batch = repository.create_harness_batch(
            db, n_runs=n_runs_expected, config_snapshot=_config_snapshot(settings)
        )

    n_executed = 0
    n_skipped = 0
    for enquiry in enquiries:
        enquiry_id = enquiry["id"]
        enquiry_text = enquiry["text"]
        for repeat_index in range(1, n_repeats + 1):
            existing = repository.find_run_by_batch_position(db, batch.id, enquiry_id, repeat_index)
            if existing is not None:
                n_skipped += 1
                logger.info(
                    "skipping already-completed run",
                    extra={
                        "event": "harness_run_skipped",
                        "harness_batch_id": batch.id,
                        "enquiry_id": enquiry_id,
                        "repeat_index": repeat_index,
                        "existing_run_id": existing.id,
                    },
                )
                continue

            logger.info(
                "starting harness run",
                extra={
                    "event": "harness_run_started",
                    "harness_batch_id": batch.id,
                    "enquiry_id": enquiry_id,
                    "repeat_index": repeat_index,
                },
            )
            pipeline.run(
                enquiry_text,
                enquiry_id=enquiry_id,
                repeat_index=repeat_index,
                db=db,
                harness_batch_id=batch.id,
            )
            n_executed += 1

    metrics = compute_harness_metrics(db, batch.id)
    repository.finish_harness_batch(db, batch.id, metrics=metrics)

    json_report_path = _write_json_report(
        batch_id=batch.id,
        n_enquiries=len(enquiries),
        n_repeats=n_repeats,
        n_runs_expected=n_runs_expected,
        n_runs_executed=n_executed,
        n_runs_skipped=n_skipped,
        config_snapshot=_config_snapshot(settings),
        metrics=metrics,
    )
    markdown_report_path = _write_markdown_report(
        batch_id=batch.id,
        n_enquiries=len(enquiries),
        n_repeats=n_repeats,
        n_runs_expected=n_runs_expected,
        n_runs_executed=n_executed,
        n_runs_skipped=n_skipped,
        metrics=metrics,
    )

    return HarnessRunSummary(
        harness_batch_id=batch.id,
        n_enquiries=len(enquiries),
        n_repeats=n_repeats,
        n_runs_expected=n_runs_expected,
        n_runs_executed=n_executed,
        n_runs_skipped=n_skipped,
        metrics=metrics,
        json_report_path=json_report_path,
        markdown_report_path=markdown_report_path,
    )


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _write_json_report(
    *,
    batch_id: str,
    n_enquiries: int,
    n_repeats: int,
    n_runs_expected: int,
    n_runs_executed: int,
    n_runs_skipped: int,
    config_snapshot: dict[str, Any],
    metrics: dict[str, Any],
) -> Path:
    """Structured JSON artifact, independent of the database (belt-and-braces
    per docs/architecture.md section 15's free-tier disk persistence risk):
    even if the SQLite file is lost, this file (committed to the repo) is
    still durable evidence of what the harness measured."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "harness_batch_id": batch_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "n_enquiries": n_enquiries,
        "n_repeats": n_repeats,
        "n_runs_expected": n_runs_expected,
        "n_runs_executed": n_runs_executed,
        "n_runs_skipped": n_runs_skipped,
        "config_snapshot": config_snapshot,
        "metrics": metrics,
    }
    report_path = REPORTS_DIR / f"{_timestamp()}.json"
    report_path.write_text(json.dumps(report, indent=2, default=str))
    return report_path


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _write_markdown_report(
    *,
    batch_id: str,
    n_enquiries: int,
    n_repeats: int,
    n_runs_expected: int,
    n_runs_executed: int,
    n_runs_skipped: int,
    metrics: dict[str, Any],
) -> Path:
    """Human-readable counterpart to the JSON report -- what a reviewer
    actually reads, per docs/Trial_Brief_Paul_Detablan.md's proof-note
    expectations ("what you measured")."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    m = metrics
    variance = m.get("variance", {})
    by_enquiry = variance.get("by_enquiry", {})

    lines = [
        "# Evaluation Harness Report",
        "",
        f"**Harness batch:** `{batch_id}`  ",
        f"**Generated:** {datetime.now(UTC).isoformat()}  ",
        f"**Runs:** {n_runs_executed} executed, {n_runs_skipped} skipped (resumed), "
        f"{n_runs_expected} expected ({n_enquiries} enquiries x {n_repeats} repeats)",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Completion rate | {_fmt(m.get('completion_rate'))} |",
        f"| Quarantined rate | {_fmt(m.get('quarantined_rate'))} |",
        f"| Error rate | {_fmt(m.get('error_rate'))} |",
        f"| Verifier pass rate | {_fmt(m.get('pass_rate'))} |",
        f"| Fabrication rate (flagged by verifier) | {_fmt(m.get('fabrication_rate'))} |",
        f"| Fabrication caught rate | {_fmt(m.get('fabrication_caught_rate'))} |",
        f"| Planner schema breach rate | {_fmt(m.get('planner_schema_breach_rate'))} |",
        f"| Extractor schema breach rate | {_fmt(m.get('extractor_schema_breach_rate'))} |",
        f"| Tool selection accuracy | {_fmt(m.get('tool_selection_accuracy'))} |",
        f"| Repair attempted rate | {_fmt(m.get('repair_attempted_rate'))} |",
        f"| Repair success rate | {_fmt(m.get('repair_success_rate'))} |",
        "",
        "## Latency / Tokens / Cost",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Mean latency (ms) | {_fmt(m.get('mean_latency_ms'))} |",
        f"| Median latency (ms) | {_fmt(m.get('median_latency_ms'))} |",
        f"| p95 latency (ms) | {_fmt(m.get('p95_latency_ms'))} |",
        f"| Mean tokens | {_fmt(m.get('mean_tokens'))} |",
        f"| Mean cost (USD) | {_fmt(m.get('mean_cost_usd'), 6)} |",
        "",
        "## Run-to-run variance (grouped by enquiry)",
        "",
        f"Overall latency stdev (ms): {_fmt(variance.get('overall_latency_ms_stdev'))}  ",
        f"Overall cost stdev (USD): {_fmt(variance.get('overall_cost_usd_stdev'), 6)}",
        "",
    ]

    if by_enquiry:
        lines += [
            "| Enquiry | Repeats | Final statuses | Latency stdev (ms) | Cost stdev (USD) | "
            "Distinct final_record shapes |",
            "|---|---|---|---|---|---|",
        ]
        for enquiry_id, v in by_enquiry.items():
            lines.append(
                f"| {enquiry_id} | {v['n_repeats']} | {', '.join(v['final_statuses'])} | "
                f"{_fmt(v['latency_ms_stdev'])} | {_fmt(v['cost_usd_stdev'], 6)} | "
                f"{v['distinct_final_record_count']} |"
            )
        lines.append("")

    lines += ["## Honesty notes", ""]
    for note in m.get("notes", []):
        lines.append(f"- {note}")
    lines.append("")

    report_path = REPORTS_DIR / f"{_timestamp()}.md"
    report_path.write_text("\n".join(lines))
    return report_path


if __name__ == "__main__":
    configure_logging(get_settings().log_level)
    summary = run_harness()
    print(  # noqa: T201 -- CLI entry point, this is the intended user-facing output
        f"Harness batch {summary.harness_batch_id}: "
        f"{summary.n_runs_executed} executed, {summary.n_runs_skipped} skipped "
        f"({summary.n_runs_expected} expected). "
        f"Reports written to {summary.json_report_path} and {summary.markdown_report_path}."
    )
