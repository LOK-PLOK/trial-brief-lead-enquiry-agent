"""Small, stateless support services shared by tools/agent code (deduplication
hashing, cost aggregation) that don't belong to any single tool or stage.

Note: this package is not enumerated in docs/architecture.md's folder tree,
but it is implied by section 8 (write_record's normalize+hash step) and
section 12 (cost/token aggregation). Added here rather than inlining that
logic into a tool or route module, to keep those modules single-purpose and
the logic independently unit-testable. Flagging per development-rules.md
("do not change the architecture without justification").
"""
