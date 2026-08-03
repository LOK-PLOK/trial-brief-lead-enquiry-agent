"""`parse_enquiry`: LLM extraction of name, email, phone, country, budget
band, asset interest, urgency. See docs/architecture.md section 8.

The only LLM-backed tool. Its call is independent of both the planner's and
verifier's calls (own prompt module: agent/prompts/parse_enquiry_prompt.py).
"""

from __future__ import annotations

import time

from pydantic import BaseModel

from app.agent.prompts.parse_enquiry_prompt import (
    PARSE_ENQUIRY_SYSTEM_PROMPT,
    build_parse_enquiry_user_prompt,
)
from app.core.config import get_settings
from app.core.logging import get_logger, run_id_ctx
from app.llm.base import LLMProviderError, LLMSchemaValidationError, ModelAdapter, StructuredCompletionRequest
from app.schemas.extraction import ExtractedFields
from app.tools.base import Tool, ToolResult

logger = get_logger(__name__)


class ParseEnquiryArgs(BaseModel):
    enquiry_text: str


class ParseEnquiryTool(Tool):
    name = "parse_enquiry"
    description = "LLM extraction of name, email, phone, country, budget band, asset interest, urgency."
    args_schema = ParseEnquiryArgs
    result_schema = ExtractedFields

    def __init__(self, adapter: ModelAdapter) -> None:
        self._adapter = adapter

    def run(self, args: ParseEnquiryArgs) -> ToolResult:
        """`args` is already validated against `ParseEnquiryArgs` by
        `Tool.execute()` -- this only needs to do the actual extraction.

        An adapter failure (provider error, or schema validation still
        failing after the adapter's own one internal retry) is treated as an
        *expected* business outcome for an LLM-backed tool and reported as
        `ToolResult(success=False, error=...)` rather than raised, so the
        Executor logs and halts deterministically (docs/contracts.md
        section 2's "controlled tool failure" category) instead of treating
        an upstream provider hiccup as a tool bug. Only `LLMProviderError`/
        `LLMSchemaValidationError` are caught here -- a genuine bug in this
        method itself still propagates loudly, per `Tool.run()`'s own
        contract (docs/contracts.md section 4).
        """
        started = time.perf_counter()
        request = StructuredCompletionRequest(
            system_prompt=PARSE_ENQUIRY_SYSTEM_PROMPT,
            user_prompt=build_parse_enquiry_user_prompt(args.enquiry_text),
            response_schema=ExtractedFields,
            model=get_settings().resolved_model("extractor"),
            max_tokens=512,
            metadata={"run_id": run_id_ctx.get(), "stage": "parse_enquiry"},
        )
        try:
            response = self._adapter.complete_structured(request)
        except (LLMProviderError, LLMSchemaValidationError) as exc:
            logger.error(
                "parse_enquiry adapter call failed",
                extra={"event": "parse_enquiry_adapter_failed", "error": str(exc)},
            )
            return ToolResult(
                success=False,
                data=None,
                error=f"{type(exc).__name__}: {exc}",
                latency_ms=_elapsed_ms(started),
            )

        if not isinstance(response.parsed, ExtractedFields):
            # Defensive: the adapter contract guarantees `parsed` already
            # validates against `response_schema` -- but a boundary is never
            # trusted blindly, same principle as agent/planner.py and
            # agent/verifier.py's own defensive checks. A wrong type here is
            # a violated adapter contract, reported the same controlled way
            # as any other adapter failure rather than raised.
            error = f"adapter returned {type(response.parsed).__name__}, expected ExtractedFields"
            logger.error(
                "parse_enquiry adapter violated its own contract",
                extra={"event": "parse_enquiry_adapter_contract_violation", "error": error},
            )
            return ToolResult(success=False, data=None, error=error, latency_ms=_elapsed_ms(started))

        logger.info(
            "parse_enquiry llm call completed",
            extra={
                "event": "parse_enquiry_llm_call",
                "model": response.model,
                "system_prompt": PARSE_ENQUIRY_SYSTEM_PROMPT,
                "user_prompt": request.user_prompt,
                "response": response.parsed.model_dump(mode="json"),
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "latency_ms": response.latency_ms,
            },
        )
        return ToolResult(
            success=True,
            data=response.parsed.model_dump(mode="json"),
            error=None,
            latency_ms=_elapsed_ms(started),
        )


def _elapsed_ms(perf_start: float) -> float:
    return (time.perf_counter() - perf_start) * 1000
