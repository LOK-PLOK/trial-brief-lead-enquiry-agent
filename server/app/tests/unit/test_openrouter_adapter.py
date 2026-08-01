"""Unit tests for llm/openrouter_adapter.py. See docs/contracts.md section 5.

No real network call is ever made: `OpenRouterAdapter` accepts an injectable
`client=` (see its constructor) satisfying only the tiny subset of
`httpx.Client`'s interface this module actually uses (`.post()`), so these
tests substitute a scripted fake instead of hitting the real API or mocking
`httpx` internals.
"""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel

from app.llm.base import (
    LLMProviderError,
    LLMSchemaValidationError,
    StructuredCompletionRequest,
)
from app.llm.openrouter_adapter import OpenRouterAdapter


class _Widget(BaseModel):
    """A minimal schema, deliberately unrelated to any real pipeline schema
    -- these tests exercise the adapter's own contract in isolation, not any
    particular stage's prompts."""

    name: str
    count: int


def _chat_completion_payload(content: str, *, model: str = "openai/gpt-4o-mini") -> dict:
    return {
        "id": "chatcmpl-test",
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 42, "completion_tokens": 7},
    }


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, json_body: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._json_body = json_body
        self.text = text or json.dumps(json_body or {})

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
            response = httpx.Response(self.status_code, request=request, text=self.text)
            raise httpx.HTTPStatusError(f"status {self.status_code}", request=request, response=response)

    def json(self) -> dict:
        return self._json_body


