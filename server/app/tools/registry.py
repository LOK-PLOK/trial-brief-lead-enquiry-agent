"""The tool registry: single source of truth for which tools exist and what
the Planner is told about them. See docs/architecture.md sections 6 and 8,
and docs/contracts.md sections 2 and 4.

Importing the four tool modules below is what triggers their automatic
registration via `Tool.__init_subclass__` (see tools/base.py) -- adding a
new tool means "write the module, add one import line here", never
"hand-edit a list of instances that has to stay in sync with the Planner's
manifest".
"""

from __future__ import annotations

import inspect
from typing import Any

from app.llm.base import ModelAdapter

# Imported for their registration side effect only -- see module docstring.
from app.tools import (  # noqa: F401
    lookup_jurisdiction_rule,
    parse_enquiry,
    score_lead,
    write_record,
)
from app.tools.base import Tool


class ToolRegistryError(Exception):
    """Raised when a registered tool class can't be instantiated -- e.g. it
    needs a dependency (currently only a `ModelAdapter`, for
    `parse_enquiry`) that wasn't supplied to `ToolRegistry(...)`."""


class ToolRegistry:
    """Builds one ready-to-use instance of every auto-registered `Tool`
    subclass, wiring in shared dependencies by introspecting each tool
    class's `__init__` signature -- so adding a tool that needs a new kind
    of dependency only means adding a parameter name here, never a
    per-tool-class branch.

    Instantiation is eager and fails fast: constructing `ToolRegistry()`
    without an `adapter` raises immediately if any registered tool needs
    one, rather than deferring the failure to first use. Callers that only
    need the Planner's manifest (no dependencies required) should use the
    `tool_manifest()` staticmethod instead of constructing an instance.
    """

    def __init__(self, *, adapter: ModelAdapter | None = None) -> None:
        self._adapter = adapter
        self._tools: dict[str, Tool] = {
            name: self._instantiate(tool_cls) for name, tool_cls in sorted(Tool.registered_classes().items())
        }

    def _instantiate(self, tool_cls: type[Tool]) -> Tool:
        params = inspect.signature(tool_cls.__init__).parameters
        kwargs: dict[str, Any] = {}
        if "adapter" in params:
            if self._adapter is None:
                raise ToolRegistryError(
                    f"{tool_cls.__name__} (tool {tool_cls.name!r}) requires a "
                    f"ModelAdapter; construct ToolRegistry(adapter=...) with one."
                )
            kwargs["adapter"] = self._adapter
        return tool_cls(**kwargs)

    def get_tool(self, name: str) -> Tool:
        """Look up a tool by name for the Executor's dispatch (enum-constrained,
        never by parsing free-form model text -- docs/architecture.md section 7).
        """
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name!r}") from exc

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __iter__(self):
        return iter(self._tools.values())

    @staticmethod
    def tool_manifest() -> list[dict[str, Any]]:
        """Manifest handed to the Planner. Built directly from registered
        *classes* (no instances or dependencies needed -- see
        `Tool.manifest_entry()`), sorted by name so the order is
        deterministic regardless of module import order.
        """
        return [tool_cls.manifest_entry() for _, tool_cls in sorted(Tool.registered_classes().items())]
