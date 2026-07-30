"""The evaluation harness. See docs/architecture.md sections 11 and development-rules.md
("The evaluation harness is a first-class feature.").

Standalone from the FastAPI app on purpose: run via
`PYTHONPATH=server python -m evaluation.harness` from the repo root (see
README.md). Must import and call `app.agent.orchestrator.Pipeline.run()` —
never a separate/duplicate pipeline implementation — so harness numbers
reproduce real system behaviour.
"""
