"""Common `Tool` interface. See docs/architecture.md section 8 and
docs/contracts.md section 4.

Every tool exposes `name`, `description`, `args_schema`, `result_schema`, and
the abstract `run()` hook that business logic implements. `execute()` is the
single sanctioned public entry point (used by the Executor): it validates
raw arguments against `args_schema`, delegates to `run()`, then validates a
successful result's `data` against `result_schema` -- validation on both
sides of the boundary, so `run()` implementations never need to re-validate
what they were given, and can never silently return a shape that drifts from
what they advertise.

Subclassing `Tool` automatically registers the class (see
`__init_subclass__`) so `tools/registry.py` never needs a hand-maintained
list of instances -- dropping a new `SomeTool(Tool)` class into a module
that gets imported (see registry.py's imports) is the only step required
for it to appear in the Planner's manifest and be dispatchable by name.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError


@dataclass
class ToolResult:
    success: bool
    data: dict[str, Any] | None
    error: str | None
    latency_ms: float


class ToolValidationError(Exception):
    """Raised by `Tool.execute()` when raw input fails validation against
    `args_schema`, or a successful `run()` result's `data` fails validation
    against `result_schema`.

    Distinct from a tool's own business-logic failure
    (`ToolResult(success=False, error=...)`, returned by `run()`, never
    raised): this exception means the *call itself* was malformed --
    typically a planner-hallucinated argument or a buggy `run()`
    implementation -- not a legitimate business outcome. Keeping the two
    separate lets the Executor tell "malformed call" apart from "tool ran
    and failed" (docs/contracts.md section 4).
    """

    def __init__(self, tool_name: str, errors: list[dict[str, Any]]) -> None:
        self.tool_name = tool_name
        self.errors = errors
        super().__init__(f"{tool_name}: validation failed: {errors}")


class Tool(ABC):
    """Base class for every tool. Concrete subclasses must set `name`,
    `description`, `args_schema`, and `result_schema` as class attributes,
    and implement `run()`.
    """

    name: str
    description: str
    args_schema: type[BaseModel]
    result_schema: type[BaseModel]

    _registry: ClassVar[dict[str, type[Tool]]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

        if cls.run is Tool.run:
            # Hasn't implemented the abstract `run()` yet (e.g. an
            # intermediate subclass) -- not a usable tool, don't register.
            return

        for attr in ("name", "description", "args_schema", "result_schema"):
            if getattr(cls, attr, None) is None:
                raise TypeError(f"{cls.__name__} must define a non-None `{attr}`.")

        if not isinstance(cls.name, str) or not cls.name.strip():
            raise TypeError(f"{cls.__name__}.name must be a non-empty string.")

        for attr in ("args_schema", "result_schema"):
            schema = getattr(cls, attr)
            if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
                raise TypeError(f"{cls.__name__}.{attr} must be a pydantic BaseModel subclass.")

        existing = Tool._registry.get(cls.name)
        if existing is not None and existing is not cls:
            raise ValueError(
                f"Duplicate tool name {cls.name!r}: already registered by "
                f"{existing.__module__}.{existing.__qualname__}, cannot also "
                f"register it for {cls.__module__}.{cls.__qualname__}."
            )
        Tool._registry[cls.name] = cls

    @classmethod
    def registered_classes(cls) -> dict[str, type[Tool]]:
        """Every auto-registered concrete `Tool` subclass, keyed by `name`.

        Returns a shallow copy -- callers get a snapshot, not a handle onto
        the live registry.
        """
        return dict(Tool._registry)

    @abstractmethod
    def run(self, args: BaseModel) -> ToolResult:
        """Execute the tool against already-validated `args` (validated by
        `execute()` before this is ever called -- see below).

        Must not raise for expected/business failures (e.g. a duplicate
        lead) -- return `ToolResult(success=False, error=...)` instead, so
        the Executor can log and act on it deterministically. Unexpected
        exceptions should still propagate so they surface loudly rather
        than being silently swallowed.
        """
        raise NotImplementedError

    def validate_args(self, raw_args: dict[str, Any] | BaseModel) -> BaseModel:
        """Validate raw (e.g. planner-supplied) args against `args_schema`."""
        if isinstance(raw_args, self.args_schema):
            return raw_args
        try:
            return self.args_schema.model_validate(raw_args)
        except ValidationError as exc:
            raise ToolValidationError(self.name, exc.errors()) from exc

    def validate_result(self, data: dict[str, Any]) -> BaseModel:
        """Validate a successful `run()` result's `data` against
        `result_schema`."""
        try:
            return self.result_schema.model_validate(data)
        except ValidationError as exc:
            raise ToolValidationError(self.name, exc.errors()) from exc

    def execute(self, raw_args: dict[str, Any] | BaseModel) -> ToolResult:
        """The single sanctioned way to invoke a tool (docs/contracts.md
        section 4): validate input -> run -> validate output.

        Raises `ToolValidationError` if `raw_args` don't match
        `args_schema`, or if a successful result's `data` doesn't match
        `result_schema`. `run()` is never called with unvalidated input.
        """
        validated_args = self.validate_args(raw_args)
        result = self.run(validated_args)
        if result.success and result.data is not None:
            self.validate_result(result.data)
        return result

    @classmethod
    def manifest_entry(cls) -> dict[str, Any]:
        """JSON-serializable description exposed to the Planner as part of
        the tool manifest (name, description, args JSON schema).

        A classmethod -- needs no instance or dependencies -- so the
        manifest can be built purely from registered classes (see
        `ToolRegistry.tool_manifest()`), derived directly from
        `args_schema` so the Planner's view of this tool can never drift
        from what `run()` actually accepts.
        """
        return {
            "name": cls.name,
            "description": cls.description,
            "args_schema": cls.args_schema.model_json_schema(),
        }
