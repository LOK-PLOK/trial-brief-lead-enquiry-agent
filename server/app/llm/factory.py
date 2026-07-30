"""Constructs the configured `ModelAdapter` from `Settings`.

This is the *only* place in the codebase that should import a concrete
adapter class; everywhere else must depend on `ModelAdapter` (see llm/base.py
and docs/architecture.md's confirmed provider-agnostic direction).

No concrete adapter is implemented yet — the scaffold review removed the
placeholder OpenAI/Anthropic/Ollama adapter modules (and their SDK
dependencies) since none had a real implementation, to avoid carrying unused
code and dependencies before a provider is chosen. Implementing one is a
two-step change that never touches planner/executor/verifier/tools:
  1. Add `app/llm/<provider>_adapter.py` implementing `ModelAdapter`.
  2. Add a branch below that imports it and returns an instance.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import Settings, get_settings
from app.llm.base import ModelAdapter


def build_adapter(settings: Settings) -> ModelAdapter:
    # TODO(llm/factory): branch on settings.model_provider (see
    # app.core.config.ModelProvider) once a concrete adapter exists, e.g.:
    #     if settings.model_provider is ModelProvider.OPENAI:
    #         from app.llm.openai_adapter import OpenAIAdapter
    #         return OpenAIAdapter(api_key=settings.openai_api_key)
    raise NotImplementedError(
        f"No ModelAdapter implementation exists yet for MODEL_PROVIDER="
        f"{settings.model_provider.value!r}. Add app/llm/<provider>_adapter.py "
        f"and wire it in here."
    )


@lru_cache
def get_adapter() -> ModelAdapter:
    """Process-wide cached adapter instance, built from `get_settings()`.

    Use as a FastAPI dependency (server/app/api/deps.py) or call directly in
    scripts (evaluation/harness.py). Note: this caches on the *default*
    settings; tests that need a different provider should construct
    `build_adapter(Settings(...))` directly rather than relying on this cache.
    """
    return build_adapter(get_settings())
