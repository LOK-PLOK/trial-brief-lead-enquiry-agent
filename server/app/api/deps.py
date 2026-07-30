"""Shared FastAPI dependencies. See docs/architecture.md section 3."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db as _get_db
from app.llm.base import ModelAdapter
from app.llm.factory import get_adapter as _get_adapter


def get_db() -> Generator[Session, None, None]:
    yield from _get_db()


def get_adapter() -> ModelAdapter:
    return _get_adapter()


def get_app_settings() -> Settings:
    return get_settings()
