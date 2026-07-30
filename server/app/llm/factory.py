"""Constructs the configured `ModelAdapter` from `Settings`.

This is the *only* place in the codebase that should import a concrete
adapter class; everywhere else must depend on `ModelAdapter` (see llm/base.py
and docs/architecture.md's confirmed provider-agnostic direction).
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import ModelProvider, Settings, get_settings
from app.llm.base import ModelAdapter


def build_adapter(settings: Settings) -> ModelAdapter:
    if settings.model_provider is ModelProvider.OPENAI:
        from app.llm.openai_adapter import OpenAIAdapter

        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when MODEL_PROVIDER=openai")
        return OpenAIAdapter(api_key=settings.openai_api_key)

    if settings.model_provider is ModelProvider.ANTHROPIC:
        from app.llm.anthropic_adapter import AnthropicAdapter

        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required when MODEL_PROVIDER=anthropic")
        return AnthropicAdapter(api_key=settings.anthropic_api_key)

    if settings.model_provider is ModelProvider.OLLAMA:
        from app.llm.ollama_adapter import OllamaAdapter

        return OllamaAdapter(host=settings.ollama_host)

    raise ValueError(f"Unsupported MODEL_PROVIDER: {settings.model_provider}")


@lru_cache
def get_adapter() -> ModelAdapter:
    """Process-wide cached adapter instance, built from `get_settings()`.

    Use as a FastAPI dependency (server/app/api/deps.py) or call directly in
    scripts (evaluation/harness.py). Note: this caches on the *default*
    settings; tests that need a different provider should construct
    `build_adapter(Settings(...))` directly rather than relying on this cache.
    """
    return build_adapter(get_settings())
