"""In-process harness job runner for the web UI.

Wraps `evaluation.harness.run_harness` with progress + cooperative cancel.
Does not modify Planner / Executor / Verifier / Repair / tools.
"""

from __future__ import annotations

import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.core.logging import get_logger

logger = get_logger(__name__)

JobStatus = Literal["queued", "running", "completed", "cancelled", "failed"]


def _ensure_evaluation_on_path() -> None:
    """Make `evaluation` importable when the API runs from `server/`."""
    server_dir = Path(__file__).resolve().parents[2]  # .../server
    repo_root = server_dir.parent
    for candidate in (repo_root, server_dir / "evaluation_pkg_parent"):
        path = str(candidate)
        if (candidate / "evaluation").is_dir() and path not in sys.path:
            sys.path.insert(0, path)
            return
    # Docker layout: evaluation copied next to app package root (/app/evaluation)
    app_root = Path(__file__).resolve().parents[2]
    if (app_root / "evaluation").is_dir() and str(app_root) not in sys.path:
        sys.path.insert(0, str(app_root))


@dataclass
class HarnessJob:
    id: str
    dataset_id: str
    dataset_label: str
    n_repeats: int
    n_total: int
    status: JobStatus = "queued"
    batch_id: str | None = None
    completed: int = 0
    current_enquiry_id: str | None = None
    phase: str = "queued"
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "dataset_id": self.dataset_id,
                "dataset_label": self.dataset_label,
                "n_repeats": self.n_repeats,
                "n_total": self.n_total,
                "completed": self.completed,
                "status": self.status,
                "batch_id": self.batch_id,
                "current_enquiry_id": self.current_enquiry_id,
                "phase": self.phase,
                "error": self.error,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "cancel_requested": self.cancel_requested,
            }


class HarnessJobManager:
    """Process-local job registry (single uvicorn worker)."""

    def __init__(self) -> None:
        self._jobs: dict[str, HarnessJob] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> HarnessJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def start(
        self,
        *,
        dataset_id: str,
        dataset_label: str,
        enquiries: list[dict[str, Any]],
        n_repeats: int,
    ) -> HarnessJob:
        n_total = len(enquiries) * n_repeats
        with self._lock:
            # Only one active job at a time keeps SQLite + OpenRouter load sane.
            for existing in self._jobs.values():
                if existing.status in ("queued", "running"):
                    raise RuntimeError(
                        f"Harness job {existing.id} is already {existing.status}. "
                        "Cancel it or wait for it to finish before starting another."
                    )

        # Create the batch *before* returning so POST /jobs includes batch_id
        # and the dashboard can show dataset metadata immediately (not only
        # after the background thread reaches run_harness).
        batch_id = _create_job_batch(
            n_runs=n_total,
            dataset_id=dataset_id,
            dataset_label=dataset_label,
            n_repeats=n_repeats,
        )
        job = HarnessJob(
            id=str(uuid.uuid4()),
            dataset_id=dataset_id,
            dataset_label=dataset_label,
            n_repeats=n_repeats,
            n_total=n_total,
            batch_id=batch_id,
        )
        with self._lock:
            for existing in self._jobs.values():
                if existing.status in ("queued", "running"):
                    raise RuntimeError(
                        f"Harness job {existing.id} is already {existing.status}. "
                        "Cancel it or wait for it to finish before starting another."
                    )
            self._jobs[job.id] = job

        thread = threading.Thread(
            target=self._run_job,
            args=(job, enquiries),
            name=f"harness-job-{job.id[:8]}",
            daemon=True,
        )
        thread.start()
        return job

    def request_cancel(self, job_id: str) -> HarnessJob:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        with job._lock:
            if job.status in ("completed", "failed", "cancelled"):
                return job
            job.cancel_requested = True
            job.phase = "cancel_requested"
        return job

    def _run_job(self, job: HarnessJob, enquiries: list[dict[str, Any]]) -> None:
        _ensure_evaluation_on_path()
        from evaluation.harness import run_harness  # noqa: WPS433 -- lazy path bootstrap

        with job._lock:
            job.status = "running"
            job.started_at = datetime.now(UTC)
            job.phase = "starting"

        def on_progress(
            completed: int, total: int, enquiry_id: str | None, phase: str
        ) -> None:
            with job._lock:
                job.completed = completed
                job.n_total = total
                job.current_enquiry_id = enquiry_id
                job.phase = phase

        def should_cancel() -> bool:
            with job._lock:
                return job.cancel_requested

        try:
            def on_batch_created(batch_id: str) -> None:
                with job._lock:
                    job.batch_id = batch_id

            summary = run_harness(
                enquiries=enquiries,
                n_repeats=job.n_repeats,
                expected_n_enquiries=None,
                harness_batch_id=job.batch_id,
                on_progress=on_progress,
                should_cancel=should_cancel,
                on_batch_created=on_batch_created,
            )
            with job._lock:
                job.batch_id = summary.harness_batch_id
                job.finished_at = datetime.now(UTC)
                if job.cancel_requested:
                    job.status = "cancelled"
                    job.phase = "cancelled"
                else:
                    job.status = "completed"
                    job.phase = "finished"
                    job.completed = job.n_total
                job.current_enquiry_id = None
        except Exception as exc:  # noqa: BLE001 -- surface to UI; do not crash worker
            logger.exception(
                "harness job failed",
                extra={"event": "harness_job_failed", "job_id": job.id},
            )
            with job._lock:
                job.status = "failed"
                job.phase = "failed"
                job.error = str(exc) or exc.__class__.__name__
                job.finished_at = datetime.now(UTC)


def _create_job_batch(
    *,
    n_runs: int,
    dataset_id: str,
    dataset_label: str,
    n_repeats: int,
) -> str:
    """Persist a harness_batches row with dataset metadata for the dashboard."""
    from app.core.config import get_settings
    from app.db import repository
    from app.db.session import session_scope

    settings = get_settings()
    snapshot = {
        "model_provider": settings.model_provider.value,
        "model_name": settings.model_name,
        "planner_model": settings.planner_model,
        "extractor_model": settings.extractor_model,
        "verifier_model": settings.verifier_model,
        "dataset_id": dataset_id,
        "dataset_label": dataset_label,
        "n_repeats": n_repeats,
    }
    with session_scope() as db:
        batch = repository.create_harness_batch(
            db, n_runs=n_runs, config_snapshot=snapshot
        )
        return batch.id


_manager: HarnessJobManager | None = None


def get_job_manager() -> HarnessJobManager:
    global _manager
    if _manager is None:
        _manager = HarnessJobManager()
    return _manager
