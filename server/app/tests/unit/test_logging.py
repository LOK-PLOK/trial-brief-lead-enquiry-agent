"""Unit tests for core/logging.py's structured JSON formatter, specifically
the surfacing of `extra=` fields -- required for stage-level structured
logging (e.g. agent/planner.py's prompt/response/latency/token-usage logs)
to actually appear in the emitted JSON rather than being silently dropped.
"""

from __future__ import annotations

import json
import logging

from app.core.logging import _JsonFormatter, run_id_ctx, stage_ctx


def _format_record(logger: logging.Logger, **extra) -> dict:
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info("a message", extra=extra)
    finally:
        logger.removeHandler(handler)

    formatter = _JsonFormatter()
    return json.loads(formatter.format(records[0]))


class TestJsonFormatterExtraFields:
    def test_standard_fields_are_always_present(self) -> None:
        logger = logging.getLogger("test.logging.standard")
        payload = _format_record(logger)
        assert set(payload) >= {"timestamp", "level", "logger", "message", "run_id", "stage"}
        assert payload["message"] == "a message"
        assert payload["level"] == "INFO"

    def test_extra_scalar_fields_are_surfaced(self) -> None:
        logger = logging.getLogger("test.logging.extra_scalar")
        payload = _format_record(logger, latency_ms=12.5, prompt_tokens=100, completion_tokens=40)
        assert payload["latency_ms"] == 12.5
        assert payload["prompt_tokens"] == 100
        assert payload["completion_tokens"] == 40

    def test_extra_nested_structures_are_surfaced(self) -> None:
        logger = logging.getLogger("test.logging.extra_nested")
        payload = _format_record(logger, response={"steps": [{"tool": "parse_enquiry"}]})
        assert payload["response"] == {"steps": [{"tool": "parse_enquiry"}]}

    def test_extra_fields_coexist_with_the_standard_message_field(self) -> None:
        logger = logging.getLogger("test.logging.coexist")
        payload = _format_record(logger, event="planner_llm_call")
        assert payload["event"] == "planner_llm_call"
        assert payload["message"] == "a message"

    def test_run_id_and_stage_context_vars_are_still_applied(self) -> None:
        from app.core.logging import _ContextFilter

        logger = logging.getLogger("test.logging.context")
        logger.addFilter(_ContextFilter())
        run_id_token = run_id_ctx.set("run-abc")
        stage_token = stage_ctx.set("planner")
        try:
            payload = _format_record(logger, latency_ms=1.0)
        finally:
            run_id_ctx.reset(run_id_token)
            stage_ctx.reset(stage_token)

        assert payload["run_id"] == "run-abc"
        assert payload["stage"] == "planner"
        assert payload["latency_ms"] == 1.0

    def test_no_extra_fields_produces_only_standard_keys(self) -> None:
        logger = logging.getLogger("test.logging.none")
        payload = _format_record(logger)
        assert set(payload) == {"timestamp", "level", "logger", "message", "run_id", "stage"}
