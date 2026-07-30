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
    tools: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StructuredCompletionResponse:
    """Normalized response shape every adapter implementation must return.

    `parsed` is already validated against `request.response_schema` by the
    adapter (or the caller, per adapter — see TODO below); `raw_response` is
    kept for durable logging (docs/architecture.md section 12).
    """

    parsed: BaseModel
    raw_response: dict[str, Any]
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    model: str


class ModelAdapter(ABC):
    """Common interface every concrete provider adapter must implement.

    No concrete adapter exists yet — see `llm/factory.py` for how one gets
    selected/constructed once implemented.
    """

    provider_name: str

    @abstractmethod
    def complete_structured(
        self, request: StructuredCompletionRequest
    ) -> StructuredCompletionResponse:
        """Issue one stateless, structured-output LLM call.

        Must:
        - Never carry conversation history across calls (each stage's calls
          are isolated — development-rules.md "Each LLM call is isolated").
        - Return a `parsed` object that validates against
          `request.response_schema`, retrying internally at most once on
          schema validation failure (see docs/architecture.md section 6);
          callers are still responsible for counting/reporting the first
          attempt's outcome for the harness's schema-breach metrics.

        TODO(llm): implement per-provider. OpenAI/Anthropic should use native
        structured-output / tool-calling modes where available; Ollama (or
        any local model without guaranteed JSON-schema support) should fall
        back to instruct-then-validate-then-retry, and is expected to have a
        higher schema-breach rate — report that honestly, don't paper over it.
        """
        raise NotImplementedError
