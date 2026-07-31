"""Shared pytest fixtures for the backend test suite.

Every DB-backed fixture here uses a fresh, file-based SQLite database per
test (via pytest's built-in `tmp_path`) rather than `:memory:`.
`:memory:` databases cannot run in WAL journal mode, and part of what this
suite verifies is that WAL mode is actually enabled (docs/architecture.md
section 15) -- so integration-style tests need a real file to be
meaningful.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.models import Base
from app.db.session import build_engine


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    """A `Settings` instance pointed at a throwaway SQLite file for this
    test only -- never the real `server/data/app.db`."""
    db_path = tmp_path / "test.db"
    return Settings(database_url=f"sqlite:///{db_path}")


@pytest.fixture
def db_engine(test_settings: Settings) -> Generator[Engine, None, None]:
    """A real (file-based) SQLite engine with all tables created, torn down
    after the test. Built via `build_engine()` directly (not the
    `lru_cache`d `get_engine()`/`get_settings()`) so each test gets full
    isolation."""
    engine = build_engine(test_settings)
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Generator[Session, None, None]:
    """A `Session` bound to `db_engine`, always closed after the test."""
    factory = sessionmaker(bind=db_engine, autoflush=False, autocommit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()
