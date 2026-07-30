"""OpenAI implementation of `ModelAdapter`.

TODO(llm/openai): implement using the OpenAI Responses/Chat Completions API
with native Structured Outputs (`response_format={"type": "json_schema", ...}`
built from `request.response_schema.model_json_schema()`) and/or native tool
calling. Populate `StructuredCompletionResponse.prompt_tokens` /
`completion_tokens` from the API's own `usage` field (measured, not
estimated — docs/architecture.md section 12).
"""

from __future__ import annotations

from app.llm.base import ModelAdapter, StructuredCompletionRequest, StructuredCompletionResponse


class OpenAIAdapter(ModelAdapter):
    provider_name = "openai"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        # TODO(llm/openai): construct the OpenAI client here.

    def complete_structured(
        self, request: StructuredCompletionRequest
    ) -> StructuredCompletionResponse:
        raise NotImplementedError
