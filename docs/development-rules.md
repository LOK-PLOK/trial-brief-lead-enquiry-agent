# Development Rules

This document is the source of truth for implementation.

## Principles

- Do not change the architecture without justification.
- Planner never executes tools.
- Executor is deterministic.
- Verifier is completely independent.
- Each LLM call is isolated.
- Tool contracts are stable.
- Structured outputs everywhere.
- Prefer simple implementations over clever ones.
- Prioritize correctness and observability.
- The evaluation harness is a first-class feature.