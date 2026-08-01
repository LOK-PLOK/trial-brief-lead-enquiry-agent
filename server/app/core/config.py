"""Application configuration.

Single source of truth for environment-derived settings. Every other module
must obtain configuration through `get_settings()` rather than reading
`os.environ` directly, so that behaviour stays testable (settings can be
overridden by constructing `Settings(...)` explicitly, e.g. in tests) and
reproducible (a `config_snapshot` of these values is persisted per harness
batch — see docs/architecture.md section 5, HARNESS_BATCHES.config_snapshot).

Env file resolution order (first match wins), so the same code works both for
local development (repo-root `.env`) and inside the single Docker container
(real process env vars, no .env file present):
  1. Real process environment variables (e.g. platform secrets in production).
  2. `<repo_root>/.env`
  3. `<repo_root>/server/.env`

Path resolution note: `_SERVER_DIR` is deliberately computed from this file's
own location (two levels up: core/ -> app/ -> the "server" directory) rather
than via a fixed `parents[N]` guess at the repo root. That's because the root
Dockerfile's build (see Dockerfile) does `COPY server/ .` into `/app`, which
puts this file at `/app/app/core/config.py` — one directory shallower than
`<repo_root>/server/app/core/config.py` locally. Computing from a fixed depth
would silently point at the wrong directory inside the container; computing
relative to *this file* keeps `_SERVER_DIR` correct in both places
(`<repo_root>/server` locally, `/app` in the container built by the root
Dockerfile).
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SERVER_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = _SERVER_DIR.parent
_DEFAULT_DATABASE_URL = f"sqlite:///{_SERVER_DIR / 'data' / 'app.db'}"


class ModelProvider(str, Enum):
    """Supported LLM providers for the model adapter layer (server/app/llm/).

    Keep this enum as the single source of truth for valid `MODEL_PROVIDER`
    values; `llm/factory.py` switches on it to pick a concrete adapter.

    `OPENROUTER` is the one provider with a concrete `ModelAdapter`
    implementation today (`llm/openrouter_adapter.py`) — it exposes an
    OpenAI-compatible API in front of many backend models (selected via
    `MODEL_NAME`, e.g. `"openai/gpt-4o-mini"`), so a single adapter covers a
    wide range of models without pinning a provider-specific SDK dependency.
    `OPENAI`/`ANTHROPIC`/`OLLAMA` remain valid, documented extension points
    (see `llm/factory.py`'s docstring) with no adapter behind them yet.
    """

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    OPENROUTER = "openrouter"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(
            _REPO_ROOT / ".env",
            _SERVER_DIR / ".env",
        ),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"

    # Comma-separated browser origins allowed to call `/api/*` cross-origin.
    # Required when the SPA and API are on different hosts (e.g. separate
    # Render services). Example:
    #   CORS_ORIGINS=https://your-frontend.onrender.com,http://localhost:5173
    # Leave blank for same-origin / Vite-proxied local development.
    cors_origins: str = ""

    # --- Model provider selection ---
    # OpenRouter is the only provider with a real adapter today (see
    # ModelProvider's docstring), so it's the working default; `model_name`
    # is an OpenRouter model id ("<vendor>/<model>").
    model_provider: ModelProvider = ModelProvider.OPENROUTER
    model_name: str = "openai/gpt-4o-mini"

    # Optional per-stage overrides (fall back to `model_name` when unset).
    # A distinct verifier model is a documented, partial mitigation for
    # verifier independence (docs/architecture.md section 9 / section 15 risks).
    planner_model: str | None = None
    extractor_model: str | None = None
    verifier_model: str | None = None

    # --- Provider credentials (only the one matching model_provider is required) ---
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    ollama_host: str = "http://localhost:11434"
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # --- Database ---
    database_url: str = _DEFAULT_DATABASE_URL

    @field_validator("database_url", mode="before")
    @classmethod
    def _default_database_url_when_blank(cls, value: str | None) -> str:
        """Treat an explicitly-blank `DATABASE_URL=` (as shipped in
        `.env.example`, intended as "leave unset to use the built-in
        default") the same as truly unset.

        Without this, pydantic-settings treats an env var that is *present
        but empty* as an explicit override, silently discarding the default
        above and handing `create_engine()` an unparseable empty string
        (`sqlalchemy.exc.ArgumentError`) -- a real, previously-reproduced bug
        (see `docs/deployment.md` for OpenRouter / DATABASE_URL setup), since this
        field has no `or`-based fallback anywhere it's consumed, unlike e.g.
        `resolved_model()`'s stage-override fields.
        """
        return value or _DEFAULT_DATABASE_URL

    @model_validator(mode="after")
    def _check_provider_credentials(self) -> Settings:
        # Deliberately left permissive here rather than raising: `Settings()`
        # is constructed pervasively (every test's `test_settings` fixture,
        # scripts that never touch an adapter, etc.), most of which have no
        # need for a real credential at all (they inject a fake `ModelAdapter`
        # directly). The credential is required, and actually enforced, at
        # the one place it's actually needed instead: `OpenRouterAdapter.__init__`
        # (see llm/openrouter_adapter.py) raises `LLMProviderError` immediately
        # if constructed without `OPENROUTER_API_KEY` set -- i.e. exactly when
        # `llm/factory.py::get_adapter()` is actually called, not merely when
        # settings are loaded.
        return self

    def resolved_model(self, stage: str) -> str:
        """Return the model name to use for a given pipeline stage.

        `stage` is one of "planner" | "extractor" | "verifier". Falls back to
        `model_name` when no stage-specific override is set.
        """
        overrides = {
            "planner": self.planner_model,
            "extractor": self.extractor_model,
            "verifier": self.verifier_model,
        }
        return overrides.get(stage) or self.model_name

    def cors_origin_list(self) -> list[str]:
        """Parsed `CORS_ORIGINS` for `CORSMiddleware.allow_origins`."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Process-wide cached settings instance.

    Use as a FastAPI dependency (see server/app/api/deps.py) or call directly
    in scripts (e.g. evaluation/harness.py). `lru_cache` keeps this a cheap,
    single read of the environment per process.
    """
    return Settings()
