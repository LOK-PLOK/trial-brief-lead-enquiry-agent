"""Orchestration tests for the web harness job manager (no LLM)."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker

from app.db import repository
from app.db import session as session_module
from app.services import harness_jobs
from app.services.harness_dashboard import build_dashboard


@pytest.fixture
def isolated_manager(
    db_engine, monkeypatch: pytest.MonkeyPatch
) -> harness_jobs.HarnessJobManager:
    factory = sessionmaker(bind=db_engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(session_module, "get_session_factory", lambda: factory)

    manager = harness_jobs.HarnessJobManager()
    monkeypatch.setattr(harness_jobs, "_manager", manager)
    return manager


def _patch_run_harness(monkeypatch: pytest.MonkeyPatch, fake_run_harness) -> None:
    harness_jobs._ensure_evaluation_on_path()
    import evaluation.harness as harness_mod

    monkeypatch.setattr(harness_mod, "run_harness", fake_run_harness)


def test_start_assigns_batch_id_before_thread_runs(
    db_session, isolated_manager: harness_jobs.HarnessJobManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /jobs must return a durable batch_id immediately (dashboard race)."""
    started = threading.Event()
    release = threading.Event()

    def fake_run_harness(**kwargs):
        started.set()
        release.wait(timeout=2)
        assert kwargs.get("harness_batch_id") is not None
        return SimpleNamespace(harness_batch_id=kwargs["harness_batch_id"])

    _patch_run_harness(monkeypatch, fake_run_harness)

    job = isolated_manager.start(
        dataset_id="standard",
        dataset_label="Standard Evaluation",
        enquiries=[{"id": "e1", "text": "hello"}],
        n_repeats=1,
    )

    assert job.batch_id is not None
    batch = repository.get_harness_batch(db_session, job.batch_id)
    assert batch is not None
    assert batch.config_snapshot["dataset_id"] == "standard"
    assert batch.config_snapshot["dataset_label"] == "Standard Evaluation"
    assert batch.finished_at is None

    assert started.wait(timeout=2)
    release.set()
    deadline = time.time() + 2
    while job.status not in ("completed", "failed", "cancelled") and time.time() < deadline:
        time.sleep(0.05)
    assert job.status == "completed"


def test_dashboard_sees_dataset_label_on_new_batch(
    db_session, isolated_manager: harness_jobs.HarnessJobManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run_harness(**kwargs):
        return SimpleNamespace(harness_batch_id=kwargs["harness_batch_id"])

    _patch_run_harness(monkeypatch, fake_run_harness)

    job = isolated_manager.start(
        dataset_id="manual_e01_e14",
        dataset_label="Manual Test Suite (E01–E14)",
        enquiries=[{"id": "E01", "text": "x"}],
        n_repeats=1,
    )
    assert job.snapshot()["batch_id"] == job.batch_id

    batch = repository.get_harness_batch(db_session, job.batch_id)
    assert batch is not None
    dash = build_dashboard(
        batch_id=batch.id,
        started_at=batch.started_at,
        finished_at=batch.finished_at,
        dataset_id=batch.config_snapshot.get("dataset_id"),
        dataset_label=batch.config_snapshot.get("dataset_label"),
        n_repeats=batch.config_snapshot.get("n_repeats"),
        metrics=batch.metrics,
        runs=[],
    )
    assert dash["dataset_label"] == "Manual Test Suite (E01–E14)"
    assert dash["statistics"]["total"] == 0
