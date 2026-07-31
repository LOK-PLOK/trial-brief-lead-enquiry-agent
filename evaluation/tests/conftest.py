"""Shared pytest fixtures for evaluation/'s own test suite.

Deliberately duplicated from server/app/tests/conftest.py rather than
imported: evaluation/ is a standalone package that consumes `app.*` as a
client of the backend (see evaluation/harness.py's own docstring), not part
of the `server/` package itself, and the two test suites are run as
independent pytest invocations (root pyproject.toml vs. server/pyproject.toml)
-- see this project's convention of keeping integration test modules
self-contained (docs/architecture.md; tests/integration/test_verifier_integration.py).
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from app.core.config import Settings
from app.db.models import Base
from app.db.session import build_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    """A `Settings` instance pointed at a throwaway SQLite file for this
    test only -- never the real `server/data/app.db`."""
    db_path = tmp_path / "test.db"
    return Settings(database_url=f"sqlite:///{db_path}")


@pytest.fixture
def db_engine(test_settings: Settings) -> Generator[Engine, None, None]:
    """A real (file-based) SQLite engine with all tables created, torn down
    after the test."""
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
