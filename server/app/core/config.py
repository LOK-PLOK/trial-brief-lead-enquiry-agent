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

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SERVER_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = _SERVER_DIR.parent


class ModelProvider(str, Enum):
    """Supported LLM providers for the model adapter layer (server/app/llm/).

    Keep this enum as the single source of truth for valid `MODEL_PROVIDER`
    values; `llm/factory.py` switches on it to pick a concrete adapter.
    """

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"


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

    # --- Model provider selection ---
    model_provider: ModelProvider = ModelProvider.OPENAI
    model_name: str = "gpt-4o-mini"

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

    # --- Database ---
    database_url: str = f"sqlite:///{_SERVER_DIR / 'data' / 'app.db'}"

    @model_validator(mode="after")
    def _check_provider_credentials(self) -> Settings:
        # TODO(config): once adapters exist, decide whether missing credentials
        # should raise here (fail fast) or only when that adapter is actually
        # instantiated by llm/factory.py. Left permissive for now so the app
        # can boot without secrets configured (e.g. CI, first-run scaffolding).
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


@lru_cache
def get_settings() -> Settings:
    """Process-wide cached settings instance.

    Use as a FastAPI dependency (see server/app/api/deps.py) or call directly
    in scripts (e.g. evaluation/harness.py). `lru_cache` keeps this a cheap,
    single read of the environment per process.
    """
    return Settings()
