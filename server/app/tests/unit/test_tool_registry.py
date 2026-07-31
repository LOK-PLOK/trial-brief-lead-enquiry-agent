"""Unit tests for `ToolRegistry` (server/app/tools/registry.py): automatic
registration of the four real tools, deterministic manifest generation, and
dependency wiring. See docs/architecture.md sections 6/8 and
docs/contracts.md sections 2/4.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.llm.base import ModelAdapter, StructuredCompletionRequest, StructuredCompletionResponse
from app.tools.lookup_jurisdiction_rule import LookupJurisdictionRuleTool
from app.tools.parse_enquiry import ParseEnquiryTool
from app.tools.registry import ToolRegistry, ToolRegistryError
from app.tools.score_lead import ScoreLeadTool
from app.tools.write_record import WriteRecordTool

EXPECTED_TOOL_NAMES = {"parse_enquiry", "lookup_jurisdiction_rule", "score_lead", "write_record"}


@dataclass
class _FakeAdapter(ModelAdapter):
    """Minimal `ModelAdapter` stand-in -- just enough to satisfy
    `ParseEnquiryTool.__init__`'s type; never actually called in these
    registry-focused tests."""

    provider_name: str = "fake"

    def complete_structured(self, request: StructuredCompletionRequest) -> StructuredCompletionResponse:
        raise NotImplementedError


def test_registry_registers_all_four_real_tools() -> None:
    registry = ToolRegistry(adapter=_FakeAdapter())
    assert len(registry) == 4
    assert {tool.name for tool in registry} == EXPECTED_TOOL_NAMES


def test_registry_without_adapter_raises_for_parse_enquiry() -> None:
    with pytest.raises(ToolRegistryError, match="ParseEnquiryTool"):
        ToolRegistry()


def test_get_tool_returns_the_correct_instance_types() -> None:
    registry = ToolRegistry(adapter=_FakeAdapter())
    assert isinstance(registry.get_tool("parse_enquiry"), ParseEnquiryTool)
    assert isinstance(registry.get_tool("lookup_jurisdiction_rule"), LookupJurisdictionRuleTool)
    assert isinstance(registry.get_tool("score_lead"), ScoreLeadTool)
    assert isinstance(registry.get_tool("write_record"), WriteRecordTool)


def test_get_tool_unknown_name_raises_key_error() -> None:
    registry = ToolRegistry(adapter=_FakeAdapter())
    with pytest.raises(KeyError, match="Unknown tool"):
        registry.get_tool("not_a_real_tool")


def test_contains_and_len() -> None:
    registry = ToolRegistry(adapter=_FakeAdapter())
    assert "score_lead" in registry
    assert "not_a_real_tool" not in registry
    assert len(registry) == 4


def test_tool_manifest_is_available_without_any_dependencies() -> None:
    """The manifest must not require an adapter -- it's derived from
    registered classes, not instances (docs/contracts.md section 4)."""
    manifest = ToolRegistry.tool_manifest()
    assert {entry["name"] for entry in manifest} == EXPECTED_TOOL_NAMES


def test_tool_manifest_is_deterministically_ordered() -> None:
    first = [entry["name"] for entry in ToolRegistry.tool_manifest()]
    second = [entry["name"] for entry in ToolRegistry.tool_manifest()]
    assert first == second
    assert first == sorted(first)  # alphabetical by name, not import order


def test_tool_manifest_entries_have_name_description_and_json_schema() -> None:
    manifest = ToolRegistry.tool_manifest()
    for entry in manifest:
        assert set(entry.keys()) == {"name", "description", "args_schema"}
        assert isinstance(entry["name"], str) and entry["name"]
        assert isinstance(entry["description"], str) and entry["description"]
        assert isinstance(entry["args_schema"], dict)
        assert "properties" in entry["args_schema"] or "type" in entry["args_schema"]


def test_tool_manifest_matches_instance_based_registry() -> None:
    """The class-level manifest and an instantiated registry must agree on
    which tools exist, so the Planner's view can never drift from what the
    Executor can actually dispatch."""
    registry = ToolRegistry(adapter=_FakeAdapter())
    manifest_names = {entry["name"] for entry in registry.tool_manifest()}
    instance_names = {tool.name for tool in registry}
    assert manifest_names == instance_names
