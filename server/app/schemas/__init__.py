"""Pydantic models that are the single source of truth for every structured
output in the system (Plan, ExtractedFields, VerifierDecision, LeadRecord,
ToolCall/ToolCallTrace, RunResult).

These same classes are used for: (a) LLM structured-output schemas, (b) the
FastAPI response models, and (c) the objects persisted to / read from the
database. Do not duplicate field definitions elsewhere.
"""
