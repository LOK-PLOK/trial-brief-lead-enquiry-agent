"""Provider-agnostic model adapter layer.

See docs/architecture.md section 6 (System Architecture) and the confirmed
direction: no hard dependency on any single provider. `agent/` code must only
ever depend on `llm.base.ModelAdapter`, never on a concrete provider module.
"""
