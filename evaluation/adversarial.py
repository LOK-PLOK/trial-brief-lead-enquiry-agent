"""Runs the 3 hand-written adversarial enquiries against the finished system
and reports what happened, per docs/Trial_Brief_Paul_Detablan.md section 3.7
and docs/architecture.md section 14 (adversarial sequence diagram).

Run from the repo root with the `server` package on PYTHONPATH:

    PYTHONPATH=server python -m evaluation.adversarial

Reported qualitatively, separate from the 45-run harness aggregate
(docs/architecture.md section 11) — these are deliberately designed to break
the system, not representative "normal" runs.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.agent.orchestrator import Pipeline
from app.core.config import get_settings
from app.db.session import init_db
from app.llm.factory import get_adapter

FIXTURES_PATH = Path(__file__).resolve().parent / "fixtures" / "adversarial_enquiries.json"


def load_adversarial_enquiries() -> list[dict]:
    data = json.loads(FIXTURES_PATH.read_text())
    return data["enquiries"]


def run_adversarial() -> None:
    """TODO(evaluation/adversarial):

    1. `enquiries = load_adversarial_enquiries()`.
    2. For each: `pipeline.run(enquiry_text, is_adversarial=True)`.
    3. Report explicitly, per enquiry: what the plan/trace/record/verifier
       decision were, and — critically — whether the injection *succeeded*
       (e.g. did the executor actually skip lookup_jurisdiction_rule, or
       record an inflated budget band) even if the verifier ultimately
       caught it. The brief asks to "report what happened, including
       anything that succeeded" — do not only report catches.
    """
    settings = get_settings()
    init_db()
    adapter = get_adapter()
    _pipeline = Pipeline(adapter=adapter, settings=settings)

    raise NotImplementedError


if __name__ == "__main__":
    run_adversarial()
