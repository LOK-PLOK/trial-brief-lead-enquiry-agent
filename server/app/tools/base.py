"""Common `Tool` interface. See docs/architecture.md section 8.

Every tool is independently unit-testable against this interface: the three
deterministic tools with plain fixed-input tests, `parse_enquiry` with a
mocked `ModelAdapter` returning canned structured responses.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


@dataclass
class ToolResult:
    success: bool
    data: dict[str, Any] | None
    error: str | None
    latency_ms: float


class Tool(ABC):
    name: str
    description: str
    args_schema: type[BaseModel]
    result_schema: type[BaseModel]

    @abstractmethod
    def run(self, args: BaseModel) -> ToolResult:
        """Execute the tool. Must not raise for expected/business failures
        (e.g. duplicate lead) — return `ToolResult(success=False, error=...)`
        instead, so the Executor can log and act on it deterministically.
        Unexpected exceptions should still propagate so they surface loudly
        rather than being silently swallowed.
        """
        raise NotImplementedError

    def manifest_entry(self) -> dict[str, Any]:
        """JSON-serializable description exposed to the Planner as part of
        the tool manifest (name, description, args JSON schema). Derived
        directly from `args_schema` so the planner's view of this tool can
        never drift from what `run()` actually accepts.
        """
        return {
            "name": self.name,
            "description": self.description,
            "args_schema": self.args_schema.model_json_schema(),
        }
