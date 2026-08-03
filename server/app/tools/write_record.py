"""`write_record`: persists the record, rejecting duplicates on a normalized
hash of email and phone. See docs/architecture.md section 8.

Deterministic — no LLM call. Delegates hashing to services/dedupe.py so that
logic is independently unit-testable and reusable.
"""

from __future__ import annotations

import time

from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import get_logger
from app.db import repository
from app.db.session import session_scope
from app.schemas.extraction import ExtractedFields
from app.services.dedupe import compute_dedupe_hash
from app.tools.base import Tool, ToolResult
from app.tools.lookup_jurisdiction_rule import JurisdictionRule
from app.tools.score_lead import ScoreLeadResult

logger = get_logger(__name__)


class WriteRecordArgs(BaseModel):
    extracted: ExtractedFields
    jurisdiction_rule: JurisdictionRule
    score: ScoreLeadResult


class WriteRecordResult(BaseModel):
    lead_id: str
    dedupe_hash: str


class WriteRecordTool(Tool):
    name = "write_record"
    description = "Persists the record, rejecting duplicates on a normalized hash of email and phone."
    args_schema = WriteRecordArgs
    result_schema = WriteRecordResult

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        """`session_factory` is an optional override, not a `ToolRegistry`
        dependency: `ToolRegistry._instantiate()` only recognizes the
        `adapter` parameter name, so it never supplies this and every
        production instantiation gets the default `None` -- meaning
        `run()` below falls back to `session_scope()`'s own real,
        process-wide cached engine, unchanged from before this parameter
        existed. It exists solely so tests can construct
        `WriteRecordTool(session_factory=...)` directly, pointed at an
        isolated throwaway test database, instead of writing into the real
        `app.db` file."""
        self._session_factory = session_factory

    def run(self, args: WriteRecordArgs) -> ToolResult:
        """`args` is already validated against `WriteRecordArgs` by
        `Tool.execute()` -- this only needs to do the actual persistence:
        check-then-insert against `dedupe_hash` (docs/contracts.md
        section 6's mandated pattern -- `find_lead_by_dedupe_hash` first,
        `create_lead` only if absent).

        Opens its own short-lived session via `db/session.py::session_scope()`
        rather than requiring a `ToolRegistry`-injected `Session`:
        `ToolRegistry` eagerly, fail-fast constructs every registered tool up
        front (docs/contracts.md section 4), so making a live `Session` a
        constructor dependency here would force every caller -- including
        the many existing tests that build a bare `ToolRegistry(adapter=...)`
        for `parse_enquiry`'s sake alone -- to also supply one, whether or
        not `write_record` ever actually runs. A dedicated session for this
        one write is exactly `session_scope()`'s own documented use case
        ("non-FastAPI callers ... one-off tools"), and is no less atomic
        than sharing the caller's session would be: `db/repository.py`
        already documents that no repository write function participates in
        cross-call transactions regardless (every write commits immediately
        on its own).
        """
        started = time.perf_counter()
        dedupe_hash = compute_dedupe_hash(args.extracted.email, args.extracted.phone)

        try:
            with session_scope(self._session_factory) as db:
                if repository.find_lead_by_dedupe_hash(db, dedupe_hash) is not None:
                    return ToolResult(
                        success=False, data=None, error="duplicate", latency_ms=_elapsed_ms(started)
                    )
                lead = repository.create_lead(
                    db,
                    dedupe_hash=dedupe_hash,
                    name=args.extracted.name,
                    email=args.extracted.email,
                    phone=args.extracted.phone,
                    country=args.extracted.country,
                    budget_band=args.extracted.budget_band.value,
                    asset_interest=args.extracted.asset_interest.value,
                    urgency=args.extracted.urgency.value,
                    score=args.score.score,
                    score_breakdown=args.score.breakdown,
                    jurisdiction_rule=args.jurisdiction_rule.model_dump(mode="json"),
                    # Optimistic default removed: write_record only runs after Verifier pass
                    # (docs/architecture.md section 10), so "accepted" here
                    # means the gate already cleared. Syncing to a later
                    # verifier failure is unnecessary because persistence
                    # never precedes verification.
                    status="accepted",
                    # `source_run_id` intentionally left unset: the `runs`
                    # row for *this* run is only persisted later, by
                    # agent/orchestrator.py's `Pipeline._persist()`, after
                    # the Verifier stage completes -- setting it here would
                    # reference a `runs.id` that doesn't exist yet, which
                    # SQLite's enforced foreign keys (db/session.py) would
                    # reject. Nullable for exactly this reason
                    # (docs/contracts.md section 8).
                )
                # Captured *inside* the session-scope block, before it
                # closes: `session_scope()`'s own clean-exit `commit()`
                # expires every object still attached to the session
                # (SQLAlchemy's default `expire_on_commit=True`), and the
                # subsequent `close()` detaches it -- accessing `lead.id`
                # after either of those would raise `DetachedInstanceError`.
                lead_id = lead.id
        except IntegrityError:
            # Race: a concurrent write inserted the same dedupe_hash between
            # our check and our insert -- still a controlled, expected
            # outcome (docs/contracts.md section 6), not a tool bug.
            logger.warning(
                "write_record lost a race to a concurrent duplicate insert",
                extra={"event": "write_record_race_duplicate", "dedupe_hash": dedupe_hash},
            )
            return ToolResult(success=False, data=None, error="duplicate", latency_ms=_elapsed_ms(started))

        result = WriteRecordResult(lead_id=lead_id, dedupe_hash=dedupe_hash)
        return ToolResult(
            success=True, data=result.model_dump(mode="json"), error=None, latency_ms=_elapsed_ms(started)
        )


def _elapsed_ms(perf_start: float) -> float:
    return (time.perf_counter() - perf_start) * 1000
