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

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class CoreMemory(Base, TimestampMixin):
    """Versioned, user-independent identity and cognitive priors.

    The grouped JSON documents deliberately make the identity extensible while
    ``schema_version`` and ``version`` keep every cognitive trace reproducible.
    Core memory changes slowly; per-turn state belongs in ``WorkingMemory``.
    """

    __tablename__ = "core_memory"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    identity: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    traits: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    perception_priors: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    interpretation_biases: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    drives: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    action_style: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    values: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    learning_policy: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    locked_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
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
    semantic_memories: Mapped[list["SemanticMemory"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    episodic_memories: Mapped[list["EpisodicMemory"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    learning_memories: Mapped[list["LearningMemory"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    working_memories: Mapped[list["WorkingMemory"]] = relationship(
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
    # Where this thread was first created. Individual messages retain their own
    # source, since the primary conversation is intentionally omnichannel.
    origin_channel: Mapped[str] = mapped_column(
        String(32), nullable=False, default="web", server_default="web"
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )
    working_memory: Mapped["WorkingMemory | None"] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", uselist=False
    )


class ChatMessage(Base, TimestampMixin):
    """A single turn within a conversation (a user prompt or an assistant reply)."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("turn_id", "role", name="uq_chat_messages_turn_role"),
    )

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
    source_channel: Mapped[str] = mapped_column(
        String(32), nullable=False, default="web", server_default="web"
    )
    external_message_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
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
    user_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), index=True, nullable=True
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claim_token: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserChatState(Base):
    """The explicit omnichannel thread selected for a user."""

    __tablename__ = "user_chat_states"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    primary_conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ChannelIdentity(Base, TimestampMixin):
    """A provider identity explicitly linked to an internal account."""

    __tablename__ = "channel_identities"
    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_account_id", "external_subject",
            name="uq_channel_identity_provider_subject",
        ),
        UniqueConstraint("provider", "user_id", name="uq_channel_identity_provider_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ChannelLinkCode(Base, TimestampMixin):
    """Short-lived, single-use proof used to link WhatsApp from the app."""

    __tablename__ = "channel_link_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)


class ChannelInboundMessage(Base, TimestampMixin):
    """Deduplicated provider event awaiting assembly into a chatbot turn."""

    __tablename__ = "channel_inbound_messages"
    __table_args__ = (
        UniqueConstraint(
            "provider_account_id", "provider_message_id",
            name="uq_channel_inbound_provider_message",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channel_identities.id", ondelete="CASCADE"), index=True, nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending", index=True)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True, nullable=True)
    chat_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True
    )


class ConversationTurnState(Base):
    """Durable debounce clock for pending omnichannel input."""

    __tablename__ = "conversation_turn_states"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    first_pending_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_pending_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dispatch_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)


class MessageDelivery(Base, TimestampMixin):
    """Transactional outbox for replies sent to an external channel."""

    __tablename__ = "message_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "assistant_message_id", "channel", "part_index",
            name="uq_delivery_message_channel_part",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assistant_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    channel_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channel_identities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    part_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class SemanticMemory(Base, TimestampMixin):
    """A durable user fact or generalized belief consolidated from experience.

    ``embedding`` is deliberately JSONB for the first version: memory sets are
    small per user and can be ranked in application code without requiring a
    database extension. It can be migrated to pgvector without changing the
    rest of the memory contract when scale warrants an ANN index.
    """

    __tablename__ = "semantic_memories"
    __table_args__ = (
        UniqueConstraint("user_id", "normalized_key", name="uq_semantic_memory_user_key"),
        CheckConstraint("importance BETWEEN 0 AND 1", name="ck_semantic_memory_importance"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_semantic_memory_confidence"),
        CheckConstraint("status IN ('active', 'superseded', 'deleted')", name="ck_semantic_memory_status"),
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
    supporting_episode_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    contradicting_episode_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    user: Mapped["User"] = relationship(back_populates="semantic_memories")


class WorkingMemory(Base, TimestampMixin):
    """Latest cognitive snapshot for one conversation.

    The actual short-term transcript stays in ``chat_messages``. This row holds
    only derived state needed to continue the cognitive loop.
    """

    __tablename__ = "working_memories"
    __table_args__ = (
        CheckConstraint("mode IN ('agent', 'cognitive')", name="ck_working_memory_mode"),
        CheckConstraint("context_window_size BETWEEN 1 AND 200", name="ck_working_memory_context_window"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="agent", server_default="agent")
    context_window_size: Mapped[int] = mapped_column(Integer, nullable=False, default=30, server_default="30")
    current_topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    active_goals: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    unresolved_questions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    cognitive_state: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    retrieved_memories: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    last_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="working_memories")
    conversation: Mapped["Conversation"] = relationship(back_populates="working_memory")


class EpisodicMemory(Base, TimestampMixin):
    """A time- and context-bound experience retained with its provenance."""

    __tablename__ = "episodic_memories"
    __table_args__ = (
        CheckConstraint("emotional_significance BETWEEN 0 AND 1", name="ck_episodic_memory_emotion"),
        CheckConstraint("importance BETWEEN 0 AND 1", name="ck_episodic_memory_importance"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_episodic_memory_confidence"),
        CheckConstraint("status IN ('active', 'consolidated', 'superseded', 'deleted')", name="ck_episodic_memory_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), index=True, nullable=True
    )
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    participants: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    source_message_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    embedding: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    emotional_significance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5, server_default="0.5")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default="1")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    consolidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    user: Mapped["User"] = relationship(back_populates="episodic_memories")


class LearningMemory(Base, TimestampMixin):
    """A supervised cognitive trace used to improve later predictions.

    Predictions and feedback are kept separately so an AI-generated guess can
    never silently become ground truth.
    """

    __tablename__ = "learning_memories"
    __table_args__ = (
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_learning_memory_confidence"),
        CheckConstraint("reward IS NULL OR reward BETWEEN -1 AND 1", name="ck_learning_memory_reward"),
        CheckConstraint("feedback_source IS NULL OR feedback_source IN ('human', 'ai', 'system', 'outcome')", name="ck_learning_memory_feedback_source"),
        CheckConstraint("status IN ('pending', 'reviewed', 'applied', 'rejected')", name="ck_learning_memory_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), index=True, nullable=True
    )
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True
    )
    core_memory_name: Mapped[str | None] = mapped_column(
        ForeignKey("core_memory.name", ondelete="SET NULL"), nullable=True
    )
    core_memory_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    situation: Mapped[dict] = mapped_column(JSONB, nullable=False)
    perceived_cues: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    predicted_interpretation: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    selected_action: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    generated_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    feedback: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    feedback_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reward: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5, server_default="0.5")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending", index=True)
    model_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    user: Mapped["User"] = relationship(back_populates="learning_memories")


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
