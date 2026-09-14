"""SQLAlchemy ORM models for MacroVision.

Schema mirrors the draft in CLAUDE.md:
  - users          : account + hashed credentials
  - weight_logs    : per-user weight entries over time (kg)
  - calorie_logs   : per-user confirmed macro intake per day
  - meals          : raw macro predictions from an uploaded food photo
  - conversations  : a chatbot thread belonging to a user
  - chat_messages  : individual turns (user/assistant) within a conversation
  - google_accounts: per-user Google OAuth grant powering the Gmail tools

weight_logs and calorie_logs are intentionally decoupled — a user may log
weight on a day without logging calories, or vice versa. A `meal` is a raw
model output the user may or may not promote into a calorie_log.

Chat history is server-side: the client sends only the new user message plus a
conversation id, and the backend reconstructs history from `chat_messages`.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class CoreMemory(Base, TimestampMixin):
    """Stable, user-independent personality settings for an agent."""

    __tablename__ = "core_memory"
    __table_args__ = (
        CheckConstraint("curiosity BETWEEN 0 AND 1", name="ck_core_memory_curiosity"),
        CheckConstraint("initiative BETWEEN 0 AND 1", name="ck_core_memory_initiative"),
        CheckConstraint("caution BETWEEN 0 AND 1", name="ck_core_memory_caution"),
        CheckConstraint("warmth BETWEEN 0 AND 1", name="ck_core_memory_warmth"),
    )

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    curiosity: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    initiative: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    caution: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    warmth: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(
        String(320), unique=True, index=True, nullable=False
    )
    # Always a hash (e.g. bcrypt/argon2) — never plaintext.
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    weight_logs: Mapped[list["WeightLog"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    calorie_logs: Mapped[list["CalorieLog"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    meals: Mapped[list["Meal"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    long_term_memories: Mapped[list["LongTermMemory"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    # At most one connected Google account per user (see GoogleAccount).
    google_account: Mapped["GoogleAccount | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


class WeightLog(Base):
    __tablename__ = "weight_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    weight: Mapped[float] = mapped_column(Float, nullable=False)  # kilograms
    date: Mapped[date] = mapped_column(Date, nullable=False)

    user: Mapped["User"] = relationship(back_populates="weight_logs")


class CalorieLog(Base):
    __tablename__ = "calorie_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    calories: Mapped[float] = mapped_column(Float, nullable=False)
    fat: Mapped[float] = mapped_column(Float, nullable=False)
    carbs: Mapped[float] = mapped_column(Float, nullable=False)
    protein: Mapped[float] = mapped_column(Float, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)

    user: Mapped["User"] = relationship(back_populates="calorie_logs")


class Meal(Base):
    __tablename__ = "meals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    image_path: Mapped[str] = mapped_column(Text, nullable=False)
    predicted_calories: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_protein: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_carbs: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_fat: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="meals")


class UserSession(Base):
    """Server-side login session.

    The cookie sent to the client holds a random opaque token; only the
    SHA-256 hash of that token is stored here, so a DB leak can't be used
    to resurrect live sessions.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="sessions")


class GoogleAccount(Base, TimestampMixin):
    """A user's connected Google account, backing the Gmail integration.

    Only the refresh token is durable; access tokens are short-lived (~1h) and
    cached here purely to avoid a refresh round-trip on every chat turn. Both
    are stored encrypted (see ``app.core.crypto``) — unlike session tokens they
    can't be hashed, since we need the plaintext to call Google.

    One row per user: reconnecting overwrites the existing row rather than
    accumulating stale grants.
    """

    __tablename__ = "google_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    # Google's stable account identifier (the `sub` claim) and the address the
    # grant belongs to — shown in the UI so the user knows what's connected.
    google_sub: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # Fernet ciphertext, never plaintext.
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Space-separated list of scopes Google actually granted (which may be a
    # subset of what we asked for, if the user unticked boxes on the consent screen).
    scopes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="google_account")

    def granted_scopes(self) -> set[str]:
        return set(self.scopes.split())


class Conversation(Base, TimestampMixin):
    """A chatbot thread. Groups the ordered messages of one conversation.

    History lives here (not on the client): each turn appends a `ChatMessage`,
    and the agent's next reply is built from this conversation's messages.
    """

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # A short human-readable label, usually derived from the first user message.
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )


class ChatMessage(Base, TimestampMixin):
    """A single turn within a conversation (a user prompt or an assistant reply)."""

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Shared by the user prompt and its assistant outcome.
    turn_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Assistant outcomes can be completed, partial, cancelled, or failed.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="completed", server_default="completed"
    )
    # Ids of images the user attached to this turn (see services/image_store).
    # A column rather than a marker inside `content`, so history stays clean
    # text for the model while the frontend can still render the thumbnails —
    # and so "the photo from earlier" survives into later turns.
    image_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    asset_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class ChatJob(Base, TimestampMixin):
    """Durable chat generation job owned by the external chat worker."""

    __tablename__ = "chat_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, index=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claim_token: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LongTermMemory(Base, TimestampMixin):
    """A durable, user-scoped fact distilled from conversation history.

    ``embedding`` is deliberately JSONB for the first version: memory sets are
    small per user and can be ranked in application code without requiring a
    database extension. It can be migrated to pgvector without changing the
    rest of the memory contract when scale warrants an ANN index.
    """

    __tablename__ = "long_term_memories"
    __table_args__ = (
        UniqueConstraint("user_id", "normalized_key", name="uq_memory_user_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    source_conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True
    )
    last_confirmed_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    user: Mapped["User"] = relationship(back_populates="long_term_memories")


class FileDeletionJob(Base, TimestampMixin):
    """Durable cleanup outbox; deliberately survives deletion of its user."""

    __tablename__ = "file_deletion_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MemoryJob(Base, TimestampMixin):
    """Durable outbox item consumed by the separate memory worker."""

    __tablename__ = "memory_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    assistant_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(nullable=True)
