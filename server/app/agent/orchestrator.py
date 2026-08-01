"""The Pipeline: ties Planner -> Executor -> Verifier -> (optional Repair) ->
post-verify `write_record` persistence together. See docs/architecture.md
section 1, section 10, and the sequence diagrams in section 14.

Critical property: both the live API (server/app/api/routes_runs.py) and the
standalone evaluation harness (evaluation/harness.py) must call this same
`Pipeline.run()` — no duplicate/parallel code path — so harness numbers are
reproducible against real system behaviour (docs/architecture.md section 11).
"""

from __future__ import annotations

import traceback
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.agent.executor import run_plan
from app.agent.plan_utils import strip_write_record
from app.agent.planner import build_plan
from app.agent.repair import attempt_repair
from app.agent.verifier import verify
from app.core.config import ModelProvider, Settings
from app.core.logging import get_logger, run_id_ctx, stage_ctx
from app.db import repository
from app.db.session import session_scope
from app.llm.base import ModelAdapter, StructuredCompletionRequest, StructuredCompletionResponse
from app.llm.pricing import estimate_cost_usd
from app.schemas.lead_record import LeadRecord
from app.schemas.plan import ToolName
from app.schemas.run import LlmCallUsage, RunResult
from app.schemas.tool_trace import ToolCall, ToolCallStatus, ToolCallTrace
from app.schemas.verifier import VerifierDecision
from app.services.cost import aggregate_usage
from app.tools.registry import ToolRegistry

logger = get_logger(__name__)


def _exception_fields(exc: BaseException) -> dict[str, str]:
    """Preserve the real exception for the run row / harness dashboard."""
    message = str(exc).strip() or repr(exc)
    return {
        "error_type": type(exc).__name__,
        "error_message": message,
        "traceback": traceback.format_exc(),
    }


