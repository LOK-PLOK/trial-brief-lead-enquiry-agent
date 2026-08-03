"""The provider-agnostic contract every model adapter must satisfy.

`agent/planner.py`, `agent/verifier.py`, and `tools/parse_enquiry.py` depend
only on `ModelAdapter` — never on a concrete provider class — so the model
can be swapped via `MODEL_PROVIDER` without touching pipeline code. See
docs/architecture.md section 6 and the confirmed provider-agnostic direction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


@dataclass
class StructuredCompletionRequest:
    """Normalized request shape passed to every adapter implementation."""

    system_prompt: str
    user_prompt: str
    response_schema: type[BaseModel]
    model: str
    temperature: float = 0.0
    # Cap completion size so providers (esp. OpenRouter) don't reserve a
    # huge default (often 16k) against remaining credits. Stage call sites
    # set tighter values; 1024 is the safe global default.
    max_tokens: int = 1024
    tools: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StructuredCompletionResponse:
    """Normalized response shape every adapter implementation must return.

    `parsed` is already validated against `request.response_schema` by the
    adapter before return; `raw_response` is kept for durable logging
    (docs/architecture.md section 12).
    """

    parsed: BaseModel
    raw_response: dict[str, Any]
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    model: str


class LLMProviderError(Exception):
    """Raised by a concrete `ModelAdapter` for any provider-level failure
    (timeout, rate limit, auth failure, malformed/unexpected response shape)
    — never let a bare provider SDK/HTTP exception escape an adapter, per
    docs/contracts.md section 5's failure-mode table: "Must propagate as an
    `LLMProviderError` (or equivalent) rather than a bare provider SDK
    exception, so `main.py`'s central exception handler can map it to a
    structured JSON error body with the correlating `run_id`."
    """


class LLMSchemaValidationError(Exception):
    """Raised by a concrete `ModelAdapter` when `request.response_schema`
    still fails to validate after the adapter's own one internal retry
    (docs/contracts.md section 5: "Schema validation fails on both the
    original attempt and the one internal retry" -> "Adapter surfaces this
    to the caller"). Callers (Planner/Verifier/`parse_enquiry`) are
    responsible for counting the *first* attempt's outcome toward the
    harness's schema-breach-rate metrics regardless of this exception.
    """


class ModelAdapter(ABC):
    """Common interface every concrete provider adapter must implement.

    See `llm/openrouter_adapter.py` for the one concrete implementation, and
    `llm/factory.py` for how it gets selected/constructed.
    """

    provider_name: str

    @abstractmethod
    def complete_structured(self, request: StructuredCompletionRequest) -> StructuredCompletionResponse:
        """Issue one stateless, structured-output LLM call.

        Must:
        - Never carry conversation history across calls (each stage's calls
          are isolated — development-rules.md "Each LLM call is isolated").
        - Return a `parsed` object that validates against
          `request.response_schema`, retrying internally at most once on
          schema validation failure (see docs/architecture.md section 6);
          callers are still responsible for counting/reporting the first
          attempt's outcome for the harness's schema-breach metrics. Raise
          `LLMSchemaValidationError` if both attempts fail to validate.
        - Raise `LLMProviderError` for any provider-level failure (timeout,
          rate limit, auth, malformed response) rather than a bare SDK/HTTP
          exception.

        OpenAI/Anthropic should use native structured-output / tool-calling
        modes where available; a model without *guaranteed* JSON-schema
        support (e.g. Ollama, or an arbitrary model proxied by OpenRouter)
        should fall back to instruct-then-validate-then-retry, and is
        expected to have a higher schema-breach rate — report that honestly,
        don't paper over it.
        """
        raise NotImplementedError
