"""The agent pipeline: Planner, Executor, Verifier, Repair loop, Orchestrator.

Per docs/development-rules.md:
- Planner never executes tools.
- Executor is deterministic.
- Verifier is completely independent (own prompt, own call, no shared context).
- Each LLM call is isolated.
"""
