"""Per-model $/token pricing used to compute `cost_usd` for every LLM call.

Kept separate from the adapters themselves so pricing can be updated without
touching request/response handling code, and so it's trivially unit-testable.
"""

from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)

# USD per 1,000 tokens. TODO(llm/pricing): fill in real, current prices for
# whichever models are actually used before running the evaluation harness —
# empty for now (no concrete adapter/model is chosen yet, see llm/factory.py),
# which is a real, honestly-reported gap: every remote model call currently
# falls through to the "unrecognized model" branch below and reports
# cost_usd=0.0 rather than a guessed number.
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

    Returns `LOCAL_PROVIDER_COST_USD` (0.0) unconditionally for local models
    (`is_local=True`) -- Ollama has no per-token API cost to look up. For a
    remote model, looks it up in `PRICING_PER_1K_TOKENS`; an unrecognized
    model logs a warning and returns 0.0 rather than raising, so a
    pricing-table gap (currently: every remote model, since the table above
    is empty) never crashes a run -- it just honestly under-reports cost
    instead of fabricating a number.
    """
    if is_local:
        return LOCAL_PROVIDER_COST_USD

    pricing = PRICING_PER_1K_TOKENS.get(model)
    if pricing is None:
        logger.warning(
            f"no pricing entry for model {model!r}; reporting cost_usd=0.0 for this call",
            extra={"event": "pricing_table_miss", "model": model},
        )
        return 0.0

    return (prompt_tokens / 1000) * pricing["prompt"] + (completion_tokens / 1000) * pricing["completion"]
