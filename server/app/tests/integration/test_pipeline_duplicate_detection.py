"""Duplicate-detection test at the full-pipeline level: does the system, end
to end via `Pipeline.run()`, correctly reject a second submission whose
extracted email/phone normalize to the same dedupe hash as an
already-accepted lead (docs/architecture.md section 8 /
docs/Trial_Brief_Paul_Detablan.md section 3.2's `write_record` requirement)?

This is a deliberate, standalone scenario (submit the *same* lead twice on
purpose), independent from evaluation/harness.py's 45-run sweep in every
sense:

- Lives under server/app/tests/ (pytest, run via server/pyproject.toml), not
  under evaluation/tests/.
- Never imports anything from `evaluation.*`.
- Never creates a `HarnessBatch` or passes a `harness_batch_id` to
  `Pipeline.run()` -- these calls are indistinguishable from a live API
  request as far as the Orchestrator is concerned.
- Does not participate in `evaluation.metrics.compute_harness_metrics()` in
  any way; its assertions are made directly against the two `RunResult`s.

Complements (rather than duplicates) the existing DB-layer coverage in
tests/integration/test_duplicate_detection.py. This module tests that the
pipeline verifies first, then runs `write_record` only after Verifier pass,
and that a second identical submission fails at that post-verify write
without inserting a second lead.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.agent.orchestrator import Pipeline
from app.core.config import Settings
from app.db import repository
from app.llm.base import ModelAdapter, StructuredCompletionRequest, StructuredCompletionResponse
from app.schemas.plan import Plan, PlanStep, ToolName
from app.schemas.verifier import VerifierDecision
from app.services.dedupe import compute_dedupe_hash
from app.tools.base import Tool, ToolResult

ENQUIRY_TEXT = "Hi, I'm Jane Doe (jane@example.com, +65 5551234), interested in whisky casks."

_CANONICAL_TOOL_NAMES = {tool.value for tool in ToolName}


class _PermissiveArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


class _PermissiveResult(BaseModel):
    model_config = ConfigDict(extra="allow")


def _mock_tool(tool_name: str, run_impl) -> type[Tool]:
    return type(
        f"Mock_{tool_name}",
        (Tool,),
        {
            "name": tool_name,
            "description": f"mock {tool_name} for duplicate-detection tests",
            "args_schema": _PermissiveArgs,
            "result_schema": _PermissiveResult,
            "run": run_impl,
        },
    )


def _register_dedupe_aware_mock_tools(db_session, contacts: list[dict] | None = None) -> None:
    """Registers the four canonical tools, with `write_record` performing a
    *real* dedupe check against `db_session` -- the exact
    check-then-insert pattern docs/contracts.md section 6 mandates for a
    real implementation (`find_lead_by_dedupe_hash` first, `create_lead`
    only if absent).

    `parse_enquiry`'s mock returns each of `contacts` in call order
    (mirroring `_FakeAdapter`'s own `contacts`-by-call-order pattern below)
    -- needed because agent/executor.py resolves `score_lead`'s/
    `write_record`'s chained `extracted`/`jurisdiction_rule`/`score` args
    from each upstream tool's *actual* result (see
    agent/executor.py::_resolve_args), not from whatever a downstream step's
    own Plan args said directly. Defaults to always returning `_JANE`, which
    is all the first two tests below need (a single, repeated contact)."""
    contact_sequence = list(contacts) if contacts is not None else [_JANE]
    call_index = {"value": 0}

    def _parse_enquiry_run(self, args: _PermissiveArgs) -> ToolResult:
        i = min(call_index["value"], len(contact_sequence) - 1)
        call_index["value"] += 1
        return ToolResult(success=True, data=dict(contact_sequence[i]), error=None, latency_ms=1.0)

    _mock_tool("parse_enquiry", _parse_enquiry_run)
    _mock_tool(
        "lookup_jurisdiction_rule",
        lambda self, args: ToolResult(
            success=True,
            data={
                "country": "Singapore",
                "requires_disclaimer": False,
                "restricted": False,
                "handling_note": "standard",
            },
            error=None,
            latency_ms=1.0,
        ),
    )
    _mock_tool(
        "score_lead",
        lambda self, args: ToolResult(
            success=True, data={"score": 80, "breakdown": {"budget": 80}}, error=None, latency_ms=1.0
        ),
    )

    def _write_record_run(self, args: _PermissiveArgs) -> ToolResult:
        extracted = args.extracted or {}
        dedupe_hash = compute_dedupe_hash(extracted.get("email"), extracted.get("phone"))
        existing = repository.find_lead_by_dedupe_hash(db_session, dedupe_hash)
        if existing is not None:
            return ToolResult(success=False, data=None, error="duplicate", latency_ms=1.0)
        lead = repository.create_lead(
            db_session,
            dedupe_hash=dedupe_hash,
            email=extracted.get("email"),
            phone=extracted.get("phone"),
            status="accepted",
        )
        return ToolResult(
            success=True, data={"lead_id": lead.id, "dedupe_hash": dedupe_hash}, error=None, latency_ms=1.0
        )

    _mock_tool("write_record", _write_record_run)


def _isolate_tool_slate():
    return {name: Tool._registry.pop(name) for name in _CANONICAL_TOOL_NAMES if name in Tool._registry}


def _restore_tool_slate(saved: dict) -> None:
    for name in _CANONICAL_TOOL_NAMES:
        Tool._registry.pop(name, None)
    Tool._registry.update(saved)


_JANE = {
    "name": "Jane Doe",
    "email": "jane@example.com",
    "phone": "+65 5551234",
    "asset_interest": "Whisky cask",
}


def _canonical_plan(contact: dict = _JANE) -> Plan:
    return Plan(
        steps=[
            PlanStep(
                step=1,
                tool=ToolName.PARSE_ENQUIRY,
                args={"enquiry_text": ENQUIRY_TEXT},
                rationale="Extract structured fields from the enquiry.",
            ),
            PlanStep(
                step=2,
                tool=ToolName.LOOKUP_JURISDICTION_RULE,
                args={"country": "Singapore"},
                rationale="Look up the jurisdiction's handling rule.",
            ),
            PlanStep(step=3, tool=ToolName.SCORE_LEAD, args={}, rationale="Score the lead."),
            PlanStep(
                step=4,
                tool=ToolName.WRITE_RECORD,
                args={"extracted": dict(contact)},
                rationale="Persist the lead, rejecting duplicates.",
            ),
        ]
    )


def _passing_decision() -> VerifierDecision:
    return VerifierDecision(
        passed=True,
        confidence=0.95,
        fabrication_detected=False,
        fabricated_fields=[],
        plan_deviation_detected=False,
        deviation_details=None,
        reason="Every field verified against the enquiry text; trace matches the plan.",
    )


class _FakeAdapter(ModelAdapter):
    """Always returns the same valid plan (write_record args fixed to
    `contact`) + a passing verifier decision -- this test's variable of
    interest is `write_record`'s own behavior across repeated calls, not
    planner/verifier non-determinism. `contact` defaults to Jane's details
    but can be varied per-call via `contacts` for the "different enquiry"
    test below."""

    provider_name = "fake"

    def __init__(self, contacts: list[dict] | None = None) -> None:
        self._contacts = list(contacts) if contacts is not None else None
        self._plan_calls = 0

    def complete_structured(self, request: StructuredCompletionRequest) -> StructuredCompletionResponse:
        if request.response_schema is Plan:
            contact = self._contacts[self._plan_calls] if self._contacts is not None else _JANE
            self._plan_calls += 1
            parsed: BaseModel = _canonical_plan(contact)
            model = "fake-planner-model"
        elif request.response_schema is VerifierDecision:
            parsed = _passing_decision()
            model = "fake-verifier-model"
        else:
            raise AssertionError(f"unexpected response_schema: {request.response_schema}")
        return StructuredCompletionResponse(
            parsed=parsed,
            raw_response={"id": "fake"},
            prompt_tokens=10,
            completion_tokens=5,
            latency_ms=1.0,
            model=model,
        )


class TestPipelineRejectsADuplicateSubmission:
    def test_first_submission_succeeds_and_creates_a_lead(self, db_session) -> None:
        saved = _isolate_tool_slate()
        try:
            _register_dedupe_aware_mock_tools(db_session)
            pipeline = Pipeline(adapter=_FakeAdapter(), settings=Settings())

            result = pipeline.run(ENQUIRY_TEXT, db=db_session)

            expected_hash = compute_dedupe_hash("jane@example.com", "+65 5551234")

            assert result.final_status == "completed"
            assert result.final_record is not None
            assert result.final_record.dedupe_hash == expected_hash
            assert repository.find_lead_by_dedupe_hash(db_session, expected_hash) is not None
        finally:
            _restore_tool_slate(saved)

    def test_second_identical_submission_is_rejected_as_a_controlled_failure(self, db_session) -> None:
        saved = _isolate_tool_slate()
        try:
            _register_dedupe_aware_mock_tools(db_session)
            pipeline = Pipeline(adapter=_FakeAdapter(), settings=Settings())

            first = pipeline.run(ENQUIRY_TEXT, db=db_session)
            second = pipeline.run(ENQUIRY_TEXT, db=db_session)

            assert first.final_status == "completed"
            assert first.id != second.id  # two distinct runs, never conflated

            # Verifier is the gate: a duplicate is rejected only at post-verify
            # write_record, after a successful re-verify of the same record.
            assert second.final_status == "error"
            assert second.final_record is not None
            assert second.verifier_decision is not None
            assert second.verifier_decision.passed is True
            assert second.tool_call_trace is not None
            assert len(second.tool_call_trace.calls) == 4
            last_call = second.tool_call_trace.calls[-1]
            assert last_call.tool == ToolName.WRITE_RECORD
            assert last_call.status == "error"
            assert last_call.error == "duplicate"
            assert last_call.validation_errors is None  # a business failure, not a validation one

            # Exactly one lead exists in total -- the duplicate was never inserted.
            assert len(repository.list_leads(db_session)) == 1
        finally:
            _restore_tool_slate(saved)

    def test_a_different_enquiry_with_different_contact_details_is_not_treated_as_a_duplicate(
        self, db_session
    ) -> None:
        """Sanity check that this test's setup isn't accidentally rejecting
        *every* second run -- only ones that genuinely share both a
        normalized email and phone with an existing lead."""
        saved = _isolate_tool_slate()
        try:
            john = {"name": "John Smith", "email": "john@example.com", "phone": "+65 5559999"}
            _register_dedupe_aware_mock_tools(db_session, contacts=[_JANE, john])
            pipeline = Pipeline(adapter=_FakeAdapter(contacts=[_JANE, john]), settings=Settings())

            first = pipeline.run(ENQUIRY_TEXT, db=db_session)
            second = pipeline.run("Hi, I'm John Smith, also interested.", db=db_session)

            assert first.final_status == "completed"
            assert second.final_status == "completed"
            assert first.final_record.dedupe_hash != second.final_record.dedupe_hash
            assert len(repository.list_leads(db_session)) == 2
        finally:
            _restore_tool_slate(saved)


def test_this_test_module_never_touches_harness_infrastructure(db_session) -> None:
    """Structural guard for requirement 6: this module must not drive the
    harness or create harness bookkeeping rows.

    Uses importlib (not process-wide `sys.modules`), because unrelated API
    tests may legitimately load the harness job package in the same pytest
    session.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "evaluation" not in imported
    assert "run_harness" not in imported
    assert repository.get_latest_harness_batch(db_session) is None
