"""OpenRouter `ModelAdapter` implementation. See docs/contracts.md section 5
and docs/architecture.md section 6.

OpenRouter (https://openrouter.ai) exposes a single OpenAI-compatible
`/chat/completions` endpoint in front of many different backend models,
selected per-request via `model` (an OpenRouter model id, e.g.
`"openai/gpt-4o-mini"` or `"anthropic/claude-3.5-haiku"` — see `MODEL_NAME`).
No provider SDK dependency is added for this: the API is plain HTTP, so this
uses the already-pinned `httpx` (the same reasoning `requirements.txt` already
documents for Ollama: "it's called over plain HTTP").

Structured outputs (docs/contracts.md section 5's `parsed` guarantee): this
adapter requests OpenRouter's native `response_format: {"type": "json_schema",
...}` mode (best-effort — not every model/provider OpenRouter proxies
supports it; unsupported params are dropped rather than erroring by default,
per OpenRouter's own docs) *and* embeds the same JSON Schema directly in the
system prompt as a textual instruction, then validates the response against
`request.response_schema` with Pydantic regardless of what the provider
claims to have honored. This is deliberately the conservative
"instruct-then-validate-then-retry" strategy `llm/base.py` already documents
for "any local model without guaranteed JSON-schema support" — because
`MODEL_NAME` is operator-configurable to *any* OpenRouter model id, this
adapter cannot assume native schema support the way a single-provider OpenAI
adapter could, so it never relies on `response_format` alone to guarantee
validity.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from pydantic import ValidationError

from app.core.logging import get_logger
from app.llm.base import (
    LLMProviderError,
    LLMSchemaValidationError,
    ModelAdapter,
    StructuredCompletionRequest,
    StructuredCompletionResponse,
)

logger = get_logger(__name__)

_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_TIMEOUT_SECONDS = 60.0

# One initial attempt + exactly one retry on schema-validation failure, per
# docs/contracts.md section 5 ("the adapter retries internally at most once
# on schema-validation failure") and this task's explicit requirement
# ("retry once only if schema validation fails").
_MAX_ATTEMPTS = 2


class OpenRouterAdapter(ModelAdapter):
    """Provider-agnostic `ModelAdapter` backed by OpenRouter's chat
    completions API. Construct via `llm/factory.py::build_adapter()` — never
    imported directly outside that module (docs/contracts.md section 5:
    "`llm/factory.py` is the *only* place in the codebase permitted to
    import a concrete adapter class")."""

    provider_name = "openrouter"

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise LLMProviderError(
                "OpenRouterAdapter requires an API key -- set OPENROUTER_API_KEY "
                "(get one at https://openrouter.ai/keys)."
            )
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        # Injectable for tests (avoids any real network call); a single
        # long-lived client otherwise, reused across every call this process
        # makes (the adapter itself is a process-wide `lru_cache`d singleton
        # -- see llm/factory.py::get_adapter()).
        self._client = client or httpx.Client(timeout=timeout)

    def complete_structured(self, request: StructuredCompletionRequest) -> StructuredCompletionResponse:
        schema_name = request.response_schema.__name__
        schema_json = request.response_schema.model_json_schema()
        correction_note: str | None = None
        last_error: Exception | None = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            system_prompt = _augment_system_prompt(
                request.system_prompt, schema_name, schema_json, correction_note
            )
            payload: dict[str, Any] = {
                "model": request.model,
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": request.user_prompt},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "strict": False,
                        "schema": schema_json,
                    },
                },
            }
            if request.tools:
                payload["tools"] = request.tools

            started = time.perf_counter()
            raw_response = self._post_chat_completion(payload)
            latency_ms = (time.perf_counter() - started) * 1000

            content = _extract_content(raw_response)
            usage = raw_response.get("usage") or {}
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
            response_model = raw_response.get("model") or request.model

            try:
                parsed = request.response_schema.model_validate(_loads_json_object(content))
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                logger.warning(
                    "openrouter response failed schema validation",
                    extra={
                        "event": "openrouter_schema_validation_failed",
                        "attempt": attempt,
                        "schema": schema_name,
                        "model": request.model,
                        "error": str(exc),
                    },
                )
                if attempt < _MAX_ATTEMPTS:
                    correction_note = str(exc)
                    continue
                raise LLMSchemaValidationError(
                    f"{schema_name}: OpenRouter response did not validate after "
                    f"{_MAX_ATTEMPTS} attempt(s): {exc}"
                ) from exc

            logger.info(
                "openrouter completion succeeded",
                extra={
                    "event": "openrouter_completion",
                    "attempt": attempt,
                    "schema": schema_name,
                    "model": response_model,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "latency_ms": latency_ms,
                },
            )
            return StructuredCompletionResponse(
                parsed=parsed,
                raw_response=raw_response,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
                model=response_model,
            )

        # Unreachable in practice: the loop above always either returns or
        # raises on its final attempt; kept only to satisfy static analysis.
        raise LLMSchemaValidationError(f"{schema_name}: no response obtained.") from last_error

    def _post_chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            # Optional per OpenRouter's docs (attribution for their public
            # rankings) -- included since they're free and harmless, not
            # required for the API to function.
            "HTTP-Referer": "https://github.com/whisky-cask-club/lead-enquiry-agent",
            "X-Title": "Lead Enquiry Agent",
        }
        try:
            response = self._client.post(f"{self._base_url}/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LLMProviderError(
                f"OpenRouter request failed with {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"OpenRouter request failed: {exc}") from exc

        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise LLMProviderError(f"OpenRouter returned a non-JSON response: {response.text!r}") from exc


def _extract_content(raw_response: dict[str, Any]) -> str:
    try:
        return raw_response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMProviderError(f"Unexpected OpenRouter response shape: {raw_response!r}") from exc


def _loads_json_object(content: str) -> dict[str, Any]:
    """Parse `content` as a JSON object, tolerating models that wrap it in a
    markdown code fence despite being told not to (a real, common failure
    mode for `json_schema`-unsupported models falling back to plain text)."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[len("json") :]
        text = text.strip()
    return json.loads(text)


def _augment_system_prompt(
    base_system_prompt: str,
    schema_name: str,
    schema_json: dict[str, Any],
    correction_note: str | None,
) -> str:
    """Append the target JSON Schema (and, on a retry, the prior validation
    error) to `base_system_prompt` -- defense in depth alongside the
    `response_format` request parameter, since OpenRouter does not
    guarantee every backend model honors that parameter (module docstring).
    Never mutates the caller's prompt text; every call builds a fresh string
    (docs/development-rules.md: "Each LLM call is isolated")."""
    sections = [
        base_system_prompt,
        "",
        f"Respond with ONLY a single JSON object matching the `{schema_name}` JSON Schema below. "
        "No prose, no markdown code fences, no explanation outside the object's own fields.",
        json.dumps(schema_json, indent=2, sort_keys=True),
    ]
    if correction_note:
        sections.extend(
            [
                "",
                "CORRECTION REQUIRED -- your previous response did not validate against the schema above:",
                correction_note,
            ]
        )
    return "\n".join(sections)
