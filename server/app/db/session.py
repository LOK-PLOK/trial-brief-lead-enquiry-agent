"""Engine and session factory.

SQLite is used with `check_same_thread=False` (safe here because FastAPI's
default sync-route handling plus a single-process `uvicorn` worker mean we
never share one session across threads concurrently) and WAL journal mode,
which reduces "database is locked" errors under light concurrent access
(docs/architecture.md section 15, SQLite concurrency risk).
"""

from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.models import Base


def _ensure_sqlite_dir_exists(database_url: str) -> None:
    """SQLite won't create missing parent directories on its own; ensure the
    data directory exists before the engine tries to open the file."""
    if not database_url.startswith("sqlite"):
        return
    path = urlparse(database_url).path
    if not path or path == ":memory:":
        return
    Path(path).resolve().parent.mkdir(parents=True, exist_ok=True)


def _enable_sqlite_wal(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    if not hasattr(dbapi_connection, "execute"):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()


def build_engine(settings: Settings) -> Engine:
    connect_args = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        _ensure_sqlite_dir_exists(settings.database_url)

    engine = create_engine(settings.database_url, connect_args=connect_args, future=True)

    if settings.database_url.startswith("sqlite"):
        event.listen(engine, "connect", _enable_sqlite_wal)

    return engine


@lru_cache
def get_engine() -> Engine:
    return build_engine(get_settings())


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    """Create all tables if they don't exist.

    No migration tool is used for this trial (see scaffold review: Alembic
    was removed as unnecessary for a single-environment 3-day assessment with
    no production schema history to preserve). If the schema needs to evolve
    against a database that already has data, replace this with a real
    migration tool rather than editing models.py in place.
    """
    Base.metadata.create_all(bind=get_engine())


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a session, always closed after the request.
    See server/app/api/deps.py for the wired-up dependency."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