class _FakeClient:
    """Scripted stand-in for `httpx.Client`: returns each of `responses` in
    order, one per `.post()` call, and records every call's payload for
    assertions."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url: str, *, headers: dict, json: dict) -> _FakeResponse:  # noqa: A002
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self._responses.pop(0)


def _make_request(**overrides) -> StructuredCompletionRequest:
    defaults = {
        "system_prompt": "You are a test system prompt.",
        "user_prompt": "Produce a Widget.",
        "response_schema": _Widget,
        "model": "openai/gpt-4o-mini",
        "metadata": {"stage": "test"},
    }
    defaults.update(overrides)
    return StructuredCompletionRequest(**defaults)


class TestConstruction:
    def test_missing_api_key_raises_llm_provider_error(self) -> None:
        with pytest.raises(LLMProviderError):
            OpenRouterAdapter(api_key=None)

    def test_empty_api_key_raises_llm_provider_error(self) -> None:
        with pytest.raises(LLMProviderError):
            OpenRouterAdapter(api_key="")

    def test_provider_name_is_openrouter(self) -> None:
        adapter = OpenRouterAdapter(api_key="sk-test", client=_FakeClient([]))
        assert adapter.provider_name == "openrouter"


class TestSuccessfulCompletion:
    def test_first_attempt_success_returns_parsed_and_usage(self) -> None:
        content = json.dumps({"name": "cask-42", "count": 3})
        fake_client = _FakeClient([_FakeResponse(json_body=_chat_completion_payload(content))])
        adapter = OpenRouterAdapter(api_key="sk-test", client=fake_client)

        response = adapter.complete_structured(_make_request())

        assert isinstance(response.parsed, _Widget)
        assert response.parsed == _Widget(name="cask-42", count=3)
        assert response.prompt_tokens == 42
        assert response.completion_tokens == 7
        assert response.model == "openai/gpt-4o-mini"
        assert response.latency_ms >= 0
        assert response.raw_response["id"] == "chatcmpl-test"
        assert len(fake_client.calls) == 1

    def test_request_uses_bearer_auth_and_json_schema_response_format(self) -> None:
        content = json.dumps({"name": "x", "count": 1})
        fake_client = _FakeClient([_FakeResponse(json_body=_chat_completion_payload(content))])
        adapter = OpenRouterAdapter(api_key="sk-secret", client=fake_client)

        adapter.complete_structured(_make_request(model="anthropic/claude-3.5-haiku"))

        call = fake_client.calls[0]
        assert call["headers"]["Authorization"] == "Bearer sk-secret"
        assert call["json"]["model"] == "anthropic/claude-3.5-haiku"
        assert call["json"]["response_format"]["type"] == "json_schema"
        assert call["json"]["response_format"]["json_schema"]["name"] == "_Widget"
        assert call["json"]["response_format"]["json_schema"]["schema"] == _Widget.model_json_schema()
        assert call["json"]["max_tokens"] == 1024
        # The schema is also embedded in the prompt text as a defense-in-depth
        # fallback for models that don't honor response_format (module docstring).
        assert "_Widget" in call["json"]["messages"][0]["content"]

    def test_tolerates_markdown_fenced_json_content(self) -> None:
        fenced = "```json\n" + json.dumps({"name": "fenced", "count": 5}) + "\n```"
        fake_client = _FakeClient([_FakeResponse(json_body=_chat_completion_payload(fenced))])
        adapter = OpenRouterAdapter(api_key="sk-test", client=fake_client)

        response = adapter.complete_structured(_make_request())

        assert response.parsed == _Widget(name="fenced", count=5)


class TestRetryOnSchemaValidationFailure:
    def test_retries_exactly_once_then_succeeds(self) -> None:
        invalid = json.dumps({"name": "missing-count"})  # `count` required, missing
        valid = json.dumps({"name": "recovered", "count": 9})
        fake_client = _FakeClient(
            [
                _FakeResponse(json_body=_chat_completion_payload(invalid)),
                _FakeResponse(json_body=_chat_completion_payload(valid)),
            ]
        )
        adapter = OpenRouterAdapter(api_key="sk-test", client=fake_client)

        response = adapter.complete_structured(_make_request())

        assert response.parsed == _Widget(name="recovered", count=9)
        assert len(fake_client.calls) == 2
        # The retry's prompt must carry the correction note forward -- a
        # fresh, stateless call, never a continuation (development-rules.md:
        # "Each LLM call is isolated"), so the correction has to live in the
        # prompt text itself rather than any hidden conversation state.
        assert "CORRECTION REQUIRED" in fake_client.calls[1]["json"]["messages"][0]["content"]

    def test_raises_llm_schema_validation_error_after_exhausting_the_one_retry(self) -> None:
        invalid = json.dumps({"name": "still-missing-count"})
        fake_client = _FakeClient(
            [
                _FakeResponse(json_body=_chat_completion_payload(invalid)),
                _FakeResponse(json_body=_chat_completion_payload(invalid)),
            ]
        )
        adapter = OpenRouterAdapter(api_key="sk-test", client=fake_client)

        with pytest.raises(LLMSchemaValidationError):
            adapter.complete_structured(_make_request())

        assert len(fake_client.calls) == 2

    def test_malformed_json_content_is_also_retried_once(self) -> None:
        fake_client = _FakeClient(
            [
                _FakeResponse(json_body=_chat_completion_payload("not json at all")),
                _FakeResponse(json_body=_chat_completion_payload(json.dumps({"name": "ok", "count": 1}))),
            ]
        )
        adapter = OpenRouterAdapter(api_key="sk-test", client=fake_client)

        response = adapter.complete_structured(_make_request())

        assert response.parsed == _Widget(name="ok", count=1)
        assert len(fake_client.calls) == 2


class TestProviderErrors:
    def test_http_error_status_raises_llm_provider_error(self) -> None:
        fake_client = _FakeClient([_FakeResponse(status_code=401, text='{"error": "invalid api key"}')])
        adapter = OpenRouterAdapter(api_key="sk-bad", client=fake_client)

        with pytest.raises(LLMProviderError):
            adapter.complete_structured(_make_request())

    def test_unexpected_response_shape_raises_llm_provider_error(self) -> None:
        fake_client = _FakeClient([_FakeResponse(json_body={"unexpected": "shape"})])
        adapter = OpenRouterAdapter(api_key="sk-test", client=fake_client)

        with pytest.raises(LLMProviderError):
            adapter.complete_structured(_make_request())

    def test_provider_error_is_not_retried(self) -> None:
        """A transport/auth failure is a different failure class from a
        schema-validation failure (docs/contracts.md section 5's failure
        table lists them separately) -- it must propagate immediately, not
        consume the one schema-validation retry."""
        fake_client = _FakeClient([_FakeResponse(status_code=500, text="boom")])
        adapter = OpenRouterAdapter(api_key="sk-test", client=fake_client)

        with pytest.raises(LLMProviderError):
            adapter.complete_structured(_make_request())

        assert len(fake_client.calls) == 1
