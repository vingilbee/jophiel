"""Declarative base and shared model mixins.

Import this module's ``Base`` everywhere models are defined, and import
``app.db.models`` (which registers all tables on ``Base.metadata``) wherever
the full schema is needed — notably Alembic's ``env.py``.
"""
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all ORM models."""


class TimestampMixin:
    """Adds a server-managed ``created_at`` column."""

    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
