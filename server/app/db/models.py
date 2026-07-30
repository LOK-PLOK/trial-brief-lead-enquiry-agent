"""SQLAlchemy ORM models mirroring docs/architecture.md section 5.

Table/column definitions only — no query logic here (see repository.py for
data-access functions). JSON columns store the corresponding Pydantic
schema's `.model_dump()` output; deserialize back through the matching
class in `app/schemas/` when reading, so the DB row and the API/agent
objects never drift apart.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(UTC)


class HarnessBatch(Base):
    """One row per full evaluation harness execution (docs/architecture.md
    section 5, HARNESS_BATCHES; section 11, Evaluation Harness)."""

    __tablename__ = "harness_batches"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    n_runs: Mapped[int] = mapped_column(Integer, default=0)
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    config_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    runs: Mapped[list[Run]] = relationship(back_populates="harness_batch")


class Run(Base):
    """One pipeline execution: a live API request or one of the harness's
    45 runs. Mirrors `app.schemas.run.RunResult` (docs/architecture.md
    section 5, RUNS)."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    enquiry_text: Mapped[str] = mapped_column(Text)
    enquiry_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    repeat_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_adversarial: Mapped[bool] = mapped_column(Boolean, default=False)

    plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    plan_schema_valid: Mapped[bool] = mapped_column(Boolean, default=False)

    tool_call_trace: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    final_record: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    verifier_pass: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    verifier_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    verifier_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    fabrication_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    plan_deviation_detected: Mapped[bool] = mapped_column(Boolean, default=False)

    repair_attempted: Mapped[bool] = mapped_column(Boolean, default=False)
    repair_succeeded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    final_status: Mapped[str] = mapped_column(String, default="error")  # completed|quarantined|error

    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    total_latency_ms: Mapped[float] = mapped_column(Float, default=0.0)

    model_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String, nullable=True)

    harness_batch_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("harness_batches.id"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    harness_batch: Mapped[HarnessBatch | None] = relationship(back_populates="runs")
    llm_calls: Mapped[list[LlmCall]] = relationship(back_populates="run")
    lead: Mapped[Lead | None] = relationship(back_populates="source_run")


class LlmCall(Base):
    """Fine-grained per-call log for observability/cost breakdown
    (docs/architecture.md section 5, LLM_CALLS; section 12, Logging)."""

    __tablename__ = "llm_calls"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"), index=True)
    # One of: planner | parse_enquiry | verifier | repair_planner | repair_verifier
    stage: Mapped[str] = mapped_column(String)

    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)

    model_provider: Mapped[str] = mapped_column(String)
    model_name: Mapped[str] = mapped_column(String)

    raw_request: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    run: Mapped[Run] = relationship(back_populates="llm_calls")


class Lead(Base):
    """An accepted or quarantined lead record (docs/architecture.md section
    5, LEADS)."""

    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    dedupe_hash: Mapped[str] = mapped_column(String, unique=True, index=True)

    name: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    country: Mapped[str | None] = mapped_column(String, nullable=True)
    budget_band: Mapped[str | None] = mapped_column(String, nullable=True)
    asset_interest: Mapped[str | None] = mapped_column(String, nullable=True)
    urgency: Mapped[str | None] = mapped_column(String, nullable=True)

    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    jurisdiction_rule: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    status: Mapped[str] = mapped_column(String)  # accepted|quarantined

    source_run_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("runs.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    source_run: Mapped[Run | None] = relationship(back_populates="lead")
