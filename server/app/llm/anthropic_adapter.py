"""Anthropic implementation of `ModelAdapter`.

TODO(llm/anthropic): implement using the Messages API with tool use, forcing
a single tool call whose input schema is `request.response_schema`'s JSON
schema (Anthropic has no native "JSON mode" equivalent to OpenAI's Structured
Outputs, so this is the standard way to get schema-shaped output). Populate
usage from the API's own `usage` field.
"""

from __future__ import annotations

from app.llm.base import ModelAdapter, StructuredCompletionRequest, StructuredCompletionResponse


class AnthropicAdapter(ModelAdapter):
    provider_name = "anthropic"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        # TODO(llm/anthropic): construct the Anthropic client here.

    def complete_structured(
        self, request: StructuredCompletionRequest
    ) -> StructuredCompletionResponse:
        raise NotImplementedError
