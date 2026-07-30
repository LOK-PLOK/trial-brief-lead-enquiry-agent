"""Structured JSON logging setup.

Per docs/architecture.md section 12: every log line should carry enough
correlation metadata (run_id, stage, event) to reconstruct a single run's
story across planner/executor/tools/verifier/repair. This module configures
the *ephemeral* stdout tier only; the *durable* tier (persisted per-run trace
and per-call LLM logs) lives in the database via server/app/db/models.py and
is populated by the agent/orchestrator, not by this logging module.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

# Bound per-request/per-run via `run_id_ctx.set(...)` so every log record
# emitted while handling that run is automatically tagged, without threading
# run_id through every function signature.
run_id_ctx: ContextVar[str | None] = ContextVar("run_id", default=None)
stage_ctx: ContextVar[str | None] = ContextVar("stage", default=None)


class _ContextFilter(logging.Filter):
    """Injects the current run_id/stage (if any) into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = run_id_ctx.get()
        record.stage = stage_ctx.get()
        return True


class _JsonFormatter(logging.Formatter):
    """Minimal dependency-free JSON log formatter.

    TODO(logging): swap for `structlog` or `python-json-logger` if/when the
    project takes on that dependency; keep the field names below stable
    (timestamp, level, logger, message, run_id, stage) since the eventual UI
    debug view and any log aggregation queries will key off them.
    """

    def format(self, record: logging.LogRecord) -> str:
        import json

        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "run_id": getattr(record, "run_id", None),
            "stage": getattr(record, "stage", None),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(log_level: str = "INFO") -> None:
    """Configure root logging once, at process startup.

    Call from `app/main.py` (API process) and from `evaluation/harness.py` /
    `evaluation/adversarial.py` (standalone scripts) so both paths get
    identically formatted, correlated logs.
    """
    root = logging.getLogger()
    root.setLevel(log_level)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(_JsonFormatter())
    handler.addFilter(_ContextFilter())

    root.handlers.clear()
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
