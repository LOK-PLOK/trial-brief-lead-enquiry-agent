"""`parse_enquiry`: LLM extraction of name, email, phone, country, budget
band, asset interest, urgency. See docs/architecture.md section 8.

The only LLM-backed tool. Its call is independent of both the planner's and
verifier's calls (own prompt module: agent/prompts/parse_enquiry_prompt.py).
"""

from __future__ import annotations

from pydantic import BaseModel

from app.llm.base import ModelAdapter
from app.schemas.extraction import ExtractedFields
from app.tools.base import Tool, ToolResult


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

        TODO(tools/parse_enquiry): call self._adapter.complete_structured(...)
        with response_schema=ExtractedFields, using
        prompts/parse_enquiry_prompt.py. Wrap in try/except so an adapter
        failure becomes ToolResult(success=False, error=...) rather than an
        uncaught exception."""
        raise NotImplementedError