def _error_fields(
    *,
    error_type: str,
    error_message: str,
    tb: str | None = None,
) -> dict[str, str | None]:
    return {
        "error_type": error_type,
        "error_message": error_message,
        "traceback": tb,
    }


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
        """Execute one full pipeline pass (Planner -> Executor -> Verifier ->
        optional Repair -> post-verify `write_record`), persist the run row,
        and return a complete `RunResult`. Never raises: every stage failure
        is caught and converted into a structured
        `RunResult(final_status=...)` rather than propagating.

        `write_record` runs only after the Verifier has passed (including
        after a successful repair re-verify). Quarantined runs never insert
        a lead. Exactly one lead row is created per accepted enquiry.

        `db`: optional `Session` for run-row persistence. When omitted, a
        session is opened via `session_scope()`. Lead inserts use
        `write_record`'s own short-lived session (same as before).

        `harness_batch_id`: optional, forwarded to `repository.create_run()`.
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
            plan = strip_write_record(build_plan(enquiry_text, tool_manifest, tracking_adapter))
        except Exception as exc:
            logger.error(
                "planner stage failed; returning a structured error result",
                exc_info=True,
                extra={"event": "orchestrator_stage_failed", "stage": "planner"},
            )
            return self._build_result(
                common,
                tracking_adapter,
                final_status="error",
                **_exception_fields(exc),
            )

        try:
            registry = ToolRegistry(adapter=tracking_adapter)
            execution = run_plan(plan, enquiry_text, registry)
        except Exception as exc:
            logger.error(
                "executor stage raised unexpectedly; returning a structured error result",
                exc_info=True,
                extra={"event": "orchestrator_stage_failed", "stage": "executor"},
            )
            return self._build_result(
                common,
                tracking_adapter,
                plan=plan,
                plan_schema_valid=True,
                final_status="error",
                **_exception_fields(exc),
            )

        if execution.error is not None or execution.final_record is None:
            logger.warning(
                "executor did not produce a final_record; skipping verification",
                extra={
                    "event": "orchestrator_no_final_record",
                    "executor_error": execution.error,
                },
            )
            message = execution.error or "Executor produced no final_record"
            return self._build_result(
                common,
                tracking_adapter,
                plan=plan,
                plan_schema_valid=True,
                tool_call_trace=execution.trace,
                final_status="error",
                **_error_fields(error_type="ExecutorError", error_message=message),
            )

        try:
            decision = verify(
                enquiry_text,
                plan,
                execution.trace,
                execution.final_record.model_dump(mode="json"),
                tracking_adapter,
            )
        except Exception as exc:
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
                **_exception_fields(exc),
            )

        if decision.passed:
            return self._finalize_accepted(
                common,
                tracking_adapter,
                registry=registry,
                plan=plan,
                trace=execution.trace,
                record=execution.final_record,
                decision=decision,
                repair_attempted=False,
                repair_succeeded=None,
            )

        # Exactly one repair attempt (docs/architecture.md section 10), then
        # completed or quarantined — never silently dropped. Repair never
        # calls write_record; persistence happens only if re-verify passes.
        try:
            repair = attempt_repair(
                enquiry_text,
                plan,
                execution.trace,
                execution.final_record.model_dump(mode="json"),
                decision,
                tracking_adapter,
            )
        except Exception as exc:
            logger.error(
                "repair stage failed; quarantining with the original verifier decision",
                exc_info=True,
                extra={"event": "orchestrator_stage_failed", "stage": "repair"},
            )
            return self._build_result(
                common,
                tracking_adapter,
                plan=plan,
                plan_schema_valid=True,
                tool_call_trace=execution.trace,
                final_record=execution.final_record,
                verifier_decision=decision,
                repair_attempted=True,
                repair_succeeded=False,
                final_status="quarantined",
                **_exception_fields(exc),
            )

        repaired_record: LeadRecord | None = execution.final_record
        if repair.new_record is not None:
            try:
                repaired_record = LeadRecord.model_validate(repair.new_record)
            except Exception:
                logger.warning(
                    "repair produced a final_record that failed LeadRecord validation; "
                    "keeping the pre-repair record",
                    exc_info=True,
                    extra={"event": "orchestrator_repair_record_invalid"},
                )

        repaired_plan = repair.new_plan or plan
        repaired_trace = repair.new_trace or execution.trace

        if not repair.succeeded or repaired_record is None:
            return self._build_result(
                common,
                tracking_adapter,
                plan=repaired_plan,
                plan_schema_valid=True,
                tool_call_trace=repaired_trace,
                final_record=repaired_record,
                verifier_decision=repair.new_verifier_decision,
                repair_attempted=True,
                repair_succeeded=False,
                final_status="quarantined",
            )

        return self._finalize_accepted(
            common,
            tracking_adapter,
            registry=registry,
            plan=repaired_plan,
            trace=repaired_trace,
            record=repaired_record,
            decision=repair.new_verifier_decision,
            repair_attempted=True,
            repair_succeeded=True,
        )

    def _finalize_accepted(
        self,
        common: dict,
        tracking_adapter: _UsageTrackingAdapter,
        *,
        registry: ToolRegistry,
        plan,
        trace: ToolCallTrace,
        record: LeadRecord,
        decision: VerifierDecision,
        repair_attempted: bool,
        repair_succeeded: bool | None,
    ) -> RunResult:
        """Verifier (or repair re-verify) passed — persist exactly one lead."""
        write_call, write_error = self._execute_write_record(registry, record, trace)
        merged_trace = ToolCallTrace(calls=[*trace.calls, write_call])

        if write_error is not None:
            logger.warning(
                "post-verify write_record failed",
                extra={
                    "event": "orchestrator_write_failed",
                    "error": write_error,
                    "dedupe_hash": record.dedupe_hash,
                },
            )
            return self._build_result(
                common,
                tracking_adapter,
                plan=plan,
                plan_schema_valid=True,
                tool_call_trace=merged_trace,
                final_record=record,
                verifier_decision=decision,
                repair_attempted=repair_attempted,
                repair_succeeded=repair_succeeded,
                final_status="error",
                **_error_fields(
                    error_type="WriteRecordError",
                    error_message=write_error,
                ),
            )

        return self._build_result(
            common,
            tracking_adapter,
            plan=plan,
            plan_schema_valid=True,
            tool_call_trace=merged_trace,
            final_record=record,
            verifier_decision=decision,
            repair_attempted=repair_attempted,
            repair_succeeded=repair_succeeded,
            final_status="completed",
        )

    @staticmethod
    def _execute_write_record(
        registry: ToolRegistry,
        record: LeadRecord,
        prior_trace: ToolCallTrace,
    ) -> tuple[ToolCall, str | None]:
        """Run `write_record` once after Verifier pass. Returns the ToolCall
        to append to the trace and an error string if persistence failed."""
        step_number = len(prior_trace.calls) + 1
        args = {
            "extracted": record.extracted.model_dump(mode="json"),
            "jurisdiction_rule": record.jurisdiction_rule,
            "score": {
                "score": record.score,
                "breakdown": record.score_breakdown,
            },
        }
        started_at = datetime.now(UTC)
        perf_start = time.perf_counter()
        tool = registry.get_tool(ToolName.WRITE_RECORD.value)
        try:
            result = tool.execute(args)
        except Exception as exc:
            finished_at = datetime.now(UTC)
            call = ToolCall(
                step=step_number,
                tool=ToolName.WRITE_RECORD,
                args=args,
                status=ToolCallStatus.ERROR,
                result=None,
                error=f"{type(exc).__name__}: {exc}",
                validation_errors=None,
                latency_ms=(time.perf_counter() - perf_start) * 1000,
                started_at=started_at,
                finished_at=finished_at,
            )
            return call, call.error

        finished_at = datetime.now(UTC)
        latency_ms = (time.perf_counter() - perf_start) * 1000
        if result.success:
            call = ToolCall(
                step=step_number,
                tool=ToolName.WRITE_RECORD,
                args=args,
                status=ToolCallStatus.SUCCESS,
                result=result.data,
                error=None,
                validation_errors=None,
                latency_ms=latency_ms,
                started_at=started_at,
                finished_at=finished_at,
            )
            return call, None

        call = ToolCall(
            step=step_number,
            tool=ToolName.WRITE_RECORD,
            args=args,
            status=ToolCallStatus.ERROR,
            result=None,
            error=result.error,
            validation_errors=None,
            latency_ms=latency_ms,
            started_at=started_at,
            finished_at=finished_at,
        )
        return call, result.error or "write_record failed"

    @staticmethod
    def _build_result(
        common: dict,
        tracking_adapter: _UsageTrackingAdapter,
        **fields: Any,
    ) -> RunResult:
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
        """Persist the run row via db/repository.py -- never raises."""
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
