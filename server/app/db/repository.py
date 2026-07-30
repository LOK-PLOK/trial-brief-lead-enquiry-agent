"""Data-access functions. All reads/writes to the `runs`, `llm_calls`,
`leads`, and `harness_batches` tables should go through here — not through
ad hoc queries in api/ or agent/ — so persistence logic has one place to be
tested and to evolve (e.g. if SQLite is later swapped for Postgres, per
docs/architecture.md section 5).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import HarnessBatch, Lead, Run
from app.schemas.run import RunResult


def create_run(db: Session, run: RunResult) -> Run:
    """TODO(db/repository): map `RunResult` -> `Run` ORM row (+ its
    `LlmCall` children), insert, commit, return the persisted row."""
    raise NotImplementedError


def get_run(db: Session, run_id: str) -> Run | None:
    """TODO(db/repository): fetch by id."""
    raise NotImplementedError


def list_runs(db: Session, *, limit: int = 50, offset: int = 0) -> list[Run]:
    """TODO(db/repository): paginated list, most recent first."""
    raise NotImplementedError


def find_lead_by_dedupe_hash(db: Session, dedupe_hash: str) -> Lead | None:
    """TODO(db/repository): used by tools/write_record.py's duplicate check."""
    raise NotImplementedError


def create_lead(db: Session, **fields) -> Lead:
    """TODO(db/repository): insert a new lead row."""
    raise NotImplementedError


def list_leads(db: Session, *, status: str | None = None) -> list[Lead]:
    """TODO(db/repository): optionally filter by status (accepted|quarantined)."""
    raise NotImplementedError


def create_harness_batch(db: Session, **fields) -> HarnessBatch:
    """TODO(db/repository): insert a new harness_batches row."""
    raise NotImplementedError


def get_latest_harness_batch(db: Session) -> HarnessBatch | None:
    """TODO(db/repository): most recent harness_batches row, used by
    GET /api/harness/summary."""
    raise NotImplementedError
