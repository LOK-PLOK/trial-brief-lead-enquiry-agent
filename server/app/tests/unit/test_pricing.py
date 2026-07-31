"""Unit tests for llm/pricing.py's estimate_cost_usd()."""

from __future__ import annotations

from app.llm import pricing as pricing_module
from app.llm.pricing import estimate_cost_usd


class TestEstimateCostUsd:
    def test_local_model_is_always_zero_cost(self) -> None:
        assert estimate_cost_usd("any-model", 1_000_000, 1_000_000, is_local=True) == 0.0

    def test_unrecognized_remote_model_returns_zero_and_does_not_raise(self) -> None:
        assert estimate_cost_usd("totally-unknown-model", 1000, 1000, is_local=False) == 0.0

    def test_unrecognized_remote_model_logs_a_warning(self, caplog) -> None:
        import logging

        with caplog.at_level(logging.WARNING):
            estimate_cost_usd("totally-unknown-model", 10, 10, is_local=False)

        assert any("totally-unknown-model" in record.message for record in caplog.records)
        assert all(record.levelname == "WARNING" for record in caplog.records)

    def test_recognized_remote_model_computes_cost_from_the_pricing_table(self, monkeypatch) -> None:
        monkeypatch.setitem(
            pricing_module.PRICING_PER_1K_TOKENS, "priced-model", {"prompt": 1.0, "completion": 2.0}
        )

        cost = estimate_cost_usd("priced-model", prompt_tokens=1000, completion_tokens=500, is_local=False)

        assert cost == (1000 / 1000) * 1.0 + (500 / 1000) * 2.0

    def test_zero_tokens_costs_nothing_even_for_a_priced_model(self, monkeypatch) -> None:
        monkeypatch.setitem(
            pricing_module.PRICING_PER_1K_TOKENS, "priced-model", {"prompt": 5.0, "completion": 5.0}
        )
        assert estimate_cost_usd("priced-model", 0, 0, is_local=False) == 0.0
