"""The Pipeline: ties Planner -> Executor -> Verifier -> persistence
together. See docs/architecture.md section 1 and the sequence diagrams in
section 14. Repair (section 10) and the evaluation harness (section 11) are
explicitly out of scope for this module -- see `run()`'s docstring.

Critical property: both the live API (server/app/api/routes_runs.py) and the
standalone evaluation harness (evaluation/harness.py) must call this same
`Pipeline.run()` — no duplicate/parallel code path — so harness numbers are
reproducible against real system behaviour (docs/architecture.md section 11).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.agent.executor import run_plan
from app.agent.planner import build_plan
from app.agent.verifier import verify
from app.core.config import ModelProvider, Settings
from app.core.logging import get_logger, run_id_ctx, stage_ctx
from app.db import repository
from app.db.session import session_scope
from app.llm.base import ModelAdapter, StructuredCompletionRequest, StructuredCompletionResponse
from app.llm.pricing import estimate_cost_usd
from app.schemas.run import LlmCallUsage, RunResult
from app.services.cost import aggregate_usage
from app.tools.registry import ToolRegistry

logger = get_logger(__name__)


class _UsageTrackingAdapter(ModelAdapter):
    """Transparently wraps the real `ModelAdapter`, delegating every
    `complete_structured()` call unchanged while recording an
    `LlmCallUsage` entry for each one.

    This is the *only* place `RunResult.llm_calls` is populated. It exists
    because `build_plan()` and `verify()` are contractually specified
    (docs/contracts.md sections 1 and 3) to return only their parsed
    object (`Plan` / `VerifierDecision`) -- not a usage tuple -- and
    `tools/parse_enquiry.py` will eventually make its own LLM call too. A
    wrapping adapter captures usage uniformly for all three call sites from
    one place, driven entirely by `request.metadata["stage"]` (already set
    by `build_plan()`/`verify()` for their own logging), without requiring
    any of those modules to change their documented return contracts.
    """

    def __init__(self, inner: ModelAdapter, *, is_local: bool) -> None:
        self._inner = inner
        self._is_local = is_local
        self.provider_name = inner.provider_name
        self.calls: list[LlmCallUsage] = []

    def complete_structured(self, request: StructuredCompletionRequest) -> StructuredCompletionResponse:
        response = self._inner.complete_structured(request)
        stage = str(request.metadata.get("stage", "unknown"))
        cost_usd = estimate_cost_usd(
            response.model,
            response.prompt_tokens,
            response.completion_tokens,
            is_local=self._is_local,
        )
        self.calls.append(
            LlmCallUsage(
                stage=stage,
                model_provider=self._inner.provider_name,
                model_name=response.model,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                cost_usd=cost_usd,
                latency_ms=response.latency_ms,
            )
        )
        return response


@dataclass
class Pipeline:
    adapter: ModelAdapter
    settings: Settings

    def run(
        self,
        enquiry_text: str,
        *,
        enquiry_id: str | None = None,
        repeat_index: int | None = None,
        is_adversarial: bool = False,
        db: Session | None = None,
        harness_batch_id: str | None = None,
    ) -> RunResult:
        """Execute one full pipeline pass (Planner -> Executor -> Verifier),
        persist it, and return a complete `RunResult`. Never raises: every
        stage failure -- Planner, Executor, or Verifier -- is caught and
        converted into a structured `RunResult(final_status="error", ...)`
        rather than propagating (this task's explicit requirement).

        `db`: optional `Session` for persistence. When omitted, a session is
        opened and closed internally via `db/session.session_scope()`, so
        neither the (not-yet-implemented) API route nor the evaluation
        harness need to manage a session themselves -- both can simply call
        `Pipeline(adapter, settings).run(enquiry_text)`. Pass one explicitly
        (e.g. from a FastAPI request-scoped dependency, or a test fixture)
        to reuse an existing session instead.

        `harness_batch_id`: optional, forwarded as-is to
        `repository.create_run()`. **Additive parameter** (evaluation/harness.py's
        own task, not the Orchestrator's original one): `RunResult` itself
        has no notion of which harness batch it belongs to (a single run
        doesn't know), and `repository.create_run()` already anticipated
        exactly this via its own optional `harness_batch_id` keyword
        (docs/contracts.md section 6: "accepted as an optional keyword so
        the harness can link a run to its batch at persistence time without
        requiring a separate update call") -- this parameter is simply the
        one missing piece of plumbing between that repository contract and
        `Pipeline.run()`'s caller. `None` (the default) behaves exactly as
        before: a live API run, or any call that doesn't pass it, is not
        linked to any batch.

        Explicitly out of scope for this method still (per the Orchestrator's
        own task): the Repair Loop (docs/architecture.md section 10) -- a
        Verifier `passed=False` currently routes straight to
        `final_status="quarantined"` -- never silently dropped, but not yet
        re-attempted. `repair_attempted` stays `False`.

        One run ID is generated up front and bound to `run_id_ctx` for the
        entire call, so every log line emitted by every stage below --
        Planner, Executor, Verifier, and this method's own -- is tagged
        with the same `run_id` (docs/architecture.md section 12), and the
        same ID is used as `RunResult.id` / the `runs.id` primary key, so
        the live log trail and the persisted row can always be correlated
        after the fact.
        """
        run_id = str(uuid.uuid4())
        run_id_token = run_id_ctx.set(run_id)
        stage_token = stage_ctx.set("orchestrator")
        tracking_adapter = _UsageTrackingAdapter(
            self.adapter, is_local=self.settings.model_provider is ModelProvider.OLLAMA
        )
        try:
            result = self._execute_stages(
                run_id=run_id,
                enquiry_text=enquiry_text,
                enquiry_id=enquiry_id,
                repeat_index=repeat_index,
                is_adversarial=is_adversarial,
                tracking_adapter=tracking_adapter,
            )
            self._persist(result, db, harness_batch_id=harness_batch_id)
            return result
        finally:
            stage_ctx.reset(stage_token)
            run_id_ctx.reset(run_id_token)

    def _execute_stages(
        self,
        *,
        run_id: str,
        enquiry_text: str,
        enquiry_id: str | None,
        repeat_index: int | None,
        is_adversarial: bool,
        tracking_adapter: _UsageTrackingAdapter,
    ) -> RunResult:
        common = {
            "id": run_id,
            "enquiry_text": enquiry_text,
            "enquiry_id": enquiry_id,
            "repeat_index": repeat_index,
            "is_adversarial": is_adversarial,
            "created_at": datetime.now(UTC),
        }

        try:
            tool_manifest = ToolRegistry.tool_manifest()
            plan = build_plan(enquiry_text, tool_manifest, tracking_adapter)
        except Exception:
            logger.error(
                "planner stage failed; returning a structured error result",
                exc_info=True,
                extra={"event": "orchestrator_stage_failed", "stage": "planner"},
            )
            return self._build_result(common, tracking_adapter, final_status="error")

        try:
            registry = ToolRegistry(adapter=tracking_adapter)
            execution = run_plan(plan, enquiry_text, registry)
        except Exception:
            # run_plan() itself never raises by contract (docs/contracts.md
            # section 2) -- this is a last-resort net for a genuine
            # Executor bug, so it degrades the same way instead of crashing.
            logger.error(
                "executor stage raised unexpectedly; returning a structured error result",
                exc_info=True,
                extra={"event": "orchestrator_stage_failed", "stage": "executor"},
            )
            return self._build_result(
                common, tracking_adapter, plan=plan, plan_schema_valid=True, final_status="error"
            )

        if execution.error is not None or execution.final_record is None:
            logger.warning(
                "executor did not produce a final_record; skipping verification",
                extra={
                    "event": "orchestrator_no_final_record",
                    "executor_error": execution.error,
                },
            )
            return self._build_result(
                common,
                tracking_adapter,
                plan=plan,
                plan_schema_valid=True,
                tool_call_trace=execution.trace,
                final_status="error",
            )

        try:
            decision = verify(
                enquiry_text,
                plan,
                execution.trace,
                execution.final_record.model_dump(mode="json"),
                tracking_adapter,
            )
        except Exception:
            logger.error(
                "verifier stage failed; returning a structured error result",
                exc_info=True,
                extra={"event": "orchestrator_stage_failed", "stage": "verifier"},
            )
            return self._build_result(
                common,
                tracking_adapter,
                plan=plan,
                plan_schema_valid=True,
                tool_call_trace=execution.trace,
                final_record=execution.final_record,
                final_status="error",
            )

        # Repair Loop intentionally not implemented yet (see run()'s
        # docstring): a failed verification routes straight to quarantine
        # rather than being silently dropped, per the brief's repair-loop
        # spec, just without an intervening repair attempt for now.
        final_status = "completed" if decision.passed else "quarantined"
        return self._build_result(
            common,
            tracking_adapter,
            plan=plan,
            plan_schema_valid=True,
            tool_call_trace=execution.trace,
            final_record=execution.final_record,
            verifier_decision=decision,
            final_status=final_status,
        )

    @staticmethod
    def _build_result(common: dict, tracking_adapter: _UsageTrackingAdapter, **fields) -> RunResult:
        usage = aggregate_usage(tracking_adapter.calls)
        return RunResult(
            **common,
            llm_calls=list(tracking_adapter.calls),
            total_tokens=int(usage["total_tokens"]),
            total_cost_usd=usage["total_cost_usd"],
            total_latency_ms=usage["total_latency_ms"],
            **fields,
        )

    def _persist(self, result: RunResult, db: Session | None, *, harness_batch_id: str | None) -> None:
        """Persist via db/repository.py -- never raises: a persistence
        failure is logged loudly (it must not be silent) but must not
        prevent `run()` from returning the already-complete `RunResult` to
        its caller (this task's requirement 7 takes priority over
        requirement 6 when the two conflict, i.e. a DB outage should not
        turn a real pipeline result into a crash)."""
        try:
            if db is not None:
                repository.create_run(db, result, harness_batch_id=harness_batch_id)
            else:
                with session_scope() as scoped_db:
                    repository.create_run(scoped_db, result, harness_batch_id=harness_batch_id)
        except Exception:
            logger.error(
                "failed to persist run",
                exc_info=True,
                extra={"event": "orchestrator_persist_failed", "run_id": result.id},
            )
