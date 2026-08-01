"""Data-access functions. All reads/writes to the `runs`, `llm_calls`,
`leads`, and `harness_batches` tables should go through here — not through
ad hoc queries in api/ or agent/ — so persistence logic has one place to be
tested and to evolve (e.g. if SQLite is later swapped for Postgres, per
docs/architecture.md section 5).

See docs/contracts.md section 6 for the full signature/purpose/failure-mode
contract each function below must satisfy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import HarnessBatch, Lead, LlmCall, Run
from app.schemas.run import RunResult

ModelT = TypeVar("ModelT", HarnessBatch, Lead, Run)


def _add_commit_refresh(db: Session, row: ModelT) -> ModelT:
    """Add + commit + refresh, rolling back on failure so the session stays
    usable for subsequent calls (e.g. a caller catching a duplicate
    `dedupe_hash` IntegrityError and continuing on the same session)."""
    db.add(row)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(row)
    return row


def create_run(db: Session, run: RunResult, *, harness_batch_id: str | None = None) -> Run:
    """Map a `RunResult` -> a `Run` ORM row (+ its `LlmCall` children),
    insert, commit, return the persisted row.

    Mapping notes (see docs/contracts.md section 8 for the target columns):
    - Nested Pydantic objects (`plan`, `tool_call_trace`, `final_record`) are
      stored via `.model_dump(mode="json")` into their respective JSON
      columns, verbatim.
    - `VerifierDecision` fields are flattened onto individual `Run` columns
      per docs/architecture.md section 5's ERD (not stored as a nested JSON
      blob), including `fabricated_fields`/`deviation_details`.
    - `Run.model_provider`/`model_name` (top-level columns) have no direct
      equivalent on `RunResult` today -- `RunResult` only carries
      per-stage provider/model inside `llm_calls`. As a pragmatic mapping
      (not a schema change), they're taken from the *first* `llm_calls`
      entry (conventionally the planner call), or left `None` if there were
      no LLM calls at all (e.g. an early executor failure).
    - `harness_batch_id` isn't a `RunResult` field either (a single pipeline
      run doesn't know which batch it belongs to) -- accepted as an optional
      keyword so the harness can link a run to its batch at persistence
      time without requiring a separate update call.
    - `LlmCallUsage` (on `RunResult`) doesn't carry `raw_request`/
      `raw_response` -- those fields exist on the `LlmCall` ORM row for
      future use once the adapter layer's raw payloads are threaded through
      to `RunResult`, and are left `None` here.
    """
    verifier = run.verifier_decision

    row = Run(
        id=run.id,
        enquiry_text=run.enquiry_text,
        enquiry_id=run.enquiry_id,
        repeat_index=run.repeat_index,
        is_adversarial=run.is_adversarial,
        plan=run.plan.model_dump(mode="json") if run.plan is not None else None,
        plan_schema_valid=run.plan_schema_valid,
        tool_call_trace=(
            run.tool_call_trace.model_dump(mode="json") if run.tool_call_trace is not None else None
        ),
        final_record=(run.final_record.model_dump(mode="json") if run.final_record is not None else None),
        verifier_pass=verifier.passed if verifier is not None else None,
        verifier_confidence=verifier.confidence if verifier is not None else None,
        verifier_reason=verifier.reason if verifier is not None else None,
        fabrication_detected=verifier.fabrication_detected if verifier is not None else False,
        fabricated_fields=verifier.fabricated_fields if verifier is not None else None,
        plan_deviation_detected=verifier.plan_deviation_detected if verifier is not None else False,
        deviation_details=verifier.deviation_details if verifier is not None else None,
        repair_attempted=run.repair_attempted,
        repair_succeeded=run.repair_succeeded,
        final_status=run.final_status,
        error_type=run.error_type,
        error_message=run.error_message,
        traceback=run.traceback,
        total_tokens=run.total_tokens,
        total_cost_usd=run.total_cost_usd,
        total_latency_ms=run.total_latency_ms,
        model_provider=run.llm_calls[0].model_provider if run.llm_calls else None,
        model_name=run.llm_calls[0].model_name if run.llm_calls else None,
        harness_batch_id=harness_batch_id,
        created_at=run.created_at,
    )
    row.llm_calls = [
        LlmCall(
            run_id=run.id,
            stage=usage.stage,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cost_usd=usage.cost_usd,
            latency_ms=usage.latency_ms,
            model_provider=usage.model_provider,
            model_name=usage.model_name,
        )
        for usage in run.llm_calls
    ]

    return _add_commit_refresh(db, row)


def get_run(db: Session, run_id: str) -> Run | None:
    """Fetch a single run by id, or `None` if it doesn't exist."""
    return db.get(Run, run_id)


def list_runs(db: Session, *, limit: int = 50, offset: int = 0) -> list[Run]:
    """Paginated list, most recent first."""
    stmt = select(Run).order_by(Run.created_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt).all())


