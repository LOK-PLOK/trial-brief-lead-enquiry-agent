"""Normalization + hashing used by `write_record` to detect duplicate leads.

Kept separate from the tool itself so it's a pure, trivially unit-testable
function (docs/architecture.md section 8: "SHA-256 hash" of the normalized
email + phone).
"""

from __future__ import annotations


def normalize_email(email: str | None) -> str:
    """TODO(services/dedupe): lowercase + trim. Decide handling for None/empty."""
    raise NotImplementedError


def normalize_phone(phone: str | None) -> str:
    """TODO(services/dedupe): strip to digits only. Decide handling for
    None/empty and for country-code variants (e.g. leading '+' / '00')."""
    raise NotImplementedError


def compute_dedupe_hash(email: str | None, phone: str | None) -> str:
    """TODO(services/dedupe): SHA-256 of the normalized email+phone
    concatenation. Must be a pure function (same input -> same hash, always)
    so duplicate detection in write_record is fully deterministic."""
    raise NotImplementedError
