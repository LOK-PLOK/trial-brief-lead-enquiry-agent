"""Local open-weight model implementation of `ModelAdapter`, via Ollama.

TODO(llm/ollama): implement against Ollama's local HTTP API. Weaker/no
guarantee of native structured-output support compared to OpenAI/Anthropic,
so this adapter should use an instruct-then-validate-then-retry strategy
(prompt the model to emit JSON matching `request.response_schema`, parse,
validate, retry once on failure). Token usage may need to be estimated
(e.g. via a tokenizer) rather than read from a response field — note this
approximation explicitly wherever it's used (docs/architecture.md section 15,
"structured-output non-compliance varies by provider").
"""

from __future__ import annotations

from app.llm.base import ModelAdapter, StructuredCompletionRequest, StructuredCompletionResponse


class OllamaAdapter(ModelAdapter):
    provider_name = "ollama"

    def __init__(self, host: str) -> None:
        self._host = host
        # TODO(llm/ollama): construct the Ollama client here.

    def complete_structured(
        self, request: StructuredCompletionRequest
    ) -> StructuredCompletionResponse:
        raise NotImplementedError
