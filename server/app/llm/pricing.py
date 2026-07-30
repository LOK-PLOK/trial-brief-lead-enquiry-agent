"""Per-model $/token pricing used to compute `cost_usd` for every LLM call.

Kept separate from the adapters themselves so pricing can be updated without
touching request/response handling code, and so it's trivially unit-testable.
"""

from __future__ import annotations

# USD per 1,000 tokens. TODO(llm/pricing): fill in real, current prices for
# whichever models are actually used before running the evaluation harness —
# the numbers below are illustrative placeholders only.
PRICING_PER_1K_TOKENS: dict[str, dict[str, float]] = {
    # "gpt-4o-mini": {"prompt": 0.0, "completion": 0.0},
    # "claude-3-5-haiku": {"prompt": 0.0, "completion": 0.0},
}

# Local models (e.g. via Ollama) have no per-token API cost.
LOCAL_PROVIDER_COST_USD = 0.0


def estimate_cost_usd(
    model: str, prompt_tokens: int, completion_tokens: int, *, is_local: bool = False
) -> float:
    """Compute cost for one LLM call from its token usage.

    TODO(llm/pricing): implement the actual lookup + calculation once a model
    is chosen. Must return 0.0 for local/Ollama models and log a warning (not
    raise) for an unrecognized remote model, so a pricing-table gap never
    crashes a run.
    """
    raise NotImplementedError