def find_lead_by_dedupe_hash(db: Session, dedupe_hash: str) -> Lead | None:
    """Used by `tools/write_record.py`'s duplicate check *before* attempting
    an insert -- see `services/dedupe.py` for how `dedupe_hash` is computed
    ("duplicate if both [normalized email and phone] match")."""
    stmt = select(Lead).where(Lead.dedupe_hash == dedupe_hash)
    return db.scalars(stmt).first()


def create_lead(db: Session, **fields: Any) -> Lead:
    """Insert a new lead row.

    Raises the DB's `IntegrityError` unchanged if `dedupe_hash` collides
    with an existing row (docs/contracts.md section 8) -- callers must
    check `find_lead_by_dedupe_hash` first; this function does not
    pre-check or swallow the error, only rolls the session back so it
    remains usable afterward.
    """
    lead = Lead(**fields)
    return _add_commit_refresh(db, lead)


def list_leads(db: Session, *, status: str | None = None) -> list[Lead]:
    """List leads, most recent first, optionally filtered by
    `status` ("accepted" | "quarantined")."""
    stmt = select(Lead).order_by(Lead.created_at.desc())
    if status is not None:
        stmt = stmt.where(Lead.status == status)
    return list(db.scalars(stmt).all())


def create_harness_batch(db: Session, **fields: Any) -> HarnessBatch:
    """Insert a new harness_batches row."""
    batch = HarnessBatch(**fields)
    return _add_commit_refresh(db, batch)


def get_latest_harness_batch(db: Session) -> HarnessBatch | None:
    """Most recent harness_batches row (by `started_at`), used by
    GET /api/harness/summary. `None` if no batch has ever been run."""
    stmt = select(HarnessBatch).order_by(HarnessBatch.started_at.desc()).limit(1)
    return db.scalars(stmt).first()


def get_harness_batch(db: Session, harness_batch_id: str) -> HarnessBatch | None:
    """Fetch a single harness batch by id, or `None` if it doesn't exist.

    Additive (docs/contracts.md section 6 predates this): needed by
    evaluation/harness.py to resume a specific, previously-started batch
    (rather than only ever reading "whatever is latest"), so an interrupted
    45-run harness can be resumed by id rather than only by convention.
    """
    return db.get(HarnessBatch, harness_batch_id)


def find_run_by_batch_position(
    db: Session, harness_batch_id: str, enquiry_id: str, repeat_index: int
) -> Run | None:
    """Has this `(enquiry_id, repeat_index)` pair already been executed
    within this specific harness batch? Backs evaluation/harness.py's
    resumability check (docs/architecture.md section 11: "skips
    (enquiry_id, repeat_index) pairs already completed").

    Additive (docs/contracts.md section 6 predates this): scoped by
    `harness_batch_id` as well as `(enquiry_id, repeat_index)` -- the same
    enquiry/repeat pair can legitimately also appear in a different batch,
    or as a one-off live API run with no batch at all, neither of which
    should count as "already done" for *this* batch's resumability.
    Backed by the existing `ix_runs_enquiry_id_repeat_index` index
    (db/models.py) plus an equality filter on `harness_batch_id`.
    """
    stmt = select(Run).where(
        Run.harness_batch_id == harness_batch_id,
        Run.enquiry_id == enquiry_id,
        Run.repeat_index == repeat_index,
    )
    return db.scalars(stmt).first()


def list_runs_for_batch(db: Session, harness_batch_id: str) -> list[Run]:
    """All runs belonging to one harness batch, oldest first -- the raw
    material evaluation/metrics.py aggregates into the batch's summary
    metrics. Never mutates state.

    Additive (docs/contracts.md section 6 predates this): the harness needs
    to read back exactly the rows it just wrote, scoped to its own batch,
    which none of the existing query functions (`list_runs`, unscoped and
    paginated for the UI's history view) provide.
    """
    stmt = select(Run).where(Run.harness_batch_id == harness_batch_id).order_by(Run.created_at.asc())
    return list(db.scalars(stmt).all())


def finish_harness_batch(db: Session, harness_batch_id: str, *, metrics: dict) -> HarnessBatch:
    """Stamp a harness batch as complete: sets `finished_at` (now, UTC) and
    `metrics` (the computed summary from evaluation/metrics.py), commits,
    returns the refreshed row.

    Additive (docs/contracts.md section 6 predates this): `create_harness_batch`
    alone can only ever insert a fresh row at the *start* of a batch, before
    any metrics exist; this is the corresponding "close out" call once the
    45 runs are done, kept in repository.py rather than evaluation/harness.py
    mutating the ORM row directly, per this module's own "all reads/writes ...
    go through here" rule.

    Raises `ValueError` if `harness_batch_id` doesn't exist -- callers must
    have created the batch first via `create_harness_batch`.
    """
    batch = db.get(HarnessBatch, harness_batch_id)
    if batch is None:
        raise ValueError(f"No harness_batches row with id {harness_batch_id!r}.")
    batch.finished_at = datetime.now(UTC)
    batch.metrics = metrics
    return _add_commit_refresh(db, batch)
