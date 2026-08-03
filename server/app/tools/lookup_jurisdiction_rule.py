"""`lookup_jurisdiction_rule`: deterministic lookup against a small local
rules file, returns the handling rule for that country. See
docs/architecture.md section 8.

Deterministic — no LLM call. Unit-testable with fixed inputs.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.core.logging import get_logger
from app.tools.base import Tool, ToolResult

logger = get_logger(__name__)

# In-process cache of the parsed rules file (small and static) -- loaded once
# per process rather than re-read from disk on every call. Keyed by the
# resolved path so a test pointing the tool at a different `rules_path`
# never sees another test's cached content.
_RULES_CACHE: dict[str, dict[str, Any]] = {}
_RULES_CACHE_LOCK = threading.Lock()


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

        Never raises (this task's explicit requirement): a genuinely missing
        `country` (blank/whitespace-only) is a *controlled* failure
        (`ToolResult(success=False, error=...)`), matching
        docs/contracts.md section 2's "controlled tool failure" category --
        distinct from an unrecognized-but-present country name, which falls
        back to the rules file's own `default` entry with a logged warning
        (this tool's original documented behavior, preserved unchanged) --
        and distinct again from the rules file itself being missing/corrupt,
        which is also reported as a controlled failure rather than raised.
        """
        started = time.perf_counter()
        country_input = args.country.strip()

        if not country_input:
            return ToolResult(
                success=False,
                data=None,
                error="country is required and was empty.",
                latency_ms=_elapsed_ms(started),
            )

        try:
            rules_document = _load_rules(self._rules_path)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error(
                "could not load jurisdiction rules file",
                extra={"event": "jurisdiction_rules_load_failed", "rules_path": self._rules_path},
            )
            return ToolResult(
                success=False,
                data=None,
                error=f"Could not load jurisdiction rules from {self._rules_path!r}: {exc}",
                latency_ms=_elapsed_ms(started),
            )

        normalized = country_input.casefold()
        matched_name = country_input
        entry = None
        for name, rule in rules_document.get("rules", {}).items():
            if name.strip().casefold() == normalized:
                matched_name = name
                entry = rule
                break

        if entry is None:
            entry = rules_document.get("default")
            if entry is None:
                error = f"No jurisdiction rule for country {country_input!r} and no default rule configured."
                return ToolResult(success=False, data=None, error=error, latency_ms=_elapsed_ms(started))
            logger.warning(
                "no explicit jurisdiction rule for country, falling back to default",
                extra={"event": "jurisdiction_rule_fallback_to_default", "country": country_input},
            )

        rule = JurisdictionRule(
            country=matched_name,
            requires_disclaimer=bool(entry["requires_disclaimer"]),
            restricted=bool(entry["restricted"]),
            handling_note=str(entry["handling_note"]),
        )
        return ToolResult(
            success=True, data=rule.model_dump(mode="json"), error=None, latency_ms=_elapsed_ms(started)
        )


def _load_rules(rules_path: str) -> dict[str, Any]:
    """Load + parse the jurisdiction rules JSON file, cached in-process per
    `rules_path`. `rules_path` is resolved relative to the `server/`
    directory (this tool's default, `"app/data/jurisdiction_rules.json"`,
    already assumes that) when it isn't already absolute -- consistent with
    how the app is run (`uvicorn app.main:app` from `server/`) and tested
    (pytest from `server/`)."""
    with _RULES_CACHE_LOCK:
        if rules_path in _RULES_CACHE:
            return _RULES_CACHE[rules_path]

    path = Path(rules_path)
    if not path.is_absolute():
        # `server/app/tools/` -> `server/` is two parents up from this file;
        # anchor there rather than relying on the process's cwd.
        path = Path(__file__).resolve().parents[2] / rules_path

    with path.open(encoding="utf-8") as handle:
        document = json.load(handle)

    with _RULES_CACHE_LOCK:
        _RULES_CACHE[rules_path] = document
    return document


def _elapsed_ms(perf_start: float) -> float:
    return (time.perf_counter() - perf_start) * 1000
