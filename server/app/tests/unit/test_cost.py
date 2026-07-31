"""Unit tests for services/cost.py's aggregate_usage()."""

from __future__ import annotations

import pytest

from app.schemas.run import LlmCallUsage
from app.services.cost import aggregate_usage


def _usage(**overrides) -> LlmCallUsage:
    defaults = dict(
        stage="planner",
        model_provider="fake",
        model_name="fake-model",
        prompt_tokens=10,
        completion_tokens=5,
        cost_usd=0.01,
        latency_ms=100.0,
    )
    defaults.update(overrides)
    return LlmCallUsage(**defaults)


class TestAggregateUsage:
    def test_empty_list_returns_all_zero_totals(self) -> None:
        assert aggregate_usage([]) == {
            "total_tokens": 0.0,
            "total_cost_usd": 0,
            "total_latency_ms": 0,
        }

    def test_single_call_sums_prompt_and_completion_tokens(self) -> None:
        usage = aggregate_usage([_usage(prompt_tokens=100, completion_tokens=20)])
        assert usage["total_tokens"] == 120.0

    def test_sums_across_multiple_calls(self) -> None:
        calls = [
            _usage(stage="planner", prompt_tokens=100, completion_tokens=20, cost_usd=0.1, latency_ms=50.0),
            _usage(stage="verifier", prompt_tokens=200, completion_tokens=40, cost_usd=0.2, latency_ms=75.0),
        ]

        usage = aggregate_usage(calls)

        assert usage["total_tokens"] == 360.0
        assert usage["total_cost_usd"] == pytest.approx(0.3)
        assert usage["total_latency_ms"] == 125.0

    def test_is_a_pure_function_and_does_not_mutate_its_input(self) -> None:
        calls = [_usage()]
        original = list(calls)
        aggregate_usage(calls)
        assert calls == original
