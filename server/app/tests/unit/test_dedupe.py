"""Unit tests for services/dedupe.py: email/phone normalization and the
combined dedupe hash. Pure functions, no DB -- see
app/tests/integration/test_duplicate_detection.py for the end-to-end
"duplicate if both match" behavior against the real `leads.dedupe_hash`
unique constraint.
"""

from __future__ import annotations

from app.services.dedupe import compute_dedupe_hash, normalize_email, normalize_phone


class TestNormalizeEmail:
    def test_lowercases_and_trims(self) -> None:
        assert normalize_email("  Jane.Doe@Example.COM  ") == "jane.doe@example.com"

    def test_already_normalized_is_unchanged(self) -> None:
        assert normalize_email("jane@example.com") == "jane@example.com"

    def test_none_normalizes_to_empty_string(self) -> None:
        assert normalize_email(None) == ""

    def test_empty_string_stays_empty(self) -> None:
        assert normalize_email("") == ""

    def test_internal_whitespace_is_preserved(self) -> None:
        # Only leading/trailing whitespace is trimmed -- not a validity check.
        assert normalize_email(" jane doe@example.com ") == "jane doe@example.com"


class TestNormalizePhone:
    def test_strips_to_digits_only(self) -> None:
        assert normalize_phone("+65 5551-234") == "655551234"

    def test_removes_parentheses_and_dashes(self) -> None:
        assert normalize_phone("(555) 123-4567") == "5551234567"

    def test_none_normalizes_to_empty_string(self) -> None:
        assert normalize_phone(None) == ""

    def test_empty_string_stays_empty(self) -> None:
        assert normalize_phone("") == ""

    def test_different_international_prefix_notation_is_a_known_limitation(self) -> None:
        # Documented limitation: digits-only normalization does not treat
        # "+65..." and "0065..." as equivalent, even though they may be the
        # same real-world number.
        assert normalize_phone("+65 5551234") != normalize_phone("0065 5551234")


class TestComputeDedupeHash:
    def test_is_deterministic(self) -> None:
        first = compute_dedupe_hash("jane@example.com", "+65 5551234")
        second = compute_dedupe_hash("jane@example.com", "+65 5551234")
        assert first == second

    def test_is_case_and_whitespace_insensitive_via_normalization(self) -> None:
        first = compute_dedupe_hash("Jane@Example.com", "555-1234")
        second = compute_dedupe_hash("  jane@example.com  ", "(555) 1234")
        assert first == second

    def test_differs_when_email_differs(self) -> None:
        first = compute_dedupe_hash("jane@example.com", "5551234")
        second = compute_dedupe_hash("john@example.com", "5551234")
        assert first != second

    def test_differs_when_phone_differs(self) -> None:
        first = compute_dedupe_hash("jane@example.com", "5551234")
        second = compute_dedupe_hash("jane@example.com", "5559999")
        assert first != second

    def test_returns_a_sha256_hex_digest(self) -> None:
        result = compute_dedupe_hash("jane@example.com", "5551234")
        assert len(result) == 64
        assert all(ch in "0123456789abcdef" for ch in result)

    def test_field_boundary_cannot_be_confused(self) -> None:
        """Without a separator, email="ab"+phone="" and email="a"+phone="b"
        would naively concatenate to the same string ("ab"). The hash must
        distinguish them."""
        first = compute_dedupe_hash("ab", "")
        second = compute_dedupe_hash("a", "b")
        assert first != second

    def test_both_none_is_still_deterministic(self) -> None:
        first = compute_dedupe_hash(None, None)
        second = compute_dedupe_hash(None, None)
        assert first == second
