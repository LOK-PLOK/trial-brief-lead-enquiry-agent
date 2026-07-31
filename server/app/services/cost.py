"""Aggregates per-call `LlmCallUsage` entries into per-run totals.

Used by agent/orchestrator.py when assembling a `RunResult`, and by
evaluation/metrics.py when computing the harness's mean tokens/cost/latency
figures — both should call this rather than re-summing ad hoc, so the two
numbers can never disagree.
"""

from __future__ import annotations

from app.schemas.run import LlmCallUsage


def aggregate_usage(calls: list[LlmCallUsage]) -> dict[str, float]:
    """Sum `calls` into the three per-run totals `RunResult` carries
    (`total_tokens`, `total_cost_usd`, `total_latency_ms`). Pure function,
    no I/O -- returns all-zero totals for an empty list (e.g. a run that
    failed before any LLM call was made)."""
    return {
        "total_tokens": float(sum(call.prompt_tokens + call.completion_tokens for call in calls)),
        "total_cost_usd": sum(call.cost_usd for call in calls),
        "total_latency_ms": sum(call.latency_ms for call in calls),
    }
