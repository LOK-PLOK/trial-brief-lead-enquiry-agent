"""Normalization + hashing used by `write_record` to detect duplicate leads.
See docs/architecture.md section 8 ("Normalizes email (lowercase/trim) and
phone (digits-only), SHA-256 hash, checks leads.dedupe_hash uniqueness
before insert") and docs/contracts.md section 8 (`leads.dedupe_hash`
unique constraint).

Two leads are duplicates of each other exactly when *both* their normalized
email and their normalized phone match -- the hash is computed over the
pair jointly (never over just one field), so the `leads.dedupe_hash` unique
constraint at the database layer is what actually enforces "duplicate if
both match".

Kept separate from the `write_record` tool itself so this is a pure,
trivially unit-testable module (no DB, no I/O).
"""

from __future__ import annotations

import hashlib

# ASCII "unit separator" -- cannot appear in a normalized email/phone value,
# so e.g. email="ab" + phone="" can never collide with email="a" + phone="b"
# in the joined string fed to the hash.
_HASH_FIELD_SEPARATOR = "\x1f"


def normalize_email(email: str | None) -> str:
    """Lowercase + trim. `None` (no email supplied) normalizes to `""`
    rather than raising, so the function is total and duplicate detection
    stays deterministic even for leads missing an email."""
    if email is None:
        return ""
    return email.strip().lower()


def normalize_phone(phone: str | None) -> str:
    """Strip to digits only (per docs/architecture.md section 8). `None`
    normalizes to `""`.

    Known limitation, documented rather than silently patched over: this is
    a literal digits-only strip, not full E.164/country-code normalization.
    "+65 5551234" -> "655551234" and "0065 5551234" -> "006555551234" are
    *not* recognized as the same number even though they may refer to the
    same real-world phone. A proper fix would use a phone-number library
    (e.g. `phonenumbers`); out of scope for this trial.
    """
    if phone is None:
        return ""
    return "".join(ch for ch in phone if ch.isdigit())


def compute_dedupe_hash(email: str | None, phone: str | None) -> str:
    """SHA-256 of the normalized `email` + `phone` pair.

    Pure function: the same `(email, phone)` pair always produces the same
    hash, so duplicate detection in `write_record` is fully deterministic
    (docs/architecture.md section 8). Two leads collide (are "duplicates")
    if and only if *both* their normalized email and normalized phone are
    identical -- a match on only one of the two fields produces a different
    hash and is correctly treated as a distinct lead.
    """
    joined = f"{normalize_email(email)}{_HASH_FIELD_SEPARATOR}{normalize_phone(phone)}"
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
