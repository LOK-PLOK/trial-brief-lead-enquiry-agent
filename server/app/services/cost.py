"""Aggregates per-call `LlmCallUsage` entries into per-run totals.

Used by agent/orchestrator.py when assembling a `RunResult`, and by
evaluation/metrics.py when computing the harness's mean tokens/cost/latency
figures — both should call this rather than re-summing ad hoc, so the two
numbers can never disagree.
"""

from __future__ import annotations

from app.schemas.run import LlmCallUsage


def aggregate_usage(calls: list[LlmCallUsage]) -> dict[str, float]:
    """TODO(services/cost): return
    {"total_tokens": ..., "total_cost_usd": ..., "total_latency_ms": ...}
    summed across `calls`. Pure function, no I/O.
    """
    raise NotImplementedError
