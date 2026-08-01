"""API response shapes for `GET /api/harness/summary` and `GET
/api/harness/runs`. See docs/architecture.md section 11, evaluation/metrics.py
(the actual metrics computation these mirror), and docs/contracts.md section 7,
which lists both endpoints' response models as "(TODO: not yet typed)" -- these
are that type, added now that the routes themselves are implemented (Phase 3).

Field-for-field mirrors of `evaluation/metrics.py::_compute_metrics_from_runs()`'s
returned dict (`HarnessMetrics`/`HarnessVariance`/`HarnessVarianceByEnquiry`) and
the `harness_batches` row (`HarnessSummary`) -- and, in turn, of the frontend's
hand-mirrored `client/src/api/types.ts` (same names), which predates this file
and is treated as the authoritative shape to match exactly rather than
independently redesigned.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class HarnessVarianceByEnquiry(BaseModel):
    n_repeats: int
    final_statuses: list[str]
    latency_ms_stdev: float
    cost_usd_stdev: float
    tokens_stdev: float
    score_stdev: float | None = None
    distinct_final_record_count: int


class HarnessVariance(BaseModel):
    by_enquiry: dict[str, HarnessVarianceByEnquiry]
    overall_latency_ms_stdev: float
    overall_cost_usd_stdev: float
    overall_tokens_stdev: float


class HarnessMetrics(BaseModel):
    n_runs: int
    completion_rate: float | None = None
    quarantined_rate: float | None = None
    error_rate: float | None = None
    pass_rate: float | None = None
    fabrication_rate: float | None = None
    fabrication_caught_rate: float | None = None
    planner_schema_breach_rate: float | None = None
    extractor_schema_breach_rate: float | None = None
    tool_selection_accuracy: float | None = None
    repair_attempted_rate: float | None = None
    repair_success_rate: float | None = None
    mean_latency_ms: float = 0.0
    median_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    mean_tokens: float = 0.0
    mean_cost_usd: float = 0.0
    variance: HarnessVariance
    notes: list[str] = []


class HarnessSummary(BaseModel):
    """Mirrors one `harness_batches` row (`app.db.models.HarnessBatch`)."""

    id: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    n_runs: int
    metrics: HarnessMetrics | None = None
    config_snapshot: dict | None = None

    model_config = ConfigDict(from_attributes=True)


# --- Job API + dashboard (web harness runner) -----------------------------


class HarnessDatasetOut(BaseModel):
    id: str
    label: str
    description: str
    n_enquiries: int
    default_repeats: int
    requires_upload: bool = False


class HarnessJobCreate(BaseModel):
    dataset_id: str = "standard"
    n_repeats: int = 1
    # Structured JSON for custom datasets: {enquiries:[{id,text},...]} or bare list.
    enquiries: dict | list | None = None
    # Plain-text / file contents for custom or single modes (.txt/.md/.csv/.json).
    raw_text: str | None = None
    filename: str | None = None


class HarnessParseRequest(BaseModel):
    text: str = ""
    filename: str | None = None
    mode: str = "auto"  # auto | single
    enquiries: dict | list | None = None


class HarnessParsedEnquiry(BaseModel):
    id: str
    text: str


class HarnessParseResponse(BaseModel):
    format_detected: str
    n_enquiries: int
    enquiries: list[HarnessParsedEnquiry]


class HarnessJobOut(BaseModel):
    id: str
    dataset_id: str
    dataset_label: str
    n_repeats: int
    n_total: int
    completed: int
    status: str
    batch_id: str | None = None
    current_enquiry_id: str | None = None
    phase: str
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested: bool = False


class HarnessRunRow(BaseModel):
    id: str
    enquiry_id: str | None = None
    repeat_index: int | None = None
    final_status: str
    score: float | None = None
    repair_attempted: bool = False
    repair_succeeded: bool | None = None
    total_latency_ms: float = 0.0
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    verifier_pass: bool | None = None
    confidence: float | None = None
    reason: str = ""
    error_type: str | None = None
    error_message: str | None = None
    traceback: str | None = None
    fabrication_detected: bool = False
    fabricated_fields: list[str] = []
    plan_deviation_detected: bool = False
    created_at: datetime | None = None


class PipelineHealth(BaseModel):
    planner: str
    executor: str
    tools: str
    verifier: str
    repair: str
    note: str | None = None


class HarnessDashboard(BaseModel):
    batch_id: str
    dataset_id: str | None = None
    dataset_label: str | None = None
    n_repeats: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: float | None = None
    statistics: dict
    cost: dict
    performance: dict
    failure_breakdown: dict[str, int]
    pipeline_health: PipelineHealth
    charts: dict
    metrics: HarnessMetrics | None = None
    runs: list[HarnessRunRow]


class TimelineEvent(BaseModel):
    stage: str
    label: str
    status: str  # ok | warn | error
    detail: str = ""
