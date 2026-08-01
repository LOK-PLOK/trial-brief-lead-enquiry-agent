"""Derive dashboard aggregates from persisted harness runs.

Read-only visualization helpers — no Planner/Executor/Verifier changes.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from app.db.models import Run


_FIELD_BUCKETS = (
    "budget_band",
    "urgency",
    "country",
    "jurisdiction",
    "score",
    "plan_deviation",
    "other",
)


def _bucket_field(name: str) -> str:
    n = name.strip().lower().replace(" ", "_")
    if "budget" in n:
        return "budget_band"
    if "urgency" in n:
        return "urgency"
    if n == "country" or n.endswith(".country"):
        if "jurisdiction" in n:
            return "jurisdiction"
        return "country"
    if "jurisdiction" in n or "handling_note" in n or "restricted" in n or "disclaimer" in n:
        return "jurisdiction"
    if n == "score" or "score_breakdown" in n or n.endswith(".score"):
        return "score"
    return "other"


def _score_from_record(final_record: dict | None) -> float | None:
    if not isinstance(final_record, dict):
        return None
    score = final_record.get("score")
    return float(score) if isinstance(score, (int, float)) else None


def build_run_row(run: Run) -> dict[str, Any]:
    error_message = run.error_message
    # Prefer the concrete failure message over a verifier reason when both exist.
    reason = error_message or run.verifier_reason or ""
    return {
        "id": run.id,
        "enquiry_id": run.enquiry_id,
        "repeat_index": run.repeat_index,
        "final_status": run.final_status,
        "score": _score_from_record(run.final_record),
        "repair_attempted": run.repair_attempted,
        "repair_succeeded": run.repair_succeeded,
        "total_latency_ms": run.total_latency_ms,
        "total_cost_usd": run.total_cost_usd,
        "total_tokens": run.total_tokens,
        "verifier_pass": run.verifier_pass,
        "confidence": run.verifier_confidence,
        "reason": reason,
        "error_type": run.error_type,
        "error_message": error_message,
        "traceback": run.traceback,
        "fabrication_detected": run.fabrication_detected,
        "fabricated_fields": run.fabricated_fields or [],
        "plan_deviation_detected": run.plan_deviation_detected,
        "created_at": run.created_at,
    }


def build_run_timeline(run: Run) -> list[dict[str, Any]]:
    """Human-readable stage timeline from persisted run artifacts."""
    events: list[dict[str, Any]] = []

    events.append(
        {
            "stage": "intake",
            "label": "Enquiry received",
            "status": "ok",
            "detail": (run.enquiry_id or "adhoc")
            + (f" · repeat {run.repeat_index}" if run.repeat_index else ""),
        }
    )

    if run.plan is not None:
        events.append(
            {
                "stage": "planner",
                "label": "Planner",
                "status": "ok" if run.plan_schema_valid else "warn",
                "detail": (
                    f"{len(run.plan.get('steps', []))} step(s)"
                    if isinstance(run.plan, dict)
                    else "plan present"
                ),
            }
        )
    else:
        events.append(
            {
                "stage": "planner",
                "label": "Planner",
                "status": "error",
                "detail": "No plan produced",
            }
        )

    trace = run.tool_call_trace if isinstance(run.tool_call_trace, dict) else {}
    calls = trace.get("calls") if isinstance(trace, dict) else None
    if isinstance(calls, list) and calls:
        ok = sum(1 for c in calls if isinstance(c, dict) and c.get("status") == "success")
        err = len(calls) - ok
        events.append(
            {
                "stage": "executor",
                "label": "Executor / Tools",
                "status": "error" if err else "ok",
                "detail": f"{ok} succeeded" + (f", {err} failed" if err else ""),
            }
        )
        for call in calls:
            if not isinstance(call, dict):
                continue
            tool = call.get("tool", "tool")
            status = call.get("status", "error")
            events.append(
                {
                    "stage": "tools",
                    "label": str(tool),
                    "status": "ok" if status == "success" else "error",
                    "detail": (
                        f"{call.get('latency_ms', 0):.0f} ms"
                        if call.get("latency_ms") is not None
                        else (call.get("error") or "")
                    ),
                }
            )
    else:
        events.append(
            {
                "stage": "executor",
                "label": "Executor / Tools",
                "status": "error",
                "detail": "No tool calls recorded",
            }
        )

    if run.verifier_pass is None:
        events.append(
            {
                "stage": "verifier",
                "label": "Verifier",
                "status": "warn",
                "detail": "Verifier did not run",
            }
        )
    else:
        events.append(
            {
                "stage": "verifier",
                "label": "Verifier",
                "status": "ok" if run.verifier_pass else "error",
                "detail": (run.verifier_reason or "")[:180],
            }
        )

    if run.repair_attempted:
        events.append(
            {
                "stage": "repair",
                "label": "Repair",
                "status": "ok" if run.repair_succeeded else "warn",
                "detail": (
                    "Repair succeeded"
                    if run.repair_succeeded
                    else "Repair attempted but did not clear the failure"
                ),
            }
        )
    else:
        events.append(
            {
                "stage": "repair",
                "label": "Repair",
                "status": "ok",
                "detail": "Not needed",
            }
        )

    status_map = {
        "completed": ("ok", "Completed — lead persistence path"),
        "quarantined": ("warn", "Quarantined"),
        "error": ("error", "Error"),
    }
    final_status, detail = status_map.get(run.final_status, ("warn", run.final_status))
    events.append(
        {
            "stage": "outcome",
            "label": "Final status",
            "status": final_status,
            "detail": detail,
        }
    )
    return events


def build_dashboard(
    *,
    batch_id: str,
    started_at: datetime | None,
    finished_at: datetime | None,
    dataset_id: str | None,
    dataset_label: str | None,
    n_repeats: int | None,
    metrics: dict[str, Any] | None,
    runs: list[Run],
) -> dict[str, Any]:
    statuses = Counter(r.final_status for r in runs)
    total = len(runs)
    passed = statuses.get("completed", 0)
    quarantined = statuses.get("quarantined", 0)
    failed = statuses.get("error", 0)
    success_rate = (passed / total) if total else None

    latencies = [r.total_latency_ms for r in runs if r.total_latency_ms is not None]
    costs = [r.total_cost_usd for r in runs]
    tokens = [r.total_tokens for r in runs]

    failure_breakdown = {k: 0 for k in _FIELD_BUCKETS}
    for run in runs:
        if run.plan_deviation_detected:
            failure_breakdown["plan_deviation"] += 1
        for field in run.fabricated_fields or []:
            failure_breakdown[_bucket_field(str(field))] += 1

    # Pipeline health from aggregate outcomes
    planner_ok = sum(1 for r in runs if r.plan is not None and r.plan_schema_valid)
    tools_ok = 0
    tools_total = 0
    for run in runs:
        trace = run.tool_call_trace if isinstance(run.tool_call_trace, dict) else {}
        calls = trace.get("calls") if isinstance(trace, dict) else None
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, dict):
                continue
            tools_total += 1
            if call.get("status") == "success":
                tools_ok += 1
    verifier_runs = [r for r in runs if r.verifier_pass is not None]
    verifier_pass = sum(1 for r in verifier_runs if r.verifier_pass)
    repair_attempted = [r for r in runs if r.repair_attempted]
    repair_ok = sum(1 for r in repair_attempted if r.repair_succeeded)

    def _health(ok: int, denom: int, *, warn_below: float = 0.95) -> str:
        if denom == 0:
            return "unknown"
        ratio = ok / denom
        if ratio >= warn_below:
            return "green"
        if ratio >= 0.7:
            return "yellow"
        return "red"

    # Verifier health: high quarantine from fabrication → yellow/red even if "working"
    verifier_health = _health(verifier_pass, len(verifier_runs))
    if quarantined and total and (quarantined / total) >= 0.4:
        verifier_health = "red" if verifier_health != "green" or (quarantined / total) >= 0.5 else "yellow"

    cost_by_stage: dict[str, float] = {}
    for run in runs:
        for call in run.llm_calls:
            stage = call.stage or "unknown"
            cost_by_stage[stage] = cost_by_stage.get(stage, 0.0) + float(call.cost_usd or 0.0)

    duration_ms = None
    if started_at and finished_at:
        duration_ms = (finished_at - started_at).total_seconds() * 1000.0

    return {
        "batch_id": batch_id,
        "dataset_id": dataset_id,
        "dataset_label": dataset_label,
        "n_repeats": n_repeats,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
        "statistics": {
            "total": total,
            "passed": passed,
            "quarantined": quarantined,
            "failed": failed,
            "success_rate": success_rate,
        },
        "cost": {
            "total_tokens": int(sum(tokens)) if tokens else 0,
            "total_cost_usd": float(sum(costs)) if costs else 0.0,
            "average_cost_per_enquiry": (float(sum(costs)) / total) if total else 0.0,
        },
        "performance": {
            "average_runtime_ms": (sum(latencies) / len(latencies)) if latencies else 0.0,
            "fastest_ms": min(latencies) if latencies else None,
            "slowest_ms": max(latencies) if latencies else None,
        },
        "failure_breakdown": failure_breakdown,
        "pipeline_health": {
            "planner": _health(planner_ok, total) if total else "unknown",
            "executor": _health(tools_ok, tools_total) if tools_total else "unknown",
            "tools": _health(tools_ok, tools_total) if tools_total else "unknown",
            "verifier": verifier_health if verifier_runs else "unknown",
            "repair": (
                _health(repair_ok, len(repair_attempted), warn_below=0.8)
                if repair_attempted
                else "green"
            ),
            "note": (
                "Most quarantines come from verifier decisions."
                if quarantined > passed and quarantined > 0
                else None
            ),
        },
        "charts": {
            "pass_vs_quarantined": {
                "passed": passed,
                "quarantined": quarantined,
                "failed": failed,
            },
            "repair_success_rate": (
                (repair_ok / len(repair_attempted)) if repair_attempted else None
            ),
            "failures_by_field": failure_breakdown,
            "average_runtime_ms": (sum(latencies) / len(latencies)) if latencies else 0.0,
            "cost_per_stage": cost_by_stage,
        },
        "metrics": metrics,
        "runs": [build_run_row(r) for r in runs],
    }
