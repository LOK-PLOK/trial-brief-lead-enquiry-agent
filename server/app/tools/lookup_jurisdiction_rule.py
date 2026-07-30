"""`lookup_jurisdiction_rule`: deterministic lookup against a small local
rules file, returns the handling rule for that country. See
docs/architecture.md section 8.

Deterministic — no LLM call. Unit-testable with fixed inputs.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.tools.base import Tool, ToolResult


class LookupJurisdictionRuleArgs(BaseModel):
    country: str


class JurisdictionRule(BaseModel):
    country: str
    requires_disclaimer: bool
    restricted: bool
    handling_note: str


class LookupJurisdictionRuleTool(Tool):
    name = "lookup_jurisdiction_rule"
    description = "Deterministic lookup against a local rules file, returns the handling rule for a country."
    args_schema = LookupJurisdictionRuleArgs
    result_schema = JurisdictionRule

    def __init__(self, rules_path: str = "app/data/jurisdiction_rules.json") -> None:
        self._rules_path = rules_path

    def run(self, args: LookupJurisdictionRuleArgs) -> ToolResult:
        """`args` is already validated against `LookupJurisdictionRuleArgs`
        by `Tool.execute()` -- this only needs to do the actual lookup.

        TODO(tools/lookup_jurisdiction_rule): load app/data/jurisdiction_rules.json
        (cache in-process; it's small and static), look up `args.country`
        (case/whitespace-normalized), and return a fallback rule (not a
        fabricated one) with a logged warning for unknown countries."""
        raise NotImplementedError
