"""Unit tests for `WriteRecordTool`'s real persistence and duplicate-
detection behavior (docs/contracts.md section 6). Uses the shared
`db_engine` fixture (conftest.py) wrapped in a `sessionmaker`, passed in via
`WriteRecordTool(session_factory=...)`, so nothing here ever touches the
real `server/data/app.db`.
"""

from __future__ import annotations

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.db import repository
from app.tools.write_record import WriteRecordTool

VALID_JURISDICTION_RULE = {
    "country": "Singapore",
    "requires_disclaimer": True,
    "restricted": False,
    "handling_note": "Standard disclosure required.",
}

VALID_SCORE_RESULT = {"score": 78, "breakdown": {"budget": 40, "urgency": 20, "jurisdiction_risk": 18}}

VALID_EXTRACTED = {
    "name": "Jane Doe",
    "email": "jane@example.com",
    "phone": "+65 5551234",
    "country": "Singapore",
    "budget_band": "D",
    "asset_interest": "Whisky cask",
    "urgency": "Immediate",
}


@pytest.fixture
def tool(db_engine: Engine) -> WriteRecordTool:
    factory = sessionmaker(bind=db_engine, autoflush=False, autocommit=False, future=True)
    return WriteRecordTool(session_factory=factory)


def _args(extracted: dict | None = None) -> dict:
    return {
        "extracted": extracted or VALID_EXTRACTED,
        "jurisdiction_rule": VALID_JURISDICTION_RULE,
        "score": VALID_SCORE_RESULT,
    }


class TestWriteRecordTool:
    def test_a_new_record_is_persisted_and_returns_its_lead_id_and_dedupe_hash(
        self, tool: WriteRecordTool, db_engine: Engine
    ) -> None:
        result = tool.execute(_args())

        assert result.success is True
        assert result.error is None
        assert result.data["lead_id"]
        assert result.data["dedupe_hash"]

        factory = sessionmaker(bind=db_engine, autoflush=False, autocommit=False, future=True)
        with factory() as db:
            lead = repository.find_lead_by_dedupe_hash(db, result.data["dedupe_hash"])
            assert lead is not None
            assert lead.email == "jane@example.com"
            assert lead.status == "accepted"
            assert lead.source_run_id is None
            assert lead.score == 78

    def test_a_second_write_with_the_same_email_and_phone_is_reported_as_a_duplicate(
        self, tool: WriteRecordTool
    ) -> None:
        first = tool.execute(_args())
        assert first.success is True

        second = tool.execute(_args())

        assert second.success is False
        assert second.error == "duplicate"
        assert second.data is None

    def test_a_different_email_and_phone_is_not_a_duplicate(self, tool: WriteRecordTool) -> None:
        first = tool.execute(_args())
        assert first.success is True

        other = dict(VALID_EXTRACTED)
        other["email"] = "john@example.com"
        other["phone"] = "+65 5559999"
        second = tool.execute(_args(other))

        assert second.success is True
        assert second.data["dedupe_hash"] != first.data["dedupe_hash"]

    def test_dedupe_is_normalized_across_case_and_whitespace(self, tool: WriteRecordTool) -> None:
        first = tool.execute(_args())
        assert first.success is True

        other = dict(VALID_EXTRACTED)
        other["email"] = "  JANE@EXAMPLE.COM  "
        other["phone"] = "+65 555 1234"
        second = tool.execute(_args(other))

        assert second.success is False
        assert second.error == "duplicate"
