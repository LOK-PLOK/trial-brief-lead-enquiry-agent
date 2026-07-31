"""Unit tests for the `Tool` base interface (server/app/tools/base.py):
auto-registration, `execute()`'s validate -> run -> validate flow, and the
manifest derived from `args_schema`. See docs/contracts.md section 4.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.tools.base import Tool, ToolResult, ToolValidationError


class _DummyArgs(BaseModel):
    value: int


class _DummyResult(BaseModel):
    doubled: int


@pytest.fixture
def dummy_tool_cls():
    """A minimal, fully valid `Tool` subclass, registered for the duration
    of the test and removed afterwards so tests never leak state into
    `Tool._registry` (which is shared, class-level state)."""

    class DummyTool(Tool):
        name = "test_dummy_tool"
        description = "A dummy tool for base-class unit tests."
        args_schema = _DummyArgs
        result_schema = _DummyResult

        def run(self, args: _DummyArgs) -> ToolResult:
            return ToolResult(success=True, data={"doubled": args.value * 2}, error=None, latency_ms=0.1)

    yield DummyTool
    Tool._registry.pop(DummyTool.name, None)


@pytest.fixture
def failing_dummy_tool_cls():
    """A dummy tool whose `run()` returns a failure result (so
    `execute()` must skip result validation)."""

    class FailingDummyTool(Tool):
        name = "test_failing_dummy_tool"
        description = "A dummy tool that always reports a business failure."
        args_schema = _DummyArgs
        result_schema = _DummyResult

        def run(self, args: _DummyArgs) -> ToolResult:
            return ToolResult(success=False, data=None, error="business failure", latency_ms=0.1)

    yield FailingDummyTool
    Tool._registry.pop(FailingDummyTool.name, None)


@pytest.fixture
def bad_result_dummy_tool_cls():
    """A dummy tool whose `run()` returns success but with data that does
    not match `result_schema` -- `execute()` must catch this."""

    class BadResultDummyTool(Tool):
        name = "test_bad_result_dummy_tool"
        description = "A dummy tool that returns a malformed successful result."
        args_schema = _DummyArgs
        result_schema = _DummyResult

        def run(self, args: _DummyArgs) -> ToolResult:
            return ToolResult(success=True, data={"wrong_field": 1}, error=None, latency_ms=0.1)

    yield BadResultDummyTool
    Tool._registry.pop(BadResultDummyTool.name, None)


def test_execute_validates_args_and_returns_run_result(dummy_tool_cls) -> None:
    tool = dummy_tool_cls()
    result = tool.execute({"value": 21})
    assert result.success is True
    assert result.data == {"doubled": 42}


def test_execute_accepts_already_validated_model(dummy_tool_cls) -> None:
    tool = dummy_tool_cls()
    result = tool.execute(_DummyArgs(value=5))
    assert result.data == {"doubled": 10}


def test_execute_raises_tool_validation_error_on_invalid_args(dummy_tool_cls) -> None:
    tool = dummy_tool_cls()
    # `value` is required and must be an int -- both missing and wrong-typed.
    with pytest.raises(ToolValidationError) as exc_info:
        tool.execute({"value": "not-an-int"})
    assert exc_info.value.tool_name == "test_dummy_tool"
    assert exc_info.value.errors  # structured pydantic error list, not swallowed


def test_execute_raises_tool_validation_error_on_missing_required_arg(dummy_tool_cls) -> None:
    tool = dummy_tool_cls()
    with pytest.raises(ToolValidationError):
        tool.execute({})


def test_execute_skips_result_validation_on_business_failure(failing_dummy_tool_cls) -> None:
    tool = failing_dummy_tool_cls()
    # `data=None` on a failure result would fail `_DummyResult` validation if
    # checked -- execute() must not attempt to validate it in this case.
    result = tool.execute({"value": 1})
    assert result.success is False
    assert result.error == "business failure"


def test_execute_raises_tool_validation_error_on_malformed_successful_result(
    bad_result_dummy_tool_cls,
) -> None:
    tool = bad_result_dummy_tool_cls()
    with pytest.raises(ToolValidationError) as exc_info:
        tool.execute({"value": 1})
    assert exc_info.value.tool_name == "test_bad_result_dummy_tool"


def test_manifest_entry_is_derived_from_args_schema(dummy_tool_cls) -> None:
    entry = dummy_tool_cls.manifest_entry()
    assert entry == {
        "name": "test_dummy_tool",
        "description": "A dummy tool for base-class unit tests.",
        "args_schema": _DummyArgs.model_json_schema(),
    }


def test_subclassing_auto_registers_the_tool(dummy_tool_cls) -> None:
    assert Tool.registered_classes()["test_dummy_tool"] is dummy_tool_cls


def test_registered_classes_returns_a_snapshot_not_a_live_reference(dummy_tool_cls) -> None:
    snapshot = Tool.registered_classes()
    snapshot["some_other_name"] = dummy_tool_cls
    assert "some_other_name" not in Tool.registered_classes()


def test_duplicate_tool_name_raises_value_error(dummy_tool_cls) -> None:
    with pytest.raises(ValueError, match="Duplicate tool name"):

        class AnotherDummyTool(Tool):
            name = "test_dummy_tool"  # collides with dummy_tool_cls
            description = "A second tool that reuses an existing name."
            args_schema = _DummyArgs
            result_schema = _DummyResult

            def run(self, args: _DummyArgs) -> ToolResult:
                raise NotImplementedError


def test_missing_required_attribute_raises_type_error() -> None:
    with pytest.raises(TypeError, match="must define a non-None"):

        class IncompleteTool(Tool):
            name = "test_incomplete_tool"
            description = None  # not set
            args_schema = _DummyArgs
            result_schema = _DummyResult

            def run(self, args: _DummyArgs) -> ToolResult:
                raise NotImplementedError

    # Clean up in case the metaclass partially registered it before raising.
    Tool._registry.pop("test_incomplete_tool", None)


def test_non_basemodel_schema_raises_type_error() -> None:
    with pytest.raises(TypeError, match="must be a pydantic BaseModel subclass"):

        class BadSchemaTool(Tool):
            name = "test_bad_schema_tool"
            description = "Uses a plain dict instead of a BaseModel for args_schema."
            args_schema = dict  # not a BaseModel subclass
            result_schema = _DummyResult

            def run(self, args: _DummyArgs) -> ToolResult:
                raise NotImplementedError

    Tool._registry.pop("test_bad_schema_tool", None)


def test_abstract_intermediate_subclass_is_not_registered() -> None:
    class AbstractIntermediateTool(Tool):
        """Doesn't implement `run()` -- should be skipped, not registered,
        and should not even go through attribute validation."""

    assert "AbstractIntermediateTool" not in {cls.__name__ for cls in Tool.registered_classes().values()}
