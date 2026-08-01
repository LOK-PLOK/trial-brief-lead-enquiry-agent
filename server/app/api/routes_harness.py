"""Harness read APIs + web job runner.

Existing read endpoints remain. New job endpoints wrap
`evaluation.harness.run_harness` without changing Planner/Executor/Verifier.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.mappers import harness_batch_to_summary, run_to_result, run_to_summary
from app.db import repository
from app.schemas.harness import (
    HarnessDashboard,
    HarnessDatasetOut,
    HarnessJobCreate,
    HarnessJobOut,
    HarnessParseRequest,
    HarnessParseResponse,
    HarnessParsedEnquiry,
    HarnessRunRow,
    HarnessSummary,
    PipelineHealth,
    TimelineEvent,
)
from app.schemas.run import RunResult, RunSummary
from app.services.harness_dashboard import build_dashboard, build_run_timeline
from app.services.harness_jobs import get_job_manager

router = APIRouter(prefix="/api/harness", tags=["harness"])


def _ensure_evaluation_importable() -> None:
    server_dir = Path(__file__).resolve().parents[2]
    repo_root = server_dir.parent
    for candidate in (repo_root, server_dir):
        if (candidate / "evaluation").is_dir() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
            return


def _load_datasets_module():
    _ensure_evaluation_importable()
    from evaluation import datasets as datasets_module  # noqa: WPS433

    return datasets_module


@router.get("/summary", response_model=HarnessSummary | None)
def harness_summary(db: Session = Depends(get_db)) -> HarnessSummary | None:
    """Latest batch summary (null body when none yet)."""
    batch = repository.get_latest_harness_batch(db)
    return harness_batch_to_summary(batch) if batch is not None else None


@router.get("/runs", response_model=list[RunSummary])
def harness_runs(db: Session = Depends(get_db)) -> list[RunSummary]:
    """Runs for the latest harness batch."""
    batch = repository.get_latest_harness_batch(db)
    if batch is None:
        return []
    return [run_to_summary(run) for run in repository.list_runs_for_batch(db, batch.id)]


@router.get("/datasets", response_model=list[HarnessDatasetOut])
def list_harness_datasets() -> list[HarnessDatasetOut]:
    datasets = _load_datasets_module()
    return [
        HarnessDatasetOut(
            id=d.id,
            label=d.label,
            description=d.description,
            n_enquiries=d.n_enquiries,
            default_repeats=d.default_repeats,
            requires_upload=d.path is None,
        )
        for d in datasets.list_datasets()
    ]


@router.post("/parse", response_model=HarnessParseResponse)
def parse_harness_input(body: HarnessParseRequest) -> HarnessParseResponse:
    """Preview-parse pasted/uploaded text into `{id, text}` enquiries."""
    _ensure_evaluation_importable()
    from evaluation.enquiry_parser import parse_enquiries_payload

    mode = body.mode if body.mode in {"auto", "single"} else "auto"
    try:
        enquiries, fmt = parse_enquiries_payload(
            text=body.text or None,
            structured=body.enquiries,
            filename=body.filename,
            mode=mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return HarnessParseResponse(
        format_detected=fmt,
        n_enquiries=len(enquiries),
        enquiries=[HarnessParsedEnquiry(**row) for row in enquiries],
    )


@router.post("/jobs", response_model=HarnessJobOut, status_code=202)
def start_harness_job(body: HarnessJobCreate) -> HarnessJobOut:
    if body.n_repeats not in (1, 3):
        raise HTTPException(status_code=422, detail="n_repeats must be 1 or 3")

    datasets = _load_datasets_module()
    try:
        info = datasets.get_dataset(body.dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        if body.dataset_id in {"custom", "single"}:
            if body.enquiries is None and not (body.raw_text and body.raw_text.strip()):
                raise ValueError(
                    "Custom/single dataset requires raw_text (paste/upload) or an enquiries payload."
                )
            enquiries = datasets.resolve_job_enquiries(
                dataset_id=body.dataset_id,
                structured=body.enquiries,
                raw_text=body.raw_text,
                filename=body.filename,
            )
        else:
            enquiries = datasets.load_dataset_enquiries(body.dataset_id)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    manager = get_job_manager()
    try:
        job = manager.start(
            dataset_id=info.id,
            dataset_label=info.label,
            enquiries=enquiries,
            n_repeats=body.n_repeats,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return HarnessJobOut(**job.snapshot())


@router.get("/jobs/{job_id}", response_model=HarnessJobOut)
def get_harness_job(job_id: str) -> HarnessJobOut:
    job = get_job_manager().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown harness job {job_id!r}")
    return HarnessJobOut(**job.snapshot())


@router.post("/jobs/{job_id}/cancel", response_model=HarnessJobOut)
def cancel_harness_job(job_id: str) -> HarnessJobOut:
    manager = get_job_manager()
    try:
        job = manager.request_cancel(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown harness job {job_id!r}") from exc
    return HarnessJobOut(**job.snapshot())


@router.get("/batches/{batch_id}/dashboard", response_model=HarnessDashboard)
def harness_batch_dashboard(batch_id: str, db: Session = Depends(get_db)) -> HarnessDashboard:
    batch = repository.get_harness_batch(db, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"Unknown harness batch {batch_id!r}")

    runs = repository.list_runs_for_batch(db, batch.id)
    snapshot = batch.config_snapshot if isinstance(batch.config_snapshot, dict) else {}
    raw = build_dashboard(
        batch_id=batch.id,
        started_at=batch.started_at,
        finished_at=batch.finished_at,
        dataset_id=snapshot.get("dataset_id"),
        dataset_label=snapshot.get("dataset_label"),
        n_repeats=snapshot.get("n_repeats"),
        metrics=batch.metrics,
        runs=runs,
    )
    metrics = None
    if raw["metrics"] is not None:
        from app.schemas.harness import HarnessMetrics

        metrics = HarnessMetrics.model_validate(raw["metrics"])

    return HarnessDashboard(
        batch_id=raw["batch_id"],
        dataset_id=raw["dataset_id"],
        dataset_label=raw["dataset_label"],
        n_repeats=raw["n_repeats"],
        started_at=raw["started_at"],
        finished_at=raw["finished_at"],
        duration_ms=raw["duration_ms"],
        statistics=raw["statistics"],
        cost=raw["cost"],
        performance=raw["performance"],
        failure_breakdown=raw["failure_breakdown"],
        pipeline_health=PipelineHealth(**raw["pipeline_health"]),
        charts=raw["charts"],
        metrics=metrics,
        runs=[HarnessRunRow(**row) for row in raw["runs"]],
    )


@router.get("/batches/{batch_id}/runs", response_model=list[HarnessRunRow])
def harness_batch_runs(batch_id: str, db: Session = Depends(get_db)) -> list[HarnessRunRow]:
    batch = repository.get_harness_batch(db, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"Unknown harness batch {batch_id!r}")
    dash = build_dashboard(
        batch_id=batch.id,
        started_at=batch.started_at,
        finished_at=batch.finished_at,
        dataset_id=None,
        dataset_label=None,
        n_repeats=None,
        metrics=batch.metrics,
        runs=repository.list_runs_for_batch(db, batch.id),
    )
    return [HarnessRunRow(**row) for row in dash["runs"]]


@router.get("/runs/{run_id}/timeline", response_model=list[TimelineEvent])
def harness_run_timeline(run_id: str, db: Session = Depends(get_db)) -> list[TimelineEvent]:
    run = repository.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Unknown run {run_id!r}")
    return [TimelineEvent(**event) for event in build_run_timeline(run)]


@router.get("/runs/{run_id}", response_model=RunResult)
def harness_run_detail(run_id: str, db: Session = Depends(get_db)) -> RunResult:
    """Convenience alias for run detail from the harness UI."""
    run = repository.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Unknown run {run_id!r}")
    return run_to_result(run)
