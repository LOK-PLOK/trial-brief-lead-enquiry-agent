"""The tool registry: single source of truth for which tools exist and what
the Planner is told about them. See docs/architecture.md sections 6 and 8.
"""

from __future__ import annotations

from app.tools.base import Tool

# TODO(tools/registry): populate once the four tool classes are implemented,
# e.g.:
#   from app.tools.parse_enquiry import ParseEnquiryTool
#   from app.tools.lookup_jurisdiction_rule import LookupJurisdictionRuleTool
#   from app.tools.score_lead import ScoreLeadTool
#   from app.tools.write_record import WriteRecordTool
#   _TOOLS: list[Tool] = [ParseEnquiryTool(), LookupJurisdictionRuleTool(),
#                          ScoreLeadTool(), WriteRecordTool()]
_TOOLS: list[Tool] = []

_REGISTRY: dict[str, Tool] = {tool.name: tool for tool in _TOOLS}


def get_tool(name: str) -> Tool:
    """Look up a tool by name for the Executor's dispatch (enum-constrained,
    never by parsing free-form model text — docs/architecture.md section 7).
    """
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"Unknown tool: {name!r}") from exc


def tool_manifest() -> list[dict]:
    """Manifest handed to the Planner. Derived from the registry so it can
    never drift from what the Executor can actually run."""
    return [tool.manifest_entry() for tool in _TOOLS]
