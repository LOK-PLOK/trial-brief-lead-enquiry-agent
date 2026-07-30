"""Drives the full evaluation harness: 15 enquiries x 3 repeats = 45 runs.
See docs/architecture.md section 11.

Run from the repo root with the `server` package on PYTHONPATH:

    PYTHONPATH=server python -m evaluation.harness

Must call the exact same `Pipeline.run()` used by the live API
(server/app/agent/orchestrator.py) — no separate/duplicate pipeline code —
so these numbers are reproducible by the assessor against real system
behaviour (docs/architecture.md section 11, hard gate 3 in the brief).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.agent.orchestrator import Pipeline
from app.core.config import get_settings
from app.db.session import get_session_factory, init_db
from app.llm.factory import get_adapter

from evaluation.metrics import compute_harness_metrics

FIXTURES_PATH = Path(__file__).resolve().parent / "fixtures" / "enquiries.json"
REPORTS_DIR = Path(__file__).resolve().parent / "reports"
N_REPEATS = 3


def load_enquiries() -> list[dict]:
    data = json.loads(FIXTURES_PATH.read_text())
    return data["enquiries"]


def run_harness(*, n_repeats: int = N_REPEATS) -> None:
    """TODO(evaluation/harness): implement.

    1. `enquiries = load_enquiries()` — fail loudly (not silently) if empty,
       since the harness cannot run without the 15 supplied samples.
    2. Create (or resume) a `HarnessBatch` row via db/repository.py.
    3. For each enquiry x each repeat_index in range(1, n_repeats + 1):
       - Skip if a `runs` row for (enquiry_id, repeat_index,
         harness_batch_id) already exists — makes the harness resumable
         across interruptions (docs/architecture.md section 15, rate-limit
         risk).
       - Otherwise call `pipeline.run(enquiry_text, enquiry_id=...,
         repeat_index=...)`.
    4. Once all 45 runs exist for this batch, call
       `compute_harness_metrics(batch_id)` and persist the result onto the
       `HarnessBatch` row (`finished_at`, `metrics`).
    5. Write a copy of the metrics to `reports/<timestamp>.json` as a
       durable artifact independent of the database (belt-and-braces per
       docs/architecture.md section 15, free-tier disk persistence risk).
    """
    settings = get_settings()
    init_db()
    adapter = get_adapter()
    _session_factory = get_session_factory()
    _pipeline = Pipeline(adapter=adapter, settings=settings)

    raise NotImplementedError


def _write_report(metrics: dict) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = REPORTS_DIR / f"{timestamp}.json"
    report_path.write_text(json.dumps(metrics, indent=2, default=str))
    return report_path


if __name__ == "__main__":
    run_harness()
