"""Engine and session factory.

SQLite is used with `check_same_thread=False` (safe here because FastAPI's
default sync-route handling plus a single-process `uvicorn` worker mean we
never share one session across threads concurrently) and WAL journal mode,
which reduces "database is locked" errors under light concurrent access
(docs/architecture.md section 15, SQLite concurrency risk).
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
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

    No migration tool is used for this trial (Alembic was intentionally
    omitted for a single-environment assessment with no production schema
    history to preserve). Additive nullable columns are
    applied via `_ensure_sqlite_columns` so existing local DBs pick up new
    observability fields without a full reset.
    """
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_columns(engine)


def _ensure_sqlite_columns(engine: Engine) -> None:
    """Add newly introduced nullable columns to existing SQLite tables."""
    if engine.dialect.name != "sqlite":
        return
    additions = (
        ("runs", "error_type", "TEXT"),
        ("runs", "error_message", "TEXT"),
        ("runs", "traceback", "TEXT"),
    )
    with engine.begin() as conn:
        for table, column, col_type in additions:
            existing = {
                row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            }
            if column not in existing:
                conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
                )


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a session, always closed after the request.
    See server/app/api/deps.py for the wired-up dependency. Does not
    auto-commit -- route handlers (via db/repository.py) are responsible for
    committing their own writes, same as `session_scope()` below."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope(
    session_factory: sessionmaker[Session] | None = None,
) -> Generator[Session, None, None]:
    """Context-manager session for non-FastAPI callers (evaluation harness
    scripts, one-off tools like `tools/write_record.py`, tests): commits on
    clean exit, rolls back and re-raises on any exception, always closes.

    `session_factory`: optional override of the process-wide, `lru_cache`d
    `get_session_factory()` -- additive, backward-compatible (every existing
    call site passes none and gets exactly the previous behavior). Exists so
    a caller that isn't itself request-scoped (namely `WriteRecordTool`,
    which opens its own session rather than requiring `ToolRegistry` to
    inject one -- see its own docstring) can still be pointed at an
    isolated, throwaway database in tests instead of the real cached engine.

    Important: every `db/repository.py` create/write function commits its
    own write immediately (per docs/contracts.md section 6), so this does
    *not* provide multi-call atomicity across several repository calls --
    an earlier call's insert in the same `with` block is already durably
    committed by the time a later statement raises, and rollback cannot
    undo it. What this guarantees is: (1) any *uncommitted* work added
    directly to the session (e.g. `session.add(...)` without going through
    `repository.py`) is rolled back on exception, and (2) the session is
    always closed, regardless of which path is taken.
    """
    factory = session_factory or get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
