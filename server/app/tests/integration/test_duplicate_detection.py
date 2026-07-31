"""End-to-end duplicate-detection tests: `services/dedupe.py`'s hash
combined with the real `leads.dedupe_hash` unique constraint and
`db/repository.py`'s lookup function -- the exact mechanism a future
`write_record` implementation will use.

See docs/architecture.md section 8 ("checks leads.dedupe_hash uniqueness
before insert") and the explicit requirement: "duplicate if both [normalized
email and phone] match".
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.db import repository
from app.services.dedupe import compute_dedupe_hash


def _insert_lead(db_session, *, email, phone, status="accepted"):
    dedupe_hash = compute_dedupe_hash(email, phone)
    existing = repository.find_lead_by_dedupe_hash(db_session, dedupe_hash)
    if existing is not None:
        return existing, dedupe_hash
    lead = repository.create_lead(
        db_session, dedupe_hash=dedupe_hash, email=email, phone=phone, status=status
    )
    return lead, dedupe_hash


class TestDuplicateIfBothMatch:
    def test_identical_email_and_phone_is_a_duplicate(self, db_session) -> None:
        first, hash1 = _insert_lead(db_session, email="jane@example.com", phone="+65 5551234")
        second_hash = compute_dedupe_hash("jane@example.com", "+65 5551234")

        assert second_hash == hash1
        assert repository.find_lead_by_dedupe_hash(db_session, second_hash) is not None

        with pytest.raises(IntegrityError):
            repository.create_lead(
                db_session, dedupe_hash=second_hash, email="jane@example.com", phone="+65 5551234"
            )

    def test_equivalent_after_normalization_is_a_duplicate(self, db_session) -> None:
        """Different raw formatting, same normalized value -> same
        duplicate, per normalize_email/normalize_phone."""
        _insert_lead(db_session, email="Jane@Example.com", phone="(555) 123-4567")
        hash_of_second_submission = compute_dedupe_hash("  jane@example.com  ", "555-123-4567")

        with pytest.raises(IntegrityError):
            repository.create_lead(
                db_session,
                dedupe_hash=hash_of_second_submission,
                email="jane@example.com",
                phone="5551234567",
            )

    def test_matching_email_only_is_not_a_duplicate(self, db_session) -> None:
        """Same email, different phone -> NOT a duplicate ("both match" is
        required, not "either matches")."""
        lead1, hash1 = _insert_lead(db_session, email="jane@example.com", phone="5551111")
        lead2, hash2 = _insert_lead(db_session, email="jane@example.com", phone="5552222")

        assert hash1 != hash2
        assert lead1.id != lead2.id
        assert repository.list_leads(db_session) and len(repository.list_leads(db_session)) == 2

    def test_matching_phone_only_is_not_a_duplicate(self, db_session) -> None:
        """Same phone, different email -> NOT a duplicate."""
        lead1, hash1 = _insert_lead(db_session, email="jane@example.com", phone="5551234")
        lead2, hash2 = _insert_lead(db_session, email="john@example.com", phone="5551234")

        assert hash1 != hash2
        assert lead1.id != lead2.id

    def test_both_missing_email_and_phone_hash_the_same(self, db_session) -> None:
        """Documented edge case: two leads with neither email nor phone
        normalize to the same ("", "") pair and therefore the same hash --
        the second insert is treated as a duplicate of the first. This is a
        known consequence of "duplicate if both match" when both fields are
        absent, not a bug in the hash function itself."""
        lead1, hash1 = _insert_lead(db_session, email=None, phone=None)
        hash2 = compute_dedupe_hash(None, None)

        assert hash1 == hash2
        with pytest.raises(IntegrityError):
            repository.create_lead(db_session, dedupe_hash=hash2, status="accepted")

    def test_write_record_style_check_before_insert_avoids_the_integrity_error(self, db_session) -> None:
        """The sanctioned pattern (docs/contracts.md section 6): check
        `find_lead_by_dedupe_hash` first, only insert if absent -- this is
        what a real `write_record` implementation must do, and never hit
        `IntegrityError` in the expected duplicate-submission case."""
        dedupe_hash = compute_dedupe_hash("jane@example.com", "5551234")
        repository.create_lead(
            db_session,
            dedupe_hash=dedupe_hash,
            email="jane@example.com",
            phone="5551234",
            status="accepted",
        )

        resubmission_hash = compute_dedupe_hash("JANE@example.com", "555-1234")
        existing = repository.find_lead_by_dedupe_hash(db_session, resubmission_hash)

        assert existing is not None
        assert existing.dedupe_hash == dedupe_hash
