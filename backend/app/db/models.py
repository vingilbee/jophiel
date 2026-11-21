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

from sqlalchemy import DDL, BigInteger, Boolean, LargeBinary, CheckConstraint, Computed, Date, DateTime, FetchedValue, Float, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, event, func, text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class CoreMemory(Base, TimestampMixin):
    """Text-only personality, independent of a user. Loaded once per worker process."""

    __tablename__ = "core_memory"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=2, server_default="2")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    profile: Mapped[str] = mapped_column(Text, nullable=False)
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
    # The last allocated ``chat_messages.conversation_sequence``. Owned by the
    # database trigger that allocates positions under this row's lock; never
    # written by application code. Positions are never reused.
    last_message_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0", server_onupdate=FetchedValue()
    )

    user: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )


CHAT_MESSAGE_SEARCH_EXPRESSION = ("to_tsvector('english'::regconfig, CASE WHEN heard_chars IS NULL THEN content "
                                  "ELSE left(content, heard_chars) END)")


class ChatMessage(Base, TimestampMixin):
    """A single turn within a conversation (a user prompt or an assistant reply)."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("turn_id", "role", name="uq_chat_messages_turn_role"),
        UniqueConstraint(
            "conversation_id", "conversation_sequence", name="uq_chat_messages_conversation_sequence"
        ),
        CheckConstraint("conversation_sequence >= 1", name="ck_chat_messages_conversation_sequence"),
        # Interrupted replies, for observation recovery (services/observation_adapters._sweep_heard).
        Index("ix_chat_messages_heard", "created_at", postgresql_where=text("heard_chars IS NOT NULL")),
        # Transcript search (stage 5, services/transcript_search.py): what was said, as far as it was heard.
        Index("ix_chat_messages_search", "search_tsv", postgresql_using="gin"),
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
    # Spoken replies cut off by the user: how many characters of `content` they
    # heard before interrupting (null = all of it, or not a voice reply).
    # `content` keeps the full generated text; history and memory read the
    # heard part via services/heard.py.
    heard_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # What transcript search matches (stage 5): the heard part only, kept by Postgres itself. Its last word may be
    # half heard; services/transcript_search.py checks every hit against the text snapped to a whole word.
    search_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed(CHAT_MESSAGE_SEARCH_EXPRESSION, persisted=True), nullable=True, deferred=True)
    # The message's durable position in its conversation ledger
    # (dialogue-message-order-v0.1). Allocated by a BEFORE INSERT trigger under
    # the conversation row lock -- never by the application, never reused, and
    # immutable afterwards. Timestamps alone cannot order a conversation.
    conversation_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=FetchedValue()
    )
    # The phone app's clock when the user hit send (a message can wait on the phone while the backend is
    # unreachable). Client-reported, never in the future; information only, never used for ordering.
    written_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When her chat worker picked this user message up (the second tick). "She started on it", not "she replied".
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Her messages: when she actually said it aloud on a live voice session (then it isn't pushed to the phone).
    spoken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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


class PhoneDevice(Base, TimestampMixin):
    """A phone app signed in with a bearer session, and where its push notifications go (FCM).

    One per session: it is deleted with its session (sign-out) and when FCM says the token is dead."""

    __tablename__ = "phone_devices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # services/auth.session_key_hash of the bearer token: the device dies with its session.
    session_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    fcm_token: Mapped[str] = mapped_column(String(4096), unique=True, nullable=False)
    device_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notifications_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"))
    # Off: the push says only "Jophiel sent you a message". On: her words (trimmed) pass through Google.
    show_text: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    # A message notification went out since the last "clear": the next time the user is present, clear it.
    needs_clear: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)


class PushDelivery(Base, TimestampMixin):
    """Transactional outbox for phone pushes (services/push.py, workers/push.py).

    ``message``: one of her messages, for one device; sent only if, when it is due, the user isn't present
    anywhere and she didn't say it aloud. ``clear``: tell a device to take its notifications down."""

    __tablename__ = "push_deliveries"
    __table_args__ = (
        UniqueConstraint("assistant_message_id", "device_id", name="uq_push_delivery_message_device"),
        Index("ix_push_deliveries_due", "next_attempt_at", postgresql_where=text("status IN ('pending', 'sending')")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # message | clear
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("phone_devices.id", ondelete="CASCADE"), index=True, nullable=False
    )
    assistant_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), index=True, nullable=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # pending -> sending -> sent | dropped (not needed: present, spoken, switched off) | failed (gave up)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# --------------------------------------------------------------------------- #
# Time a memory is about (docs/COLUMBINA_MEMORY_SYSTEM_IMPLEMENTATION_PLAN.md §4): the user's own words, kept,
# and the interval code read them as, on the user's calendar when it was said. A fact's time is when it
# became true ("moved last month"), an episode's when it happened, a plan's when it's meant to happen.
# --------------------------------------------------------------------------- #

# day: one day; days: a few named days ("this weekend"); unknown: the words couldn't be placed (no bounds);
# repeating: a schedule ("Thursdays at 7pm", in time_repeats).
TIME_PRECISIONS = ("day", "days", "week", "month", "season", "year", "unknown", "repeating")


class TimeFieldsMixin:
    # The time words exactly as the user said them ("in December", "last month"): never dropped.
    time_words: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Half-open [start, end), local midnights of the user's zone; null when it couldn't be placed.
    time_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    time_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    time_precision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Part of the date wasn't said and was chosen (usually the year of "in December").
    time_inferred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # The zone it was read in; null when the client never said (read as UTC, and shown as unknown).
    time_zone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # A schedule: {"repeats": "weekly", "weekdays": ["thu"], "local_time": "19:00"}.
    time_repeats: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    # When the words were said (their message's time): what "last month" was read from, never a later recall.
    time_said_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def _time_checks(table: str) -> tuple:
    precisions = ", ".join(f"'{p}'" for p in TIME_PRECISIONS)
    return (
        CheckConstraint("(time_start IS NULL) = (time_end IS NULL) AND (time_start IS NULL OR time_start < time_end)",
                        name=f"ck_{table}_time_bounds"),
        CheckConstraint(f"time_precision IS NULL OR time_precision IN ({precisions})",
                        name=f"ck_{table}_time_precision"),
        # Any reading keeps its words; one with bounds says how precise they are.
        CheckConstraint("(time_words IS NULL) = (time_precision IS NULL) AND (time_words IS NULL) = (time_said_at IS NULL) "
                        "AND (time_words IS NULL OR btrim(time_words) <> '')",
                        name=f"ck_{table}_time_words"),
        CheckConstraint("time_start IS NULL OR time_precision <> 'unknown'", name=f"ck_{table}_time_unknown"),
        CheckConstraint("(time_repeats IS NOT NULL) = (time_precision IS NOT DISTINCT FROM 'repeating')",
                        name=f"ck_{table}_time_repeats"),
    )


MEMORY_ENTITY_KINDS = ("person", "pet", "place", "organization", "activity", "thing", "other")


class MemoryEntity(Base):
    """Someone or something the user's memories are about: their sister, their dog, a pottery class. Claims
    point at it (``semantic_memories.entity_id``), plans and episodes link to it (``memory_entity_links``), so
    "my sister" finds what's known about her and a renamed dog keeps everything else said about it. A new
    mention is a new entity unless the message makes clear it's the same one: two of the same name stay two
    (recall says it's ambiguous), and nothing merges them on similar wording. ``name`` follows its current
    name claim; earlier names stay in ``metadata.names``."""

    __tablename__ = "memory_entities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What it is to the user, in their words: "sister", "dog", "childhood cat", "partner".
    relation: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict,
                                            server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("kind IN ({})".format(", ".join(f"'{k}'" for k in MEMORY_ENTITY_KINDS)),
                        name="ck_memory_entities_kind"),
        CheckConstraint("status IN ('active', 'deleted')", name="ck_memory_entities_status"),
        CheckConstraint("coalesce(btrim(name), '') <> '' OR coalesce(btrim(relation), '') <> ''",
                        name="ck_memory_entities_named"),
        UniqueConstraint("id", "user_id", name="uq_memory_entities_id_user"),
        Index("ix_memory_entities_user", "user_id", "status"),
    )


class SemanticMemory(Base, TimestampMixin, TimeFieldsMixin):
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
        # Recall references check the owner in the key (docs/COLUMBINA_MEMORY_SESSION3_RECALL_SPEC.md §10).
        UniqueConstraint("id", "user_id", name="uq_semantic_memories_id_user"),
        ForeignKeyConstraint(["entity_id", "user_id"], ["memory_entities.id", "memory_entities.user_id"],
                             ondelete="SET NULL (entity_id)", name="fk_semantic_memories_entity"),
        Index("ix_semantic_memories_entity", "entity_id", postgresql_where=text("entity_id IS NOT NULL")),
        CheckConstraint("attribute IS NULL OR btrim(attribute) <> ''", name="ck_semantic_memory_attribute"),
        *_time_checks("semantic_memory"),
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
    # How often the user said it (each restatement counts); never a change of what it says.
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # The content version: it moves only when what the fact says changes (wording, value, entity, time), so a
    # restatement is never a changed premise for what depends on it.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    # A claim (stage 3): one assertion about one entity's attribute. Older facts have none of these and may hold
    # several claims in one sentence; they're split into claims only when the user next changes them.
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    attribute: Mapped[str | None] = mapped_column(String(120), nullable=True)  # "name", "lives_in", "dislikes"
    value: Mapped[str | None] = mapped_column(Text, nullable=True)  # "Pretzel", "Porto", "rain"
    qualifier: Mapped[str | None] = mapped_column(Text, nullable=True)  # "as a child", "at work"
    # It tells how things were, not how they are ("lived in Lisbon for six years"): true, but not current.
    historical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    user: Mapped["User"] = relationship(back_populates="semantic_memories")


class EpisodicMemory(Base, TimestampMixin, TimeFieldsMixin):
    """A time- and context-bound experience retained with its provenance. ``event_at`` is when it happened as
    far as code knows (a game stretch's end; for a told episode, the start of its time reading, else when it
    was told); the ``time_*`` fields keep what the user said about when."""

    __tablename__ = "episodic_memories"
    __table_args__ = (
        CheckConstraint("emotional_significance BETWEEN 0 AND 1", name="ck_episodic_memory_emotion"),
        CheckConstraint("importance BETWEEN 0 AND 1", name="ck_episodic_memory_importance"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_episodic_memory_confidence"),
        CheckConstraint("status IN ('active', 'consolidated', 'superseded', 'deleted')", name="ck_episodic_memory_status"),
        UniqueConstraint("id", "user_id", name="uq_episodic_memories_id_user"),
        *_time_checks("episodic_memory"),
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


# ---------------------------------------------------------------------------
# Conversation ledger position (dialogue-message-order-v0.1)
# ---------------------------------------------------------------------------

# Every append locks the owned conversation row (the UPDATE takes it), bumps
# its counter, and stamps the message in the same statement, so concurrent
# appends serialize and a position is never duplicated or reused. FOR NO KEY
# UPDATE semantics (a plain UPDATE of a non-key column) cannot deadlock with
# the KEY SHARE locks foreign-key inserts take on the same row.
CHAT_MESSAGE_SEQUENCE_DDL = """
CREATE OR REPLACE FUNCTION chat_messages_assign_sequence() RETURNS trigger AS $$
DECLARE
    allocated BIGINT;
BEGIN
    IF NEW.conversation_sequence IS NOT NULL THEN
        RAISE EXCEPTION 'conversation_sequence is allocated by the database' USING ERRCODE = 'check_violation';
    END IF;
    UPDATE conversations SET last_message_sequence = last_message_sequence + 1
        WHERE id = NEW.conversation_id
        RETURNING last_message_sequence INTO allocated;
    IF allocated IS NULL THEN
        RAISE EXCEPTION 'chat message conversation does not exist' USING ERRCODE = 'foreign_key_violation';
    END IF;
    NEW.conversation_sequence := allocated;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION chat_messages_position_guard() RETURNS trigger AS $$
BEGIN
    IF NEW.conversation_sequence IS DISTINCT FROM OLD.conversation_sequence
       OR NEW.conversation_id IS DISTINCT FROM OLD.conversation_id THEN
        RAISE EXCEPTION 'a chat message keeps its conversation position' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER chat_messages_sequence BEFORE INSERT ON chat_messages
    FOR EACH ROW EXECUTE FUNCTION chat_messages_assign_sequence();
CREATE TRIGGER chat_messages_position_immutable BEFORE UPDATE ON chat_messages
    FOR EACH ROW EXECUTE FUNCTION chat_messages_position_guard();
"""

event.listen(ChatMessage.__table__, "after_create", DDL(CHAT_MESSAGE_SEQUENCE_DDL).execute_if(dialect="postgresql"))


# --- Desktop agent (docs/DESKTOP_AGENT_IMPLEMENTATION_PLAN.md §6, §17) -------

DESKTOP_SESSION_STATES = ("starting", "running", "paused", "manual", "disconnected", "ended", "failed")
DESKTOP_TERMINAL_STATES = ("ended", "failed")


class DesktopEnvironment(Base):
    """A controlled desktop (v1: the game VM) owned by one user.

    Connection health and session state are separate: an environment can be
    online with no session. Stage B adds the controller's device credential and
    pairing; until then environments are registered directly by their owner.
    """

    __tablename__ = "desktop_environments"

    @property
    def capture_confirmed(self) -> bool:
        """The connected controller has acknowledged the current capture setting."""
        return self.status == "online" and self.capture_confirmed_epoch == self.capture_epoch

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    hosting_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="local_vm", server_default="local_vm")
    guest_os: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Trusted policy: the profile ids this environment may run. Never model-supplied.
    allowed_profile_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    policy_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="offline", server_default="offline")
    controller_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Device credential from pairing: only its SHA-256 is stored. Re-pairing
    # replaces it, which locks the previous controller out.
    controller_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    paired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    controller_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Screen capture (plan §10, §23): off until the owner allows it. Every
    # change bumps ``capture_epoch``; frames carry the epoch they were taken
    # under, and the controller reports the epoch it is obeying
    # (``capture_confirmed_epoch``), so "off" can be shown as confirmed.
    capture_gate: Mapped[str] = mapped_column(String(16), nullable=False, default="blocked", server_default="blocked")
    capture_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    capture_confirmed_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # "desktop" (the VM's whole screen) or "game_window" (the profile's game only).
    capture_scope: Mapped[str] = mapped_column(String(16), nullable=False, default="desktop", server_default="desktop")
    # The controller's last reason for having or not having a picture.
    target_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # The game's input mode the controller last reported ("playing", "menu", ...).
    input_context: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Security monitoring (services/security_activity.py): the owner's own switch, apart from capture and input.
    # Each switch-on bumps ``security_epoch``: the VM then starts reading at its logs' end, so nothing from the
    # time it was off is ever collected. ``security_changed_at``: when it was last switched either way.
    security_monitoring: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                                      server_default=text("false"))
    security_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    security_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Her mind's progress through this machine's security observations (services/mind_security.py).
    security_seen_through: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    security_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The owner's "Allow Jophiel to start sessions" switch (docs/VM_CONNECTION_PLAN.md, step 3): off for every
    # pairing until the owner turns it on. Starts the owner makes never need it; reconnecting starts nothing.
    auto_start_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                                     server_default=text("false"))
    auto_start_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # What the controller last reported about its machine (desktop_controller.link.ControllerStatus: name, OS,
    # capture / input availability and why not, start at login). Shown, never trusted for anything.
    controller_status: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    controller_status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        # game_pack: a game pack service on this machine (stage G), not a VM.
        CheckConstraint(
            "hosting_kind IN ('local_vm', 'cloud_vm', 'game_pack')", name="ck_desktop_environments_hosting_kind"
        ),
        CheckConstraint("status IN ('offline', 'online')", name="ck_desktop_environments_status"),
        CheckConstraint(
            "capture_gate IN ('allowed', 'blocked', 'manual_auth')", name="ck_desktop_environments_capture_gate"
        ),
        CheckConstraint(
            "capture_scope IN ('desktop', 'game_window')", name="ck_desktop_environments_capture_scope"
        ),
    )


SECURITY_COVERAGE_STATUSES = ("covered", "gap", "reset", "unavailable", "off")
SECURITY_INVESTIGATION_STATUSES = ("open", "concluded", "closed")


class SecurityCoverage(Base):
    """What the security collector could vouch for, per channel and interval (the VM's clock, UTC):
    ``covered`` (every record then was read), ``gap`` (overwritten before it was read), ``reset`` (the log was
    cleared), ``unavailable`` (the channel couldn't be read), ``off`` (monitoring was switched off; channel ``*``,
    open-ended until switched on). Adjacent intervals of the same status are merged, so rows grow with changes,
    not with reads. Time no row covers is unverified (the collector was away, or hasn't reported yet)."""

    __tablename__ = "security_coverage"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("desktop_environments.id", ondelete="CASCADE"), nullable=False)
    channel: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    stream: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    first_record: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_record: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(f"status IN ({', '.join(repr(s) for s in SECURITY_COVERAGE_STATUSES)})",
                        name="ck_security_coverage_status"),
        CheckConstraint("end_at IS NULL OR end_at >= start_at", name="ck_security_coverage_order"),
        Index("ix_security_coverage_env_channel_start", "environment_id", "channel", "start_at"),
    )


class SecurityInvestigation(Base):
    """One question her mind is looking into about one machine and time window (services/mind_security.py):
    signals from the source, her periodic look, and coverage gaps that bear on it are grouped here. A change
    worth another look (new evidence, a gap) bumps ``revision``; ``considered_revision`` is the one her last
    thought read. Open while she has an unresolved question; ``concluded`` once she has thought it through
    (reopened by relevant new evidence); ``closed`` when nothing changes for a day."""

    __tablename__ = "security_investigations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("desktop_environments.id", ondelete="CASCADE"), nullable=False)
    # Where she'd say what she found: the owner's latest conversation when it opened (owned, never guessed later).
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", server_default="open")
    opened_by: Mapped[str] = mapped_column(String(16), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    considered_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # What brought it here, in order: [{"kind": signal|periodic|gap|evidence, "at", "text", "refs": [obs ids]}].
    signals: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    # Observation ids that started or changed it (the timeline itself is read from observation by window).
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    last_thought_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    last_thought_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    conclusion_kind: Mapped[str | None] = mapped_column(String(12), nullable=True)
    conclusion: Mapped[str | None] = mapped_column(Text, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(f"status IN ({', '.join(repr(s) for s in SECURITY_INVESTIGATION_STATUSES)})",
                        name="ck_security_investigations_status"),
        CheckConstraint("window_end >= window_start", name="ck_security_investigations_window"),
        Index("ix_security_investigations_env_status", "environment_id", "status"),
        Index("ix_security_investigations_conversation", "conversation_id"),
    )


class DesktopPairing(Base):
    """A one-time pairing code for an environment (plan §10: expiring,
    single-use, bound to the user who created it).

    The relay needs the code itself to check the controller's proof, so it is
    stored until the pairing is used or dies, then cleared. ``code_lookup`` is
    how the relay finds the row without the controller ever sending the code.
    """

    __tablename__ = "desktop_pairings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("desktop_environments.id", ondelete="CASCADE"), index=True, nullable=False
    )
    code_lookup: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DesktopSession(Base):
    """One gameplay session: lifecycle, input ownership, and the current plan.

    ``state_revision`` is the compare-and-swap counter for lifecycle changes;
    ``execution_generation`` fences input (bumped by anything that stops it);
    ``plan_revision`` is bumped only by a plan replace, never by an append.
    """

    __tablename__ = "desktop_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("desktop_environments.id", ondelete="CASCADE"), index=True, nullable=False
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    profile_id: Mapped[str] = mapped_column(String(32), nullable=False)
    profile_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    state_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    reason_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    control_owner: Mapped[str] = mapped_column(String(16), nullable=False, default="none", server_default="none")
    execution_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    plan: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    plan_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_step_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    current_step_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    driver_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    commentary_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="occasional", server_default="occasional"
    )
    # When a goal is done: "continue" = Columbina picks the next goal herself;
    # "stop" = she says so and the session pauses.
    autonomy: Mapped[str] = mapped_column(String(16), nullable=False, default="continue", server_default="continue")
    # Explore and learn (stage H3, services/game_challenges.py): after a goal she picks her
    # own next challenge, and the cap on goals she picks herself doesn't apply.
    explore: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    # The goal in her working memory this session's plan serves (services/memory_manager.py). Replacing
    # the plan never changes the goal; closing or suspending the goal pauses the session. ``plan["goal_id"]``
    # is a copy for readers that only see the plan.
    goal_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    max_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    # Who started it: "user" (the dashboard / API) or "jophiel" (her own start). None: before this was recorded.
    started_by: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Retried start requests (e.g. a repeated tool call) return the same session.
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "state IN ('starting', 'running', 'paused', 'manual', 'disconnected', 'ended', 'failed')",
            name="ck_desktop_sessions_state",
        ),
        CheckConstraint("control_owner IN ('none', 'gameplay', 'manual')", name="ck_desktop_sessions_control_owner"),
        CheckConstraint(
            "commentary_mode IN ('quiet', 'occasional', 'commentary')", name="ck_desktop_sessions_commentary_mode"
        ),
        CheckConstraint("autonomy IN ('continue', 'stop')", name="ck_desktop_sessions_autonomy"),
        CheckConstraint("started_by IN ('user', 'jophiel')", name="ck_desktop_sessions_started_by"),
        ForeignKeyConstraint(["goal_item_id", "owner_user_id"], ["working_items.id", "working_items.user_id"],
                             ondelete="SET NULL (goal_item_id)", name="fk_desktop_sessions_goal_item"),
        Index("ix_desktop_sessions_goal_item", "goal_item_id", postgresql_where=text("goal_item_id IS NOT NULL")),
        # One live session per environment: concurrent starts cannot both win.
        Index(
            "uq_desktop_sessions_active_environment",
            "environment_id",
            unique=True,
            postgresql_where=text("state NOT IN ('ended', 'failed')"),
        ),
        Index(
            "uq_desktop_sessions_owner_idempotency",
            "owner_user_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )


class AgentEventJob(Base):
    """Something that should wake Jophiel without a user message (plan §8.2).

    One *pending* job per ``(source, dedupe_key)`` (e.g. per desktop session):
    new events append their reason to it instead of queueing a second turn. A
    worker claims it with a lease, runs one background turn, and marks it
    done. Postgres is authoritative; Redis only wakes the worker.
    """

    __tablename__ = "agent_event_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    reasons: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'done', 'failed')", name="ck_agent_event_jobs_status"
        ),
        Index(
            "uq_agent_event_jobs_pending", "source", "dedupe_key",
            unique=True, postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_agent_event_jobs_claim", "status", "not_before"),
    )


class DesktopSessionEvent(Base):
    """Bounded, persisted lifecycle/plan history. High-rate decision events
    never land here (plan §17 Events)."""

    __tablename__ = "desktop_session_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("desktop_sessions.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    state_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_desktop_session_events_session_id", "session_id", "id"),)


# --------------------------------------------------------------------------- #
# Game learning: experience -> reflection -> lessons / proposals
# --------------------------------------------------------------------------- #


class MindThought(Base):
    """One thought of her mind (docs/COLUMBINA_MIND_DESIGN.md): a react, light or
    hard thought about a moment of play, and what she said because of it. Not
    chat history (that stays what was said) and not memory (a thought can be
    wrong). Kept a few days; shown in its own panel on the chat page."""

    __tablename__ = "mind_thoughts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("desktop_sessions.id", ondelete="SET NULL"), nullable=True
    )
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    said: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="done", server_default="done")
    topic: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # The kind of thinking (why, next, you, judgement, feeling, memory; think.KINDS). Null for hard thoughts.
    kind: Mapped[str | None] = mapped_column(String(12), nullable=True)
    # Hard thoughts (app/ai/mind/hard.py): what started it (failing, surprise, big, handoff, quiet), the kind
    # of conclusion (decision, guess, question, intention, feeling), her private reasoning (panel only), a tip
    # for her hands, a question for the user, and why it was dropped (status "dropped").
    trigger: Mapped[str | None] = mapped_column(String(12), nullable=True)
    conclusion_kind: Mapped[str | None] = mapped_column(String(12), nullable=True)
    thinking: Mapped[str | None] = mapped_column(Text, nullable=True)
    for_hands: Mapped[str | None] = mapped_column(Text, nullable=True)
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    dropped_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Which source and activity it is about ("minecraft" + a session id, "conversation" + a conversation id).
    # No foreign key: the activity is a different table per source. session_id stays for game thoughts.
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    activity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # The earlier conclusion it says it carries out (a short id from her effort timeline), as the model said
    # it; code never infers it (docs/learning-system/AUDIT-2026-11-04-learning-from-experience.md).
    follows: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Her judgment of the effort when she had one to judge: progress (yes/no/unclear), and when it isn't, the
    # cause she favours, a check, what it would support and what it would leave unknown (stage 2).
    assessment: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("level IN ('react', 'light', 'quiet', 'hard')", name="ck_mind_thoughts_level"),
        CheckConstraint("status IN ('done', 'stale', 'unfinished', 'dropped')", name="ck_mind_thoughts_status"),
        Index("ix_mind_thoughts_user_created", "user_id", "created_at"),
        Index("ix_mind_thoughts_activity_created", "user_id", "activity_id", "created_at"),
    )


class GameExperience(Base):
    """One thing that happened in a game session, recorded by code, never by a
    model: a skill's outcome, something the pack noticed, the lessons in her
    hands' prompt when a run started. Append-only; the raw material reflection
    learns from, and what measuring learning counts."""

    __tablename__ = "game_experiences"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("desktop_sessions.id", ondelete="CASCADE"), nullable=False
    )
    game: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    event_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('skill_outcome', 'notice', 'lessons_applied')", name="ck_game_experiences_kind"
        ),
        UniqueConstraint("user_id", "event_key", name="uq_game_experiences_event"),
        Index("ix_game_experiences_session", "session_id", "id"),
        Index("ix_game_experiences_user_game", "user_id", "game", "id"),
    )


class GamePlayerLink(Base):
    """Which player in a game is the user ("tearDrop12" in Minecraft), so she knows who "me" is
    whoever else is online, and remembers play with the user by name. Set when the user says so."""

    __tablename__ = "game_player_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    game: Mapped[str] = mapped_column(String(32), nullable=False)
    player_name: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("user_id", "game", name="uq_game_player_links_user_game"),)


class GameLesson(Base):
    """Something she learned about playing a game: when <situation>, <advice>.

    Level 1 learning, applied automatically: a ``skill`` lesson (how an action
    behaves) goes to her hands' prompt; a ``plan`` lesson (what to do, in what
    order, when) goes to Jophiel's game summary, where she sets goals. A lesson
    starts as ``trial`` and earns trust from evidence: each reflection that
    finds a later session supporting it adds support, contradicting it adds
    contradiction. Every count is backed by experience ids, so a model's guess
    never becomes a lesson on its own."""

    __tablename__ = "game_lessons"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    game: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(96), nullable=False)
    situation: Mapped[str] = mapped_column(Text, nullable=False)
    advice: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(String(8), nullable=False, default="skill", server_default="skill")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="trial", server_default="trial")
    support_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    contradict_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # [{"session_id", "verdict": "new"|"supports"|"contradicts", "experience_ids": [...], "at"}]
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("status IN ('trial', 'confirmed', 'retired')", name="ck_game_lessons_status"),
        CheckConstraint("level IN ('skill', 'plan')", name="ck_game_lessons_level"),
        UniqueConstraint("user_id", "game", "normalized_key", name="uq_game_lessons_key"),
    )


class GameProposal(Base):
    """Level 3 learning: a change to the game code she suggests (a skill that
    fails for reasons she can't fix by choosing differently, a missing skill).
    Never applied by the app: the user reviews it and hands it to a coding
    agent. Repeats add evidence to the same proposal instead of a new one."""

    __tablename__ = "game_proposals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    game: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(96), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    area: Mapped[str] = mapped_column(String(64), nullable=False)
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion: Mapped[str] = mapped_column(Text, nullable=False)
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # [{"session_id", "experience_ids": [...], "examples": [outcome lines], "at"}]
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", server_default="open")
    user_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'accepted', 'implemented', 'rejected')", name="ck_game_proposals_status"
        ),
        UniqueConstraint("user_id", "game", "normalized_key", name="uq_game_proposals_key"),
    )


class GameSkillAttempt(Base):
    """One time she tried to teach herself a move (stage H1, services/game_practice.py):
    the task, every program she wrote for it with what it did and the critic's
    verdict, and how it ended. Started only from a turn where the user spoke
    (she may offer; only the user says go). Kept until the user wipes game data;
    the programs that worked are what the skill library (H2) will build on."""

    __tablename__ = "game_skill_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("desktop_sessions.id", ondelete="SET NULL"), nullable=True
    )
    game: Mapped[str] = mapped_column(String(32), nullable=False)
    task: Mapped[str] = mapped_column(Text, nullable=False)
    # "offer": she offered and the user said yes; "user": the user asked for it; "trial": the execution of a
    # bounded experiment her mind selected (services/learning_trials.py; its trial links back to it).
    origin: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="queued", server_default="queued")
    # [{"n", "approach", "code", "program": {status, reason, value, calls, log, seconds},
    #   "facts": {inventory, moved_m, ...}, "verdict": {"success", "reason"}}]
    tries: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    code: Mapped[str | None] = mapped_column(Text, nullable=True)  # the program that worked
    stop_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'learned', 'failed', 'stopped')",
                        name="ck_game_skill_attempts_status"),
        # self: she started it herself (exploring, or a step no skill does; stage H3)
        CheckConstraint("origin IN ('offer', 'user', 'self', 'trial')", name="ck_game_skill_attempts_origin"),
        # One attempt at a time per session: her body can only learn one thing at once.
        Index("uq_game_skill_attempts_active_session", "session_id", unique=True,
              postgresql_where=text("status IN ('queued', 'running')")),
        Index("ix_game_skill_attempts_user_created", "user_id", "created_at"),
    )


class GameChallenge(Base):
    """A challenge she set herself while exploring (stage H3, services/game_challenges.py):
    proposed when a goal is done, active once she makes it her goal, then done, given up
    (she set another goal), replaced (the user gave one) or expired (the session ended)."""

    __tablename__ = "game_challenges"
    __table_args__ = (
        CheckConstraint("status IN ('proposed','active','done','given_up','replaced','expired')",
                        name="ck_game_challenges_status"),
        Index("ix_game_challenges_user_game", "user_id", "game", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("desktop_sessions.id", ondelete="SET NULL"), nullable=True
    )
    game: Mapped[str] = mapped_column(String(32), nullable=False)
    challenge: Mapped[str] = mapped_column(Text, nullable=False)
    why: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    aim: Mapped[str] = mapped_column(String(16), nullable=False)  # discover | for_us
    check: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # a StepCheck, when one fits
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="proposed", server_default="proposed")
    plan_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the plan that is this challenge
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    learned: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # names of moves learned on the way
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GameLearnedSkill(Base):
    """A move she taught herself (stage H2): the program from an attempt that worked,
    kept per user so her hands can use it in any later session (services/game_skills.py).
    Usable as soon as it's learned; the user can switch it off or delete it on the
    Learning page. Learning the same move again replaces the program. Three misses in
    a row switch it off by themselves; a learned program is tidied once, and the tidied
    copy goes back to ``original_code`` if it misses before it ever worked."""

    __tablename__ = "game_learned_skills"
    __table_args__ = (UniqueConstraint("user_id", "game", "name", name="uq_game_learned_skills_user_game_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    game: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)  # the skill her hands pick: learned_<words>
    task: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("game_skill_attempts.id", ondelete="SET NULL"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    uses: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    successes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    misses_in_row: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    off_reason: Mapped[str | None] = mapped_column(Text, nullable=True)  # why it switched itself off
    original_code: Mapped[str | None] = mapped_column(Text, nullable=True)  # as learned, when ``code`` is tidied
    tidy_note: Mapped[str | None] = mapped_column(Text, nullable=True)  # what the tidy changed, or why it was undone
    tidy_proven: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class GameReflectionJob(Base):
    """One reflection per stretch of play: the experiences after
    ``from_experience_id`` up to ``up_to_experience_id``. Queued in the
    transaction that takes the session out of play (paused, offline, ended),
    since a session is rarely ended outright. Leased like the other job
    tables; Postgres is authoritative."""

    __tablename__ = "game_reflection_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("desktop_sessions.id", ondelete="CASCADE"), nullable=False
    )
    game: Mapped[str] = mapped_column(String(32), nullable=False)
    # The stretch reflected on: experiences with from < id <= up_to (from 0 = the session's start).
    from_experience_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    up_to_experience_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    claim_token: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What the reflection did: counts and ids, for the learning page and reports.
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'done', 'skipped', 'failed')", name="ck_game_reflection_jobs_status"
        ),
        Index("ix_game_reflection_jobs_claim", "status", "created_at"),
        UniqueConstraint("session_id", "up_to_experience_id", name="uq_game_reflection_jobs_segment"),
    )


# --------------------------------------------------------------------------- #
# Working memory (docs/COLUMBINA_MEMORY_SESSION1_SPEC.md §4; services/memory_manager.py):
# contexts, goals that outlive their steps, commitments, intentions, questions, recent events,
# and where each came from. General: a game is one source of contexts and events among others.
# Rows reference each other through composite keys that carry user_id (or context_id), so nothing
# can point across users or contexts; those references are NO ACTION, so only the account cascade
# and the reset remove a referenced row.
# --------------------------------------------------------------------------- #

# The column named "text" shadows sqlalchemy.text inside these class bodies.
sa_text = text

MEMORY_CONTEXT_KINDS = ("general", "activity", "project", "world")
MEMORY_CONTEXT_STATUSES = ("active", "paused", "closed")
WORKING_ITEM_KINDS = ("goal", "intention", "commitment", "question", "hypothesis", "decision", "plan", "constraint")
# What may point at the goal it serves (parent_id): a sub-goal, and her own items and the user's rules for it
# (docs/learning-system/AUDIT-2026-11-04-learning-from-experience.md: an outcome gathers the approaches taken for it).
WORKING_ITEM_SERVES = ("goal", "decision", "intention", "hypothesis", "question", "constraint")
# Per kind: (open statuses, closed statuses).
WORKING_ITEM_STATUSES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "goal": (("active", "suspended"), ("completed", "abandoned")),
    "commitment": (("open", "needs_review"), ("fulfilled", "cancelled")),
    "intention": (("active",), ("done", "dropped", "lapsed")),
    "question": (("open",), ("answered", "dropped")),
    # Hers, from her own thinking (docs/COLUMBINA_THOUGHT_MEMORY_SPEC.md): a suspicion, and what she decided.
    "hypothesis": (("open",), ("supported", "disproved", "unresolved")),
    "decision": (("active",), ("superseded", "withdrawn")),
    # Something the user means to do at a time ("visit Nora in December"): its time passing without word of
    # it makes it needs_review, never happened.
    "plan": (("planned", "needs_review"), ("happened", "cancelled")),
    # A rule the user gave for an outcome, in their words ("this is your task, don't follow me"): in force until
    # lifted. Which layer a correction changes is her mind's call; this is where a lasting rule goes.
    "constraint": (("active",), ("lifted",)),
}
WORKING_ITEM_CLOSED = tuple(sorted({s for _, closed in WORKING_ITEM_STATUSES.values() for s in closed}))
# basis: what she read when she thought it; restated: she reached it again. Neither is support.
MEMORY_EVIDENCE_ROLES = ("origin", "support", "resolution", "correction", "basis", "restated")


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


_WORKING_ITEM_STATUS_CHECK = " OR ".join(
    f"(kind = '{kind}' AND status IN ({_in(opened + closed)}))"
    for kind, (opened, closed) in WORKING_ITEM_STATUSES.items()
)


class MemoryContext(Base):
    """Where a memory belongs: a world, an activity, a project, or the user's general life (the default,
    one per user). Normal use only closes a context; deleting one fails while anything references it."""

    __tablename__ = "memory_contexts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    # Who can recognise it again (e.g. "minecraft_pack"), and that source's identity for it.
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    external_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(f"kind IN ({_in(MEMORY_CONTEXT_KINDS)})", name="ck_memory_contexts_kind"),
        CheckConstraint(f"status IN ({_in(MEMORY_CONTEXT_STATUSES)})", name="ck_memory_contexts_status"),
        CheckConstraint("btrim(label) <> ''", name="ck_memory_contexts_label"),
        CheckConstraint("(status = 'closed') = (closed_at IS NOT NULL)", name="ck_memory_contexts_closed"),
        CheckConstraint(
            "kind <> 'general' OR (source IS NULL AND external_ref IS NULL AND parent_id IS NULL "
            "AND status <> 'closed')", name="ck_memory_contexts_general"),
        CheckConstraint("external_ref IS NULL OR source IS NOT NULL", name="ck_memory_contexts_ref"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_memory_contexts_parent_self"),
        UniqueConstraint("id", "user_id", name="uq_memory_contexts_id_user"),
        ForeignKeyConstraint(["parent_id", "user_id"], ["memory_contexts.id", "memory_contexts.user_id"],
                             name="fk_memory_contexts_parent"),
        Index("uq_memory_contexts_general", "user_id", unique=True, postgresql_where=sa_text("kind = 'general'")),
        Index("uq_memory_contexts_ref", "user_id", "source", "external_ref", unique=True,
              postgresql_where=sa_text("external_ref IS NOT NULL")),
        Index("ix_memory_contexts_user_status", "user_id", "status", sa_text("last_active_at DESC")),
    )


class WorkingItem(Base, TimeFieldsMixin):
    """Unfinished business: a goal (with sub-goals), a commitment owed by one party to the other, her own
    intention, a question waiting for an answer, or, from her own thinking, a hypothesis or a decision.
    Stands on its own: its text, status, context and times are complete without its evidence links. Time
    and plan changes never close a goal."""

    __tablename__ = "working_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    context_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Who wants it (goal), who owes it (commitment), whose it is (intention), who asked (question).
    owner: Mapped[str] = mapped_column(String(16), nullable=False)
    owed_to: Mapped[str | None] = mapped_column(String(16), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    done_when: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "reported": the user said it; "inferred": she concluded it.
    epistemic: Mapped[str] = mapped_column(String(16), nullable=False)
    # Why she concluded it, from her own thinking (a thought handed to memory); null for what the user said.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # Which revision of each fact it was last checked against after that fact changed: {fact id: revision}.
    checked_facts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict,
                                                server_default=sa_text("'{}'::jsonb"))
    # A plan's own words, copied from the user's message ("going to see Nora in Lisbon in December").
    user_words: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The unresolved disagreement it depends on (a question her mind raised about a conflict): settling the
    # conflict closes it (services/memory_conflicts.settle).
    premise_conflict_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(["premise_conflict_id", "user_id"], ["memory_conflicts.id", "memory_conflicts.user_id"],
                             ondelete="SET NULL (premise_conflict_id)", name="fk_working_items_premise_conflict"),
        Index("ix_working_items_premise_conflict", "premise_conflict_id",
              postgresql_where=sa_text("premise_conflict_id IS NOT NULL")),
        CheckConstraint(f"kind IN ({_in(WORKING_ITEM_KINDS)})", name="ck_working_items_kind"),
        CheckConstraint(_WORKING_ITEM_STATUS_CHECK, name="ck_working_items_status"),
        CheckConstraint("owner IN ('user', 'columbina', 'shared')", name="ck_working_items_owner"),
        CheckConstraint("epistemic IN ('reported', 'inferred')", name="ck_working_items_epistemic"),
        CheckConstraint("btrim(text) <> ''", name="ck_working_items_text"),
        CheckConstraint(f"(closed_at IS NOT NULL) = (status IN ({_in(WORKING_ITEM_CLOSED)}))",
                        name="ck_working_items_closed"),
        CheckConstraint(f"parent_id IS NULL OR (kind IN ({_in(WORKING_ITEM_SERVES)}) AND parent_id <> id)",
                        name="ck_working_items_parent"),
        CheckConstraint("kind <> 'constraint' OR coalesce(btrim(user_words), '') <> ''",
                        name="ck_working_items_constraint_words"),
        CheckConstraint("(kind = 'commitment') = (owed_to IS NOT NULL)", name="ck_working_items_owed_to"),
        # Owed by one party to the other; "I'll remember to..." to herself is an intention.
        CheckConstraint("kind <> 'commitment' OR (owner IN ('user', 'columbina') AND owed_to IN ('user', 'columbina') "
                        "AND owner <> owed_to)", name="ck_working_items_commitment_parties"),
        CheckConstraint("kind <> 'intention' OR due_at IS NOT NULL", name="ck_working_items_intention_due"),
        CheckConstraint("revision >= 1", name="ck_working_items_revision"),
        CheckConstraint("kind <> 'plan' OR coalesce(btrim(user_words), '') <> ''", name="ck_working_items_plan_words"),
        *_time_checks("working_items"),
        UniqueConstraint("id", "user_id", name="uq_working_items_id_user"),
        UniqueConstraint("id", "context_id", name="uq_working_items_id_context"),
        ForeignKeyConstraint(["context_id", "user_id"], ["memory_contexts.id", "memory_contexts.user_id"],
                             name="fk_working_items_context"),
        ForeignKeyConstraint(["parent_id", "context_id"], ["working_items.id", "working_items.context_id"],
                             name="fk_working_items_parent"),
        Index("ix_working_items_user_status", "user_id", "status"),
        Index("ix_working_items_context_kind_status", "context_id", "kind", "status"),
        Index("ix_working_items_parent", "parent_id", postgresql_where=sa_text("parent_id IS NOT NULL")),
        Index("ix_working_items_due", "due_at", postgresql_where=sa_text("due_at IS NOT NULL AND closed_at IS NULL")),
    )


class MemoryEvent(Base):
    """Something meaningful that happened, in a context: short, one line, never a sensor sample. Where it
    came from is an evidence link, not a column."""

    __tablename__ = "memory_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    context_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    epistemic: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("epistemic IN ('observed', 'reported', 'inferred')", name="ck_memory_events_epistemic"),
        CheckConstraint("btrim(text) <> ''", name="ck_memory_events_text"),
        UniqueConstraint("id", "user_id", name="uq_memory_events_id_user"),
        ForeignKeyConstraint(["context_id", "user_id"], ["memory_contexts.id", "memory_contexts.user_id"],
                             name="fk_memory_events_context"),
        ForeignKeyConstraint(["item_id", "user_id"], ["working_items.id", "working_items.user_id"],
                             ondelete="SET NULL (item_id)", name="fk_memory_events_item"),
        Index("uq_memory_events_dedupe", "user_id", "dedupe_key", unique=True,
              postgresql_where=sa_text("dedupe_key IS NOT NULL")),
        Index("ix_memory_events_context", "context_id", sa_text("id DESC")),
        Index("ix_memory_events_user", "user_id", "id"),
    )


class MemoryManagerState(Base):
    """The manager's own bookkeeping per user, for the due checks and for inspection. Pending work is a
    query over working_items, never stored here."""

    __tablename__ = "memory_manager_state"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    focus_context_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    last_event_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    last_tick_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_decision: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(["focus_context_id", "user_id"], ["memory_contexts.id", "memory_contexts.user_id"],
                             ondelete="SET NULL (focus_context_id)", name="fk_memory_manager_state_focus"),
        Index("ix_memory_manager_state_next_check", "next_check_at"),
    )


class MemoryEvidence(Base):
    """Where a memory came from: one target (an item, an event, a fact or an entity) and one source, each a
    real, owner-checked foreign key. Deleting the source deletes the link and nothing else: the memory stands
    on its own. A fact's links (stage 3): ``origin`` where it was first said, ``restated``/``correction`` where
    its wording changed, ``support`` where it was said again unchanged."""

    __tablename__ = "memory_evidence"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=True)
    session_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("desktop_session_events.id", ondelete="CASCADE"), nullable=True)
    thought_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("mind_thoughts.id", ondelete="CASCADE"), nullable=True)
    experience_id: Mapped[int | None] = mapped_column(
        ForeignKey("game_experiences.id", ondelete="CASCADE"), nullable=True)
    observation_id: Mapped[int | None] = mapped_column(
        ForeignKey("observations.id", ondelete="CASCADE"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("num_nonnulls(item_id, event_id, fact_id, entity_id) = 1", name="ck_memory_evidence_target"),
        CheckConstraint("num_nonnulls(message_id, session_event_id, thought_id, experience_id, observation_id) = 1",
                        name="ck_memory_evidence_source"),
        CheckConstraint(f"role IN ({_in(MEMORY_EVIDENCE_ROLES)}) AND (role <> 'resolution' OR item_id IS NOT NULL)",
                        name="ck_memory_evidence_role"),
        ForeignKeyConstraint(["item_id", "user_id"], ["working_items.id", "working_items.user_id"],
                             ondelete="CASCADE", name="fk_memory_evidence_item"),
        ForeignKeyConstraint(["event_id", "user_id"], ["memory_events.id", "memory_events.user_id"],
                             ondelete="CASCADE", name="fk_memory_evidence_event"),
        ForeignKeyConstraint(["fact_id", "user_id"], ["semantic_memories.id", "semantic_memories.user_id"],
                             ondelete="CASCADE", name="fk_memory_evidence_fact"),
        ForeignKeyConstraint(["entity_id", "user_id"], ["memory_entities.id", "memory_entities.user_id"],
                             ondelete="CASCADE", name="fk_memory_evidence_entity"),
        UniqueConstraint("item_id", "event_id", "fact_id", "entity_id", "role", "message_id", "session_event_id",
                         "thought_id", "experience_id", "observation_id", name="uq_memory_evidence",
                         postgresql_nulls_not_distinct=True),
        Index("ix_memory_evidence_item", "item_id"),
        Index("ix_memory_evidence_event", "event_id"),
        Index("ix_memory_evidence_fact", "fact_id", postgresql_where=sa_text("fact_id IS NOT NULL")),
        Index("ix_memory_evidence_entity", "entity_id", postgresql_where=sa_text("entity_id IS NOT NULL")),
        Index("ix_memory_evidence_message", "message_id"),
        Index("ix_memory_evidence_session_event", "session_event_id"),
        Index("ix_memory_evidence_thought", "thought_id"),
        Index("ix_memory_evidence_experience", "experience_id"),
        Index("ix_memory_evidence_observation", "observation_id"),
    )



MEMORY_ENTITY_LINK_ROLES = ("participant", "place", "about", "reference")


class MemoryEntityLink(Base):
    """Who or what a plan or an episode involves: the user's sister in "visit Nora in December", the place it's
    at. One target (an item or an episode) per row, both owner-checked keys; it goes with either side."""

    __tablename__ = "memory_entity_links"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    episode_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("num_nonnulls(item_id, episode_id) = 1", name="ck_memory_entity_links_target"),
        CheckConstraint(f"role IN ({_in(MEMORY_ENTITY_LINK_ROLES)})", name="ck_memory_entity_links_role"),
        ForeignKeyConstraint(["entity_id", "user_id"], ["memory_entities.id", "memory_entities.user_id"],
                             ondelete="CASCADE", name="fk_memory_entity_links_entity"),
        ForeignKeyConstraint(["item_id", "user_id"], ["working_items.id", "working_items.user_id"],
                             ondelete="CASCADE", name="fk_memory_entity_links_item"),
        ForeignKeyConstraint(["episode_id", "user_id"], ["episodic_memories.id", "episodic_memories.user_id"],
                             ondelete="CASCADE", name="fk_memory_entity_links_episode"),
        UniqueConstraint("entity_id", "item_id", "episode_id", "role", name="uq_memory_entity_links",
                         postgresql_nulls_not_distinct=True),
        Index("ix_memory_entity_links_entity", "entity_id"),
        Index("ix_memory_entity_links_item", "item_id", postgresql_where=sa_text("item_id IS NOT NULL")),
        Index("ix_memory_entity_links_episode", "episode_id", postgresql_where=sa_text("episode_id IS NOT NULL")),
    )


# --------------------------------------------------------------------------- #
# Observation (docs/COLUMBINA_MEMORY_SESSION2_SPEC.md): what a source reported
# --------------------------------------------------------------------------- #

OBSERVATION_ACTORS = ("user", "columbina", "environment", "tool", "system")
# inferred: her own reasoning (a thought handed to memory), neither observed nor reported.
OBSERVATION_EPISTEMIC = ("observed", "reported", "inferred")
OBSERVATION_ORIGINS = ("user", "external", "system", "model")
OBSERVATION_SALIENCE = ("routine", "notable", "critical")
OBSERVATION_PROCESSING_STATUSES = ("pending", "retry", "applied", "skipped", "failed")
OBSERVATION_AVAILABILITY = ("unknown", "online", "offline")


class Observation(Base):
    """One report from a source, normalized: immutable once written (a trigger allows only clearing a
    reference whose target went away). The source's own record stays authoritative; this is its common
    shape, with no message bodies copied in. Its id is identity, not commit order."""

    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # Bound by the source when it knows (e.g. the world a skill ran in); else observation_bindings.
    context_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_instance: Mapped[str] = mapped_column(String(160), nullable=False)
    source_event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    actor: Mapped[str] = mapped_column(String(16), nullable=False)
    epistemic: Mapped[str] = mapped_column(String(16), nullable=False)
    content_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    salience: Mapped[str] = mapped_column(String(16), nullable=False)
    caused_by_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    outcome_of_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=True)
    delivery_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("message_deliveries.id", ondelete="CASCADE"), nullable=True)
    experience_id: Mapped[int | None] = mapped_column(
        ForeignKey("game_experiences.id", ondelete="CASCADE"), nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("desktop_sessions.id", ondelete="SET NULL"), nullable=True)
    thought_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("mind_thoughts.id", ondelete="CASCADE"), nullable=True)

    __table_args__ = (
        CheckConstraint(f"actor IN ({_in(OBSERVATION_ACTORS)})", name="ck_observations_actor"),
        CheckConstraint(f"epistemic IN ({_in(OBSERVATION_EPISTEMIC)})", name="ck_observations_epistemic"),
        CheckConstraint(f"content_origin IN ({_in(OBSERVATION_ORIGINS)})", name="ck_observations_origin"),
        CheckConstraint(f"salience IN ({_in(OBSERVATION_SALIENCE)})", name="ck_observations_salience"),
        CheckConstraint("char_length(summary) BETWEEN 1 AND 500 AND btrim(summary) <> ''",
                        name="ck_observations_summary"),
        CheckConstraint("schema_version >= 1", name="ck_observations_schema_version"),
        CheckConstraint("btrim(source) <> '' AND btrim(source_instance) <> '' AND btrim(source_event_key) <> ''",
                        name="ck_observations_identity"),
        CheckConstraint("caused_by_id IS NULL OR caused_by_id <> id", name="ck_observations_caused_by_self"),
        CheckConstraint("outcome_of_id IS NULL OR outcome_of_id <> id", name="ck_observations_outcome_self"),
        UniqueConstraint("user_id", "source", "source_instance", "source_event_key", name="uq_observations_source_key"),
        UniqueConstraint("id", "user_id", name="uq_observations_id_user"),
        ForeignKeyConstraint(["context_id", "user_id"], ["memory_contexts.id", "memory_contexts.user_id"],
                             name="fk_observations_context"),
        ForeignKeyConstraint(["caused_by_id", "user_id"], ["observations.id", "observations.user_id"],
                             ondelete="SET NULL (caused_by_id)", name="fk_observations_caused_by"),
        ForeignKeyConstraint(["outcome_of_id", "user_id"], ["observations.id", "observations.user_id"],
                             ondelete="SET NULL (outcome_of_id)", name="fk_observations_outcome_of"),
        Index("ix_observations_user_received", "user_id", sa_text("received_at DESC"), sa_text("id DESC")),
        Index("ix_observations_context_received", "context_id", sa_text("received_at DESC"), sa_text("id DESC"),
              postgresql_where=sa_text("context_id IS NOT NULL")),
        Index("ix_observations_message", "message_id", postgresql_where=sa_text("message_id IS NOT NULL")),
        Index("ix_observations_delivery", "delivery_id", postgresql_where=sa_text("delivery_id IS NOT NULL")),
        Index("ix_observations_experience", "experience_id", postgresql_where=sa_text("experience_id IS NOT NULL")),
        Index("ix_observations_session", "session_id", postgresql_where=sa_text("session_id IS NOT NULL")),
        Index("ix_observations_thought", "thought_id", postgresql_where=sa_text("thought_id IS NOT NULL")),
        Index("ix_observations_caused_by", "caused_by_id", postgresql_where=sa_text("caused_by_id IS NOT NULL")),
        Index("ix_observations_outcome_of", "outcome_of_id", postgresql_where=sa_text("outcome_of_id IS NOT NULL")),
    )


class ObservationProcessing(Base):
    """Whether one consumer has dealt with one observation: durable per row, so an observation that
    commits late (a lower id after higher ones were processed) is still processed. Only ``memory`` today."""

    __tablename__ = "observation_processing"

    observation_id: Mapped[int] = mapped_column(
        ForeignKey("observations.id", ondelete="CASCADE"), primary_key=True)
    consumer: Mapped[str] = mapped_column(String(16), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                      nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    rule_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        CheckConstraint(f"status IN ({_in(OBSERVATION_PROCESSING_STATUSES)})", name="ck_observation_processing_status"),
        CheckConstraint("attempts >= 0", name="ck_observation_processing_attempts"),
        CheckConstraint("(status IN ('applied', 'skipped', 'failed')) = (processed_at IS NOT NULL)",
                        name="ck_observation_processing_done"),
        ForeignKeyConstraint(["observation_id", "user_id"], ["observations.id", "observations.user_id"],
                             ondelete="CASCADE", name="fk_observation_processing_observation"),
        Index("ix_observation_processing_due", "consumer", "next_attempt_at", "observation_id",
              postgresql_where=sa_text("status IN ('pending', 'retry')")),
        Index("ix_observation_processing_user", "user_id", "consumer", "status"),
    )


class ObservationBinding(Base):
    """The context an observation was bound to after the fact (e.g. a user message, once her reply showed
    which context it was about). Kept apart so the observation itself never changes."""

    __tablename__ = "observation_bindings"

    observation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    context_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # How it was bound: focus_at_reply, focus_at_message, source_context_at_time.
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["observation_id", "user_id"], ["observations.id", "observations.user_id"],
                             ondelete="CASCADE", name="fk_observation_bindings_observation"),
        ForeignKeyConstraint(["context_id", "user_id"], ["memory_contexts.id", "memory_contexts.user_id"],
                             name="fk_observation_bindings_context"),
        Index("ix_observation_bindings_context", "context_id"),
    )


class ObservationSource(Base):
    """A source's health and progress, per user and instance (a conversation, a game session): mutable,
    unlike its observations. Availability, freshness and meaningful change are kept apart: silence in a
    conversation says nothing about whether the user is there."""

    __tablename__ = "observation_sources"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_instance: Mapped[str] = mapped_column(String(160), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sa_text("true"))
    availability: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", server_default="unknown")
    last_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Adapter-specific durable ingestion progress (e.g. {"sequence": 41} for a conversation).
    position: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    last_decision: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict,
                                                server_default=sa_text("'{}'::jsonb"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(f"availability IN ({_in(OBSERVATION_AVAILABILITY)})", name="ck_observation_sources_availability"),
        CheckConstraint("revision >= 1", name="ck_observation_sources_revision"),
    )


class ObservationAdapter(Base):
    """An adapter's own run state, for all users: when capture began (nothing older is imported), its last
    run and decision."""

    __tablename__ = "observation_adapters"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_decision: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict,
                                                server_default=sa_text("'{}'::jsonb"))


# An observation is immutable: the only change allowed is clearing a reference whose target went away
# (a context removed by a reset, an expired causal link, a deleted game session).
OBSERVATION_IMMUTABLE_DDL = """
CREATE OR REPLACE FUNCTION observations_immutable() RETURNS trigger AS $$
BEGIN
    IF (NEW.context_id IS DISTINCT FROM OLD.context_id AND NEW.context_id IS NOT NULL)
       OR (NEW.caused_by_id IS DISTINCT FROM OLD.caused_by_id AND NEW.caused_by_id IS NOT NULL)
       OR (NEW.outcome_of_id IS DISTINCT FROM OLD.outcome_of_id AND NEW.outcome_of_id IS NOT NULL)
       OR (NEW.session_id IS DISTINCT FROM OLD.session_id AND NEW.session_id IS NOT NULL)
       OR (to_jsonb(NEW) - ARRAY['context_id', 'caused_by_id', 'outcome_of_id', 'session_id'])
          IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['context_id', 'caused_by_id', 'outcome_of_id', 'session_id']) THEN
        RAISE EXCEPTION 'observations are immutable' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER observations_immutable BEFORE UPDATE ON observations
    FOR EACH ROW EXECUTE FUNCTION observations_immutable();
"""

event.listen(Observation.__table__, "after_create", DDL(OBSERVATION_IMMUTABLE_DDL).execute_if(dialect="postgresql"))

# Parents: a working item's parent is a goal, and no chain of parents (items or contexts) loops back.
# The composite keys already keep a parent in the same context (items) or user (contexts).
WORKING_MEMORY_PARENT_DDL = """
CREATE OR REPLACE FUNCTION working_items_parent_check() RETURNS trigger AS $$
DECLARE
    walker uuid;
    steps integer := 0;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.kind IS DISTINCT FROM OLD.kind
       AND EXISTS (SELECT 1 FROM working_items WHERE parent_id = NEW.id) THEN
        RAISE EXCEPTION 'working_items: a goal with sub-goals keeps its kind' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.parent_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF (SELECT kind FROM working_items WHERE id = NEW.parent_id) IS DISTINCT FROM 'goal' THEN
        RAISE EXCEPTION 'working_items: a parent must be a goal' USING ERRCODE = 'check_violation';
    END IF;
    walker := NEW.parent_id;
    WHILE walker IS NOT NULL LOOP
        IF walker = NEW.id OR steps > 64 THEN
            RAISE EXCEPTION 'working_items: parents may not loop' USING ERRCODE = 'check_violation';
        END IF;
        steps := steps + 1;
        SELECT parent_id INTO walker FROM working_items WHERE id = walker;
    END LOOP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER working_items_parent_check BEFORE INSERT OR UPDATE OF parent_id, kind, context_id ON working_items
    FOR EACH ROW EXECUTE FUNCTION working_items_parent_check();
"""

MEMORY_CONTEXT_PARENT_DDL = """
CREATE OR REPLACE FUNCTION memory_contexts_parent_check() RETURNS trigger AS $$
DECLARE
    walker uuid;
    steps integer := 0;
BEGIN
    walker := NEW.parent_id;
    WHILE walker IS NOT NULL LOOP
        IF walker = NEW.id OR steps > 64 THEN
            RAISE EXCEPTION 'memory_contexts: parents may not loop' USING ERRCODE = 'check_violation';
        END IF;
        steps := steps + 1;
        SELECT parent_id INTO walker FROM memory_contexts WHERE id = walker;
    END LOOP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER memory_contexts_parent_check BEFORE INSERT OR UPDATE OF parent_id ON memory_contexts
    FOR EACH ROW EXECUTE FUNCTION memory_contexts_parent_check();
"""

event.listen(WorkingItem.__table__, "after_create", DDL(WORKING_MEMORY_PARENT_DDL).execute_if(dialect="postgresql"))
event.listen(MemoryContext.__table__, "after_create", DDL(MEMORY_CONTEXT_PARENT_DDL).execute_if(dialect="postgresql"))


# --------------------------------------------------------------------------- #
# Recall (docs/COLUMBINA_MEMORY_SESSION3_RECALL_SPEC.md): what a reply was given, and what it used
# --------------------------------------------------------------------------- #

RECALL_RELATIONS = ("supplied", "referenced")
RECALL_ASSOCIATION_STATUSES = ("none", "pending", "done", "ambiguous", "failed", "skipped")
RECALL_EMBEDDING_STATUSES = ("pending", "done", "failed")


class RecallReceipt(Base):
    """What one reply was given from memory: the search outcome and coverage, and counts that outlive the
    memories themselves. No memory text: a deleted memory leaves only the count behind."""

    __tablename__ = "recall_receipts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assistant_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, unique=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    purpose: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    coverage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    counts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    association_status: Mapped[str] = mapped_column(String(16), nullable=False, default="none",
                                                    server_default="none")
    association_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('not_searched', 'matched', 'no_match', 'partial', 'failed')",
                        name="ck_recall_receipts_status"),
        CheckConstraint(f"association_status IN ({_in(RECALL_ASSOCIATION_STATUSES)})",
                        name="ck_recall_receipts_association"),
        UniqueConstraint("id", "user_id", name="uq_recall_receipts_id_user"),
        Index("ix_recall_receipts_conversation", "conversation_id", "created_at"),
        Index("ix_recall_receipts_user", "user_id", "created_at"),
    )


class RecallReference(Base):
    """One memory a reply was given (``supplied``) or that a span of the delivered answer is about
    (``referenced``, an association hypothesis, not proof the sentence is true). Exactly one target, a
    real foreign key with the same owner: deleting the memory deletes the row."""

    __tablename__ = "recall_references"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    receipt_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    relation: Mapped[str] = mapped_column(String(16), nullable=False)
    fact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    episode_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # A transcript passage she was given (stage 5): its owner is checked by a trigger (messages have no user column).
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    target_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    span_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    span_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    method: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(f"relation IN ({_in(RECALL_RELATIONS)})", name="ck_recall_references_relation"),
        CheckConstraint("num_nonnulls(fact_id, episode_id, event_id, item_id, message_id) = 1",
                        name="ck_recall_references_target"),
        CheckConstraint("(span_start IS NULL) = (span_end IS NULL) AND (span_start IS NULL OR "
                        "(span_start >= 0 AND span_end > span_start))", name="ck_recall_references_span"),
        CheckConstraint("relation = 'referenced' OR span_start IS NULL", name="ck_recall_references_supplied_span"),
        ForeignKeyConstraint(["receipt_id", "user_id"], ["recall_receipts.id", "recall_receipts.user_id"],
                             ondelete="CASCADE", name="fk_recall_references_receipt"),
        ForeignKeyConstraint(["fact_id", "user_id"], ["semantic_memories.id", "semantic_memories.user_id"],
                             ondelete="CASCADE", name="fk_recall_references_fact"),
        ForeignKeyConstraint(["episode_id", "user_id"], ["episodic_memories.id", "episodic_memories.user_id"],
                             ondelete="CASCADE", name="fk_recall_references_episode"),
        ForeignKeyConstraint(["event_id", "user_id"], ["memory_events.id", "memory_events.user_id"],
                             ondelete="CASCADE", name="fk_recall_references_event"),
        ForeignKeyConstraint(["item_id", "user_id"], ["working_items.id", "working_items.user_id"],
                             ondelete="CASCADE", name="fk_recall_references_item"),
        Index("uq_recall_references_supplied", "receipt_id", "fact_id", "episode_id", "event_id", "item_id",
              "message_id", unique=True, postgresql_where=sa_text("relation = 'supplied'"), postgresql_nulls_not_distinct=True),
        Index("ix_recall_references_receipt", "receipt_id"),
        Index("ix_recall_references_event", "event_id", postgresql_where=sa_text("event_id IS NOT NULL")),
        Index("ix_recall_references_item", "item_id", postgresql_where=sa_text("item_id IS NOT NULL")),
        Index("ix_recall_references_fact", "fact_id", postgresql_where=sa_text("fact_id IS NOT NULL")),
        Index("ix_recall_references_episode", "episode_id", postgresql_where=sa_text("episode_id IS NOT NULL")),
        Index("ix_recall_references_message", "message_id", postgresql_where=sa_text("message_id IS NOT NULL")),
    )


RECALL_REFERENCE_MESSAGE_OWNER_DDL = """
CREATE OR REPLACE FUNCTION recall_references_message_owner_check() RETURNS trigger AS $$
BEGIN
    IF NEW.message_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM chat_messages m JOIN conversations c ON c.id = m.conversation_id
            WHERE m.id = NEW.message_id AND c.user_id = NEW.user_id) THEN
        RAISE EXCEPTION 'a recalled message must belong to its owner' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER recall_references_message_owner_check BEFORE INSERT OR UPDATE ON recall_references
    FOR EACH ROW EXECUTE FUNCTION recall_references_message_owner_check();
"""
event.listen(RecallReference.__table__, "after_create",
             DDL(RECALL_REFERENCE_MESSAGE_OWNER_DDL).execute_if(dialect="postgresql"))


RECALL_HISTORY_STATUSES = ("complete", "partial", "no_history", "failed")
RECALL_HISTORY_SPEAKERS = ("user", "columbina", "source", "memory")


class RecallReconstruction(Base):
    """One history her ``recall_history`` tool rebuilt for a reply (docs/COLUMBINA_SOURCE_BACKED_RECALL.md):
    what was asked, the checked account (citations as spans into the passages, never copied text), how the
    collection went, and the reader's model and input digest, so what generation received can be traced
    later. Its passages are ``recall_reconstruction_sources``. Belongs to the reply's receipt."""

    __tablename__ = "recall_reconstructions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    receipt_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)  # as the tool was asked, bounded
    about: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reasoning_effort: Mapped[str | None] = mapped_column(String(16), nullable=True)
    input_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    account: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    coverage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(f"status IN ({_in(RECALL_HISTORY_STATUSES)})", name="ck_recall_reconstructions_status"),
        UniqueConstraint("id", "user_id", name="uq_recall_reconstructions_id_user"),
        ForeignKeyConstraint(["receipt_id", "user_id"], ["recall_receipts.id", "recall_receipts.user_id"],
                             ondelete="CASCADE", name="fk_recall_reconstructions_receipt"),
        Index("ix_recall_reconstructions_receipt", "receipt_id"),
    )


class RecallReconstructionSource(Base):
    """One passage a rebuilt history was shown: exactly one real target (a memory, or the message,
    observation, experience, session event or thought it came from), its version then (``fingerprint``),
    who asserted it, and whether the account cited it. No text: deleting the source deletes the row, and
    the reconstruction keeps only its counts. A trigger checks a source row belongs to the owner."""

    __tablename__ = "recall_reconstruction_sources"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    reconstruction_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    label: Mapped[str] = mapped_column(String(8), nullable=False)
    speaker: Mapped[str] = mapped_column(String(16), nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    cited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    in_scope: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    said_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    episode_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=True)
    observation_id: Mapped[int | None] = mapped_column(
        ForeignKey("observations.id", ondelete="CASCADE"), nullable=True)
    experience_id: Mapped[int | None] = mapped_column(
        ForeignKey("game_experiences.id", ondelete="CASCADE"), nullable=True)
    session_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("desktop_session_events.id", ondelete="CASCADE"), nullable=True)
    thought_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("mind_thoughts.id", ondelete="CASCADE"), nullable=True)

    __table_args__ = (
        CheckConstraint(f"speaker IN ({_in(RECALL_HISTORY_SPEAKERS)})", name="ck_recall_reconstruction_sources_speaker"),
        CheckConstraint("num_nonnulls(fact_id, episode_id, event_id, item_id, message_id, observation_id, "
                        "experience_id, session_event_id, thought_id) = 1",
                        name="ck_recall_reconstruction_sources_target"),
        UniqueConstraint("reconstruction_id", "label", name="uq_recall_reconstruction_sources_label"),
        ForeignKeyConstraint(["reconstruction_id", "user_id"],
                             ["recall_reconstructions.id", "recall_reconstructions.user_id"],
                             ondelete="CASCADE", name="fk_recall_reconstruction_sources_reconstruction"),
        ForeignKeyConstraint(["fact_id", "user_id"], ["semantic_memories.id", "semantic_memories.user_id"],
                             ondelete="CASCADE", name="fk_recall_reconstruction_sources_fact"),
        ForeignKeyConstraint(["episode_id", "user_id"], ["episodic_memories.id", "episodic_memories.user_id"],
                             ondelete="CASCADE", name="fk_recall_reconstruction_sources_episode"),
        ForeignKeyConstraint(["event_id", "user_id"], ["memory_events.id", "memory_events.user_id"],
                             ondelete="CASCADE", name="fk_recall_reconstruction_sources_event"),
        ForeignKeyConstraint(["item_id", "user_id"], ["working_items.id", "working_items.user_id"],
                             ondelete="CASCADE", name="fk_recall_reconstruction_sources_item"),
        Index("ix_recall_reconstruction_sources_reconstruction", "reconstruction_id"),
        Index("ix_recall_reconstruction_sources_message", "message_id", postgresql_where=sa_text("message_id IS NOT NULL")),
        Index("ix_recall_reconstruction_sources_event", "event_id", postgresql_where=sa_text("event_id IS NOT NULL")),
        Index("ix_recall_reconstruction_sources_item", "item_id", postgresql_where=sa_text("item_id IS NOT NULL")),
        Index("ix_recall_reconstruction_sources_fact", "fact_id", postgresql_where=sa_text("fact_id IS NOT NULL")),
        Index("ix_recall_reconstruction_sources_episode", "episode_id",
              postgresql_where=sa_text("episode_id IS NOT NULL")),
        Index("ix_recall_reconstruction_sources_observation", "observation_id",
              postgresql_where=sa_text("observation_id IS NOT NULL")),
        Index("ix_recall_reconstruction_sources_experience", "experience_id",
              postgresql_where=sa_text("experience_id IS NOT NULL")),
        Index("ix_recall_reconstruction_sources_session_event", "session_event_id",
              postgresql_where=sa_text("session_event_id IS NOT NULL")),
        Index("ix_recall_reconstruction_sources_thought", "thought_id",
              postgresql_where=sa_text("thought_id IS NOT NULL")),
    )


class MemoryEventEmbedding(Base):
    """A memory event's embedding, written in the background so writing an event stays code-only. Recall
    works without it (context, words, time); a result whose fingerprint no longer matches is discarded."""

    __tablename__ = "memory_event_embeddings"

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    text_version: Mapped[str] = mapped_column(String(16), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    vector: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                      nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(f"status IN ({_in(RECALL_EMBEDDING_STATUSES)})", name="ck_memory_event_embeddings_status"),
        CheckConstraint("(status = 'done') = (vector IS NOT NULL)", name="ck_memory_event_embeddings_vector"),
        ForeignKeyConstraint(["event_id", "user_id"], ["memory_events.id", "memory_events.user_id"],
                             ondelete="CASCADE", name="fk_memory_event_embeddings_event"),
        Index("ix_memory_event_embeddings_due", "status", "next_attempt_at"),
    )


# A receipt belongs to the user whose conversation the reply is in.
RECALL_RECEIPT_OWNER_DDL = """
CREATE OR REPLACE FUNCTION recall_receipts_owner_check() RETURNS trigger AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM chat_messages m JOIN conversations c ON c.id = m.conversation_id
                   WHERE m.id = NEW.assistant_message_id AND c.id = NEW.conversation_id
                         AND c.user_id = NEW.user_id) THEN
        RAISE EXCEPTION 'a recall receipt must belong to the reply''s owner' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER recall_receipts_owner_check BEFORE INSERT OR UPDATE ON recall_receipts
    FOR EACH ROW EXECUTE FUNCTION recall_receipts_owner_check();
"""

event.listen(RecallReceipt.__table__, "after_create", DDL(RECALL_RECEIPT_OWNER_DDL).execute_if(dialect="postgresql"))

# A rebuilt history's source rows belong to its owner (memories are checked by their composite keys).
RECALL_RECONSTRUCTION_SOURCE_OWNER_DDL = """
CREATE OR REPLACE FUNCTION recall_reconstruction_sources_owner_check() RETURNS trigger AS $$
BEGIN
    IF (NEW.message_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM chat_messages m JOIN conversations c ON c.id = m.conversation_id
            WHERE m.id = NEW.message_id AND c.user_id = NEW.user_id))
       OR (NEW.observation_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM observations o WHERE o.id = NEW.observation_id AND o.user_id = NEW.user_id))
       OR (NEW.experience_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM game_experiences g WHERE g.id = NEW.experience_id AND g.user_id = NEW.user_id))
       OR (NEW.thought_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM mind_thoughts t WHERE t.id = NEW.thought_id AND t.user_id = NEW.user_id))
       OR (NEW.session_event_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM desktop_session_events e JOIN desktop_sessions s ON s.id = e.session_id
            WHERE e.id = NEW.session_event_id AND s.owner_user_id = NEW.user_id)) THEN
        RAISE EXCEPTION 'a reconstruction source must belong to its owner' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER recall_reconstruction_sources_owner_check BEFORE INSERT OR UPDATE ON recall_reconstruction_sources
    FOR EACH ROW EXECUTE FUNCTION recall_reconstruction_sources_owner_check();
"""

event.listen(RecallReconstructionSource.__table__, "after_create",
             DDL(RECALL_RECONSTRUCTION_SOURCE_OWNER_DDL).execute_if(dialect="postgresql"))


# --------------------------------------------------------------------------- #
# Corrections (docs/COLUMBINA_MEMORY_CORRECTIONS.md): the user correcting something said or recorded earlier
# --------------------------------------------------------------------------- #

MEMORY_CORRECTION_KINDS = ("changed", "mistaken", "unspecified")
MEMORY_CORRECTION_STATUSES = ("pending", "processing", "applied", "failed")


class MemoryCorrection(Base):
    """One explicit correction by the user, as semantic consolidation read it from their message: what it's
    about, what was said or recorded before (when known), what holds now, and whether the thing changed
    or the earlier statement was a mistake. Written in the same transaction as the consolidated facts, then
    applied by the memory manager to the current memories it affects (``result`` says which and how). The
    queue and the structured record behind the ``corrected`` memory event; deleting the message deletes it."""

    __tablename__ = "memory_corrections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    source_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False)
    memory_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memory_jobs.id", ondelete="SET NULL"), nullable=True)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    attribute: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    user_words: Mapped[str] = mapped_column(Text, nullable=False)
    # When the user said it (their message's time): corrections apply in this order.
    said_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                      nullable=False)
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(f"kind IN ({_in(MEMORY_CORRECTION_KINDS)})", name="ck_memory_corrections_kind"),
        CheckConstraint(f"status IN ({_in(MEMORY_CORRECTION_STATUSES)})", name="ck_memory_corrections_status"),
        CheckConstraint("btrim(subject) <> '' AND btrim(new_value) <> '' AND btrim(user_words) <> ''",
                        name="ck_memory_corrections_text"),
        CheckConstraint("(status = 'applied') = (applied_at IS NOT NULL)", name="ck_memory_corrections_applied"),
        UniqueConstraint("user_id", "source_message_id", "fingerprint", name="uq_memory_corrections_once"),
        ForeignKeyConstraint(["event_id", "user_id"], ["memory_events.id", "memory_events.user_id"],
                             ondelete="SET NULL (event_id)", name="fk_memory_corrections_event"),
        Index("ix_memory_corrections_due", "status", "next_attempt_at"),
        Index("ix_memory_corrections_user", "user_id", "said_at"),
        Index("ix_memory_corrections_message", "source_message_id"),
    )


MEMORY_CORRECTION_OWNER_DDL = """
CREATE OR REPLACE FUNCTION memory_corrections_owner_check() RETURNS trigger AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM chat_messages m JOIN conversations c ON c.id = m.conversation_id
                   WHERE m.id = NEW.source_message_id AND c.user_id = NEW.user_id AND m.role = 'user') THEN
        RAISE EXCEPTION 'a correction must come from its owner''s own message' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER memory_corrections_owner_check BEFORE INSERT OR UPDATE OF source_message_id, user_id
    ON memory_corrections FOR EACH ROW EXECUTE FUNCTION memory_corrections_owner_check();
"""

event.listen(MemoryCorrection.__table__, "after_create",
             DDL(MEMORY_CORRECTION_OWNER_DDL).execute_if(dialect="postgresql"))


# pending -> processing -> applying (its corrections are queued) -> done; or failed (retried daily until
# ``daily_until`` first), or superseded (a newer change of the same fact took its items over). What's held is
# kept in ``memory_dependencies`` (stage 4), not by this status: a failure never lifts a hold.
MEMORY_FACT_REVISION_STATUSES = ("pending", "processing", "applying", "done", "failed", "superseded")
MEMORY_FACT_REVISION_HOLDING = ("pending", "processing", "applying")
MEMORY_FACT_REVISION_SETTLED = ("done", "failed", "superseded")


class MemoryFactRevision(Base):
    """A fact's wording changed in consolidation (``services/memory_reconcile.py``). Kept as evidence: the old
    and new versions and the message behind the change, then read for the change it supports (or none: a
    paraphrase); a supported change becomes a ``memory_corrections`` row that reaches the working items.
    ``held_item_ids``: open items that share a source with the old version, held from autonomous use until
    this is settled (only potentially affected, never assumed to state the fact)."""

    __tablename__ = "memory_fact_revisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    fact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("semantic_memories.id", ondelete="CASCADE"),
                                               nullable=False)
    from_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    to_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    old_content: Mapped[str] = mapped_column(Text, nullable=False)
    new_content: Mapped[str] = mapped_column(Text, nullable=False)
    source_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False)
    memory_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memory_jobs.id", ondelete="SET NULL"), nullable=True)
    held_item_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list,
                                                server_default=sa_text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                      nullable=False)
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set when its quick retries ran out: retried once a day until then, then failed (its items stay held).
    daily_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(f"status IN ({_in(MEMORY_FACT_REVISION_STATUSES)})", name="ck_memory_fact_revisions_status"),
        CheckConstraint("to_revision > from_revision", name="ck_memory_fact_revisions_order"),
        CheckConstraint(f"(status IN ({_in(MEMORY_FACT_REVISION_SETTLED)})) = (settled_at IS NOT NULL)",
                        name="ck_memory_fact_revisions_settled"),
        UniqueConstraint("fact_id", "to_revision", name="uq_memory_fact_revisions_once"),
        Index("ix_memory_fact_revisions_due", "status", "next_attempt_at"),
        Index("ix_memory_fact_revisions_user", "user_id", "status"),
    )


# required: the item only makes sense while the fact holds ("visit Nora in Lisbon" on where Nora lives);
# context: it mentions or was thought with the fact, without relying on it; none: checked, unrelated; unknown:
# not known yet (an older item that shares a source or a person with the fact).
MEMORY_DEPENDENCY_ROLES = ("required", "context", "none", "unknown")
# valid: checked against ``checked_revision``; pending: the fact changed and it's being rechecked; review: the
# recheck couldn't settle it (failed, or the item needs a look): it may be out of date.
MEMORY_DEPENDENCY_STATES = ("valid", "pending", "review")
# derived: written when the item was made; judged: by a recheck's reading; candidate: queued for one;
# confirmed: the user's turn said it still stands.
MEMORY_DEPENDENCY_ORIGINS = ("derived", "judged", "candidate", "confirmed")
# Only these hold an item from autonomous use while not valid (an incidental link never blocks it).
MEMORY_DEPENDENCY_HOLDING_ROLES = ("required", "unknown")


class MemoryDependency(Base):
    """What a working item relies on (stage 4, ``services/memory_dependencies.py``): one row per item and fact,
    separate from provenance (``memory_evidence`` says where it came from; this says what must still hold for
    it to be usable). Both ends are owner-keyed and go with either side. ``pending_revision_id``: the fact's
    change (``memory_fact_revisions``) that owns the recheck; a newer change takes it over."""

    __tablename__ = "memory_dependencies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    fact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    checked_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pending_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memory_fact_revisions.id", ondelete="SET NULL"), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(f"role IN ({_in(MEMORY_DEPENDENCY_ROLES)})", name="ck_memory_dependencies_role"),
        CheckConstraint(f"state IN ({_in(MEMORY_DEPENDENCY_STATES)})", name="ck_memory_dependencies_state"),
        CheckConstraint(f"origin IN ({_in(MEMORY_DEPENDENCY_ORIGINS)})", name="ck_memory_dependencies_origin"),
        CheckConstraint("state <> 'valid' OR checked_revision IS NOT NULL", name="ck_memory_dependencies_checked"),
        ForeignKeyConstraint(["item_id", "user_id"], ["working_items.id", "working_items.user_id"],
                             ondelete="CASCADE", name="fk_memory_dependencies_item"),
        ForeignKeyConstraint(["fact_id", "user_id"], ["semantic_memories.id", "semantic_memories.user_id"],
                             ondelete="CASCADE", name="fk_memory_dependencies_fact"),
        UniqueConstraint("item_id", "fact_id", name="uq_memory_dependencies"),
        Index("ix_memory_dependencies_fact", "user_id", "fact_id"),
        Index("ix_memory_dependencies_item", "user_id", "item_id"),
        Index("ix_memory_dependencies_unsettled", "user_id", "state", postgresql_where=sa_text("state <> 'valid'")),
        Index("ix_memory_dependencies_revision", "pending_revision_id",
              postgresql_where=sa_text("pending_revision_id IS NOT NULL")),
    )


# open -> known_confirmed (the user settled it: the memory holds), fact_changed (the fact was revised since), or
# forgotten (the fact was forgotten).
MEMORY_CONFLICT_STATUSES = ("open", "known_confirmed", "fact_changed", "forgotten")


class MemoryConflict(Base):
    """A message disagreed with a fact without saying it changed or was wrong (services/memory_conflicts.py,
    docs/COLUMBINA_MEMORY_CONFLICTS.md). Memory never decides which holds: the fact is left as it was, this row
    keeps the disagreement visible (recall marks the fact unresolved), and her mind gets it as a problem, so
    her mind chooses whether to ask, think it over or leave it. ``fact_revision`` is the version that was
    contradicted; a later revision of the fact settles it as ``fact_changed``."""

    __tablename__ = "memory_conflicts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    fact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    fact_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    known_content: Mapped[str] = mapped_column(Text, nullable=False)  # the fact as it read then
    stated: Mapped[str] = mapped_column(Text, nullable=False)
    user_words: Mapped[str] = mapped_column(Text, nullable=False)
    source_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    memory_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memory_jobs.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", server_default="open")
    offered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # to her mind
    settled_by_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # The fact must be this user's: ownership is a key, not a convention.
        ForeignKeyConstraint(("fact_id", "user_id"), ("semantic_memories.id", "semantic_memories.user_id"),
                             ondelete="CASCADE", name="fk_memory_conflicts_fact_owner"),
        CheckConstraint(f"status IN ({_in(MEMORY_CONFLICT_STATUSES)})", name="ck_memory_conflicts_status"),
        CheckConstraint("(status = 'open') = (settled_at IS NULL)", name="ck_memory_conflicts_settled"),
        UniqueConstraint("fact_id", "source_message_id", name="uq_memory_conflicts_once"),
        UniqueConstraint("id", "user_id", name="uq_memory_conflicts_id_user"),
        Index("ix_memory_conflicts_user", "user_id", "status"),
        Index("ix_memory_conflicts_fact", "fact_id", "status"),
    )


# ``partial``: forgotten, but some messages that also held other things are set aside whole from memory.
MEMORY_FORGET_STATUSES = ("pending", "done", "partial", "nothing_found", "failed")


class MemoryForget(Base):
    """A user's request to forget something (services/memory_forget.py). Content-free once settled: the request
    words live only in ``request`` while it's pending and are cleared when it ends; what remains is which message
    asked, how many records of each kind went, and the messages they came from (``memory_forgotten_sources``)."""

    __tablename__ = "memory_forgets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    source_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    memory_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memory_jobs.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    request: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)  # pending only
    removed: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    # Hashes of the request's word stems (stage 5), kept once it's done: a reply that went back over the forgotten
    # history can't make memory learn it again. No words, only hashes.
    fence: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                      nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(f"status IN ({_in(MEMORY_FORGET_STATUSES)})", name="ck_memory_forgets_status"),
        CheckConstraint("(status = 'pending') = (finished_at IS NULL)", name="ck_memory_forgets_finished"),
        CheckConstraint("status = 'pending' OR request IS NULL", name="ck_memory_forgets_request_cleared"),
        UniqueConstraint("source_message_id", name="uq_memory_forgets_message"),
        UniqueConstraint("id", "user_id", name="uq_memory_forgets_id_user"),
        Index("ix_memory_forgets_due", "next_attempt_at", postgresql_where=sa_text("status = 'pending'")),
        Index("ix_memory_forgets_user", "user_id", "created_at"),
    )


class MemoryForgottenSource(Base):
    """A message that something forgotten came from: memory never re-reads it as context or re-extracts from it,
    so what was forgotten can't come back from its source (the message itself stays in the chat)."""

    __tablename__ = "memory_forgotten_sources"

    forget_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # Which characters of the message are about what was forgotten, as [start, end) pairs (stage 5); null: the
    # whole message (it couldn't be separated safely). Memory reads the rest; transcript search keeps it all.
    spans: Mapped[list | None] = mapped_column(JSONB(none_as_null=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(["forget_id", "user_id"], ["memory_forgets.id", "memory_forgets.user_id"],
                             ondelete="CASCADE", name="fk_memory_forgotten_sources_forget"),
        Index("ix_memory_forgotten_sources_message", "user_id", "message_id"),
    )


class ChatMessageEmbedding(Base):
    """A message's embedding for transcript search by meaning (stage 5, services/transcript_search.py): what was
    said, as far as it was heard, filled in the background. Search works without it (by words); a vector whose
    fingerprint no longer matches the heard text is ignored and filled again."""

    __tablename__ = "chat_message_embeddings"

    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_messages.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    vector: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                      nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(f"status IN ({_in(RECALL_EMBEDDING_STATUSES)})", name="ck_chat_message_embeddings_status"),
        CheckConstraint("(status = 'done') = (vector IS NOT NULL)", name="ck_chat_message_embeddings_vector"),
        Index("ix_chat_message_embeddings_user", "user_id", postgresql_where=sa_text("status = 'done'")),
        Index("ix_chat_message_embeddings_due", "status", "next_attempt_at"),
    )


MEMORY_UPKEEP_VERDICTS = ("different", "merged", "skipped")


class MemoryUpkeepPair(Base):
    """Two current facts upkeep looked at as possible duplicates (services/memory_upkeep.py, stage 5), and what it
    decided. ``different`` pairs aren't judged again until either fact's fingerprint changes. Content-free: ids and
    fingerprints only. A merged pair's duplicate is gone (its row with it); the row then keeps only the verdict."""

    __tablename__ = "memory_upkeep_pairs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    fact_a: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    fact_b: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    fingerprint_a: Mapped[str] = mapped_column(String(32), nullable=False)
    fingerprint_b: Mapped[str] = mapped_column(String(32), nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(120), nullable=True)  # a code reason, never memory text
    judged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(f"verdict IN ({_in(MEMORY_UPKEEP_VERDICTS)})", name="ck_memory_upkeep_pairs_verdict"),
        CheckConstraint("fact_a < fact_b", name="ck_memory_upkeep_pairs_order"),
        ForeignKeyConstraint(["fact_a", "user_id"], ["semantic_memories.id", "semantic_memories.user_id"],
                             ondelete="CASCADE", name="fk_memory_upkeep_pairs_a"),
        ForeignKeyConstraint(["fact_b", "user_id"], ["semantic_memories.id", "semantic_memories.user_id"],
                             ondelete="CASCADE", name="fk_memory_upkeep_pairs_b"),
        UniqueConstraint("fact_a", "fact_b", name="uq_memory_upkeep_pairs"),
        Index("ix_memory_upkeep_pairs_user", "user_id", "judged_at"),
    )


class AutonomousDelivery(Base):
    """What she said on her own in one quiet stretch of a conversation (services/autonomous_speech.py): the stretch
    is ``after_sequence``, the user's latest message then, so a new user message starts a new one. Reserved in
    the delivery transaction; ``key`` is the premise it was about (a conflict, an item) and ``words`` its content
    words, so the same question isn't asked again in other words until the user writes."""

    __tablename__ = "autonomous_deliveries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    after_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # question / decision / guess / ...
    key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    words: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb"))
    work_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("work_id", name="uq_autonomous_deliveries_work"),
        Index("uq_autonomous_deliveries_key", "conversation_id", "after_sequence", "key", unique=True,
              postgresql_where=sa_text("key IS NOT NULL")),
        Index("ix_autonomous_deliveries_stretch", "conversation_id", "after_sequence"),
    )


class CoordinatorWork(Base):
    """Durable background work and wake events (services/coordinator.py): thinking her mind started (``mind``,
    with a ``recall`` it depends on), a thought or proposal another part of her handed over (``mind`` with its
    result already there), a reconsideration she scheduled (``wake``: due at ``next_attempt_at``), and
    maintenance (``memory``, ``observation``). Leased like ``chat_jobs``. ``completed`` never means its result
    was said: that's ``coordinator_outputs``. A new user message in the conversation supersedes unfinished
    thinking (never a ``wake``): it raises ``cancellation_generation``, which the worker checks before accepting
    a result and before any delivery."""

    __tablename__ = "coordinator_work"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_coordinator_work_idempotency"),
        CheckConstraint("kind IN ('recall', 'mind', 'wake', 'memory', 'observation')", name="ck_coordinator_work_kind"),
        CheckConstraint("status IN ('queued', 'waiting_for_memory', 'running', 'completed', 'failed', "
                        "'cancelled', 'expired', 'superseded', 'blocked')", name="ck_coordinator_work_status"),
        Index("ix_coordinator_work_claim", "status", "lease_expires_at"),
        Index("ix_coordinator_work_conversation", "conversation_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, nullable=False)  # the turn
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"),
                                                       nullable=True)
    activity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    trigger_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    dependency_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    input: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # what the work reads (ids, labels)
    input_versions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    cancellation_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(),
                                                 onupdate=func.now())


class MindEvaluation(Base):
    """One evaluation of her mind (services/mind_turn.py): why it ran (a user's message or a wake), what she did
    and what was delivered. ``outcome``: said / partial / silent (stay_silent) / no_words (a wake she wrote nothing
    for) / cancelled / failed; ``delivery``: delivered / partial / not_sent / cancelled / failed. History shows an
    evaluation without a message as a labelled record, never as words she said. Written once per turn."""

    __tablename__ = "mind_evaluations"
    __table_args__ = (
        UniqueConstraint("turn_id", name="uq_mind_evaluations_turn"),
        CheckConstraint("outcome IN ('said', 'partial', 'silent', 'no_words', 'cancelled', 'failed')",
                        name="ck_mind_evaluations_outcome"),
        Index("ix_mind_evaluations_conversation", "conversation_id", "finished_at"),
        Index("ix_mind_evaluations_user", "user_id", "finished_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"),
                                                              nullable=True)
    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(24), nullable=False)
    trigger_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True)
    work_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("coordinator_work.id", ondelete="SET NULL"),
                                                      nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    delivery: Mapped[str] = mapped_column(String(16), nullable=False)
    assistant_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True)
    silence_reason: Mapped[str | None] = mapped_column(Text, nullable=True)  # hers, a few words
    tools: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)  # tool names, in order
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CoordinatorOutput(Base):
    """One output record per phase of a request: ``immediate`` (what the evaluation that started thinking did
    meanwhile: respond / silent) and ``follow_up`` (the wake of her mind for a finished item: ``wake`` when
    dispatched, then what she did, ``said`` / ``silent`` / ``no_words``; or ``superseded`` / ``rejected`` when
    execution facts kept it from waking her), and what was actually delivered. Unique per (request, phase), so a
    retry or restart never repeats a phase. Silence is a row here, never an empty assistant message."""

    __tablename__ = "coordinator_outputs"
    __table_args__ = (
        UniqueConstraint("request_id", "phase", name="uq_coordinator_outputs_phase"),
        CheckConstraint("phase IN ('immediate', 'follow_up')", name="ck_coordinator_outputs_phase"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"),
                                                       nullable=False)
    work_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("coordinator_work.id", ondelete="SET NULL"),
                                                      nullable=True)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    decision_origin: Mapped[str] = mapped_column(String(32), nullable=False)
    missing_capability: Mapped[str | None] = mapped_column(Text, nullable=True)
    # decided -> dispatched -> delivered / partial / cancelled / failed; not_sent (silent, blocked, superseded)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="decided")
    # Set atomically with the saved reply; cleared only after transport publication.
    announcement_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    chat_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chat_jobs.id", ondelete="SET NULL"),
                                                          nullable=True)
    assistant_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(),
                                                 onupdate=func.now())


# --------------------------------------------------------------------------- #
# Learning capture (docs/learning-system/01-capture-and-ownership.md; implementation and settings:
# docs/learning-system/01-implementation.md). Raw decision captures: what each learner was given and produced,
# and what actually executed. Written in the background by services/learning_capture.py, never on the reply
# path; kept LEARNING_CAPTURE_RETENTION_DAYS (30), and removed sooner with their conversation, message, game
# session or a forgotten memory. Links between attempts and executions are plain ids, not foreign keys: they are
# written asynchronously and out of order, and a missing one is a visible gap, never a failed insert.
# --------------------------------------------------------------------------- #

# The planner and hard thoughts joined in post-audit 1 (docs/learning-system/POST-AUDIT-01-implementation.md).
# mind: one evaluation of her mind (services/mind_turn.py): what she read, the tools she used, what she said.
CAPTURE_LEARNERS = ("mind", "hands", "planner", "hard_thought")
CAPTURE_ATTEMPT_STATUSES = ("ok", "invalid", "unavailable", "error")
# plan_apply: what application code did with the planner's decision (the plan revision it made, the rules it
# carried); thought_route: where a hard thought's conclusion went; step_check: code counting a plan step done;
# desktop_action: one action of her hands on her own desktop (services/desktop_control).
CAPTURE_EXECUTION_KINDS = ("reply", "heard", "channel_delivery", "skill_run", "plan_apply", "thought_route",
                           "step_check", "desktop_action")
# pack_dropped: trajectory chunks the game pack let go of before the backend took them (its buffer was full).
CAPTURE_GAP_REASONS = ("queue_full", "retries_exhausted", "unserializable", "source_gone", "shutdown", "pack_dropped")
# succeeded: did what was intended; deviated: did something, not exactly that (a reply cut off, a partial
# delivery); failed: known failure; unknown: no result known (yet, or ever); not_executed: never started;
# cancelled: stopped on purpose; handed_off: given to another owner (background work); not_sent: silence chosen.
CAPTURE_RESULTS = ("succeeded", "deviated", "failed", "unknown", "not_executed", "cancelled", "handed_off",
                   "not_sent")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class CaptureBlob(Base):
    """Exact, immutable content an attempt referenced instead of embedding (a system prompt, a schema), per user:
    an attempt's ``{"$blob": sha}`` resolves here while the attempt exists; orphans are pruned."""

    __tablename__ = "capture_blobs"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                   nullable=False)


class DecisionAttempt(Base):
    """One learner invocation as it happened: the exact input frozen when supplied, the output when produced.
    ``id`` is assigned before the call and travels downstream; an actual retry is a new row whose
    ``prior_attempt_id`` is the one before; a persistence retry reuses the same id (insert does nothing twice).
    ``absent`` names what wasn't captured on purpose (no prediction, no alternatives), never filled in later."""

    __tablename__ = "decision_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    learner: Mapped[str] = mapped_column(String(24), nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(24), nullable=True)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=True)
    game_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("desktop_sessions.id", ondelete="CASCADE"), nullable=True)
    prior_attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    upstream: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    versions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    input: Mapped[dict] = mapped_column(JSONB, nullable=False)
    input_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb"))
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    absent: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb"))
    # Optional links later plans fill (a prediction made before acting; how a choice was selected). Null: none.
    prediction_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    selection: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    blob_hashes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    produced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(_in("learner", CAPTURE_LEARNERS), name="ck_decision_attempts_learner"),
        CheckConstraint(_in("status", CAPTURE_ATTEMPT_STATUSES), name="ck_decision_attempts_status"),
        Index("ix_decision_attempts_user", "user_id", "captured_at"),
        Index("ix_decision_attempts_turn", "turn_id"),
        Index("ix_decision_attempts_session", "game_session_id"),
        Index("ix_decision_attempts_conversation", "conversation_id", "started_at"),
        Index("ix_decision_attempts_expires", "expires_at"),
        Index("ix_decision_attempts_blobs", "blob_hashes", postgresql_using="gin"),
    )


class DecisionAttemptSource(Base):
    """What an attempt's input was made from (``message:<id>``, ``fact:<id>``, ``item:<id>``, ...): how a
    forgotten memory or message finds the raw copies to remove."""

    __tablename__ = "decision_attempt_sources"

    attempt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decision_attempts.id", ondelete="CASCADE"),
                                                  primary_key=True)
    ref: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    __table_args__ = (Index("ix_decision_attempt_sources_ref", "user_id", "ref"),)


class DecisionExecution(Base):
    """What actually happened to an attempt's decision, recorded by the executor (delivery, hands, an effect's
    owner), separate from what was intended. ``result='unknown'`` is honest absence of a result; a later settle
    may replace only an unknown one."""

    __tablename__ = "decision_executions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    executor: Mapped[str] = mapped_column(String(40), nullable=False)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True)
    assistant_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=True)
    game_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("desktop_sessions.id", ondelete="CASCADE"), nullable=True)
    intended: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    executed: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    deviation: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(_in("kind", CAPTURE_EXECUTION_KINDS), name="ck_decision_executions_kind"),
        CheckConstraint(_in("result", CAPTURE_RESULTS), name="ck_decision_executions_result"),
        Index("ix_decision_executions_attempt", "attempt_id"),
        Index("ix_decision_executions_turn", "turn_id"),
        Index("ix_decision_executions_message", "assistant_message_id"),
        Index("ix_decision_executions_user", "user_id", "captured_at"),
        Index("ix_decision_executions_expires", "expires_at"),
    )


class CaptureGap(Base):
    """A capture known to be lost: queue full, retries exhausted, unserializable, its source gone before it was
    saved, or still queued at shutdown. Content-free. A sudden crash can lose records without a gap."""

    __tablename__ = "capture_gaps"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    record_kind: Mapped[str] = mapped_column(String(16), nullable=False)  # attempt / execution
    record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    label: Mapped[str] = mapped_column(String(40), nullable=False)  # learner or execution kind
    reason: Mapped[str] = mapped_column(String(24), nullable=False)
    tries: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(_in("reason", CAPTURE_GAP_REASONS), name="ck_capture_gaps_reason"),
        UniqueConstraint("record_id", name="uq_capture_gaps_record"),
        Index("ix_capture_gaps_user", "user_id", "detected_at"),
    )


# Her trajectory (docs/learning-system/POST-AUDIT-01-fill-missing-experience-capture.md; implementation:
# docs/learning-system/POST-AUDIT-01-implementation.md): every game tick the pack's client exposed, in chunks the
# pack encoded (JSON, gzipped: a checkpoint then one frame per tick, and the events in between). Each connection is
# a stream; a chunk is self-contained, so retention may drop any one. Raw capture: kept
# LEARNING_CAPTURE_RETENTION_DAYS, removed with the user or the game session it was collected in. Chunks name
# their stream by id, not a foreign key: they arrive asynchronously, and a missing piece shows as a hole in seq.


class TrajectoryStream(Base):
    """One connection of her player (pack ``src/trajectory.js``): a run is a pack process, a stream one
    connection in it. ``ended_at`` null: the end was never seen (still connected, or the pack stopped without
    saying so). Never continuous with another stream."""

    __tablename__ = "trajectory_streams"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    stream_index: Mapped[int] = mapped_column(Integer, nullable=False)
    game: Mapped[str] = mapped_column(String(32), nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # the pack's clock
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=-1, server_default="-1")  # as the pack said
    coverage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_trajectory_streams_user", "user_id", "started_at"),
                      Index("ix_trajectory_streams_expires", "expires_at"))


class TrajectoryChunk(Base):
    """About 5 s of her trajectory as the pack encoded it (``data``: ``encoding`` bytes, never rewritten). ``id`` is
    ``uuid5(stream, "seq:<n>")``, so a chunk saved twice is one row. Times are the pack's clock; ``captured_at``
    the backend's."""

    __tablename__ = "trajectory_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    stream_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # The session live when the backend collected it (None: she was in the world outside any session).
    game_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("desktop_sessions.id", ondelete="CASCADE"), nullable=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    first_tick: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ticks: Mapped[int] = mapped_column(Integer, nullable=False)
    events: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    format: Mapped[int] = mapped_column(Integer, nullable=False)
    encoding: Mapped[str] = mapped_column(String(16), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)  # stored (encoded)
    raw_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    kinds: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    overhead: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("stream_id", "seq", name="uq_trajectory_chunks_stream_seq"),
        Index("ix_trajectory_chunks_user", "user_id", "started_at"),
        Index("ix_trajectory_chunks_session", "game_session_id", "started_at"),
        Index("ix_trajectory_chunks_expires", "expires_at"),
    )


# --------------------------------------------------------------------------- #
# Retrospective assessment (docs/learning-system/02-evidence-and-attribution.md; implementation, statuses and
# settings: docs/learning-system/02-implementation.md). Learning's own derivatives, written only by
# services/learning_assessment.py: what feedback or an outcome is about (targets), which part of her caused it
# (causes), the six dimensions, what's missing; each revision kept, a newer one superseding an older one.
# Links are opaque refs (``attempt:<id>``, ``message:<id>``, ``turn:<id>``): raw captures expire, assessments stay
# until their conversation, game session, trigger message or user goes, or a forget removes them.
# --------------------------------------------------------------------------- #

# review: a scheduled opportunity over a stretch of activity (post-audit 3); correction / complaint / sample are
# kept for rows opened before semantic review replaced them as admission routes.
ASSESSMENT_TRIGGERS = ("correction", "complaint", "execution", "sample", "review")
ASSESSMENT_STATES = ("pending", "running", "done", "failed")
# left: the reviewer judged there was nothing worth examining (no substantive assessment, no product).
ASSESSMENT_OUTCOMES = ("concluded", "unresolved", "no_feedback", "left")
# Every stop but concluded/left is a parked, resumable inquiry: waiting (it asked to wait for more), stuck (looking
# further added nothing new), missing_evidence (what would settle it is not available).
ASSESSMENT_STOPS = ("concluded", "missing_evidence", "stuck", "waiting", "left")
ASSESSMENT_LINK_ROLES = ("target", "cause", "cited")
ASSESSMENT_USE_STATES = ("current", "reconsider")


class LearningAssessment(Base):
    """One thing worth assessing (a correction, a complaint, an execution failure, a sampled exchange) and its
    current conclusion. ``trigger_key`` makes reprocessing idempotent; ``epoch`` counts evidence arriving, so a run
    that started before new evidence came is followed by another; ``revision`` is the current revision."""

    __tablename__ = "learning_assessments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    trigger_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    trigger_key: Mapped[str] = mapped_column(String(160), nullable=False)
    trigger: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True)
    game_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("desktop_sessions.id", ondelete="CASCADE"), nullable=True)
    anchor_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=True)
    anchor_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                      nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    target_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    cause_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Records whose arrival would add evidence, as machine references only: ``attempt:<uuid>`` (a decision
    # attempt) or ``execution:<uuid>`` (an execution). Parsed and resolved by learning_assessment.awaited_ref.
    awaiting: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb"))
    # Explanations of evidence that is missing or truncated ("trajectory missing or expired for this stretch"):
    # shown, never parsed as references.
    awaiting_notes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list,
                                                 server_default=sa_text("'[]'::jsonb"))
    # The open inquiry as it stands (post-audit 3): its question, provisional reading, alternatives, what remains
    # unknown, what would resume it, the evidence revision last examined and the requests it made. Empty: none.
    inquiry: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(_in("trigger_kind", ASSESSMENT_TRIGGERS), name="ck_learning_assessments_trigger"),
        CheckConstraint(_in("state", ASSESSMENT_STATES), name="ck_learning_assessments_state"),
        CheckConstraint(f"outcome IS NULL OR {_in('outcome', ASSESSMENT_OUTCOMES)}",
                        name="ck_learning_assessments_outcome"),
        CheckConstraint(f"stop_reason IS NULL OR {_in('stop_reason', ASSESSMENT_STOPS)}",
                        name="ck_learning_assessments_stop"),
        CheckConstraint("attempts >= 0 AND revision >= 0 AND epoch >= 0", name="ck_learning_assessments_counts"),
        UniqueConstraint("user_id", "trigger_key", name="uq_learning_assessments_trigger"),
        Index("ix_learning_assessments_due", "next_attempt_at",
              postgresql_where=sa_text("state IN ('pending', 'running')")),
        Index("ix_learning_assessments_user", "user_id", "updated_at"),
        Index("ix_learning_assessments_conversation", "conversation_id"),
    )


class LearningAssessmentEvidence(Base):
    """Evidence attached to an assessment beyond what its window shows (a memory correction of the message, a
    later message another assessment found relevant, a capture that arrived late, the user's note), once each."""

    __tablename__ = "learning_assessment_evidence"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("learning_assessments.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ref: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("assessment_id", "ref", name="uq_learning_assessment_evidence"),
        Index("ix_learning_assessment_evidence_ref", "user_id", "ref"),
    )


class LearningAssessmentRevision(Base):
    """One evaluation of an assessment, kept whole: what it read (digest), each investigation round, what the
    evaluator proposed and code kept or dropped, the conclusions, and which revision superseded it."""

    __tablename__ = "learning_assessment_revisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("learning_assessments.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluator: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    input_digest: Mapped[str] = mapped_column(String(32), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    dropped: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb"))
    rounds: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb"))
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    target_status: Mapped[str] = mapped_column(String(16), nullable=False)
    cause_status: Mapped[str] = mapped_column(String(16), nullable=False)
    stop_reason: Mapped[str] = mapped_column(String(24), nullable=False)
    independent: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    usage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(_in("outcome", ASSESSMENT_OUTCOMES), name="ck_learning_assessment_revisions_outcome"),
        CheckConstraint(_in("stop_reason", ASSESSMENT_STOPS), name="ck_learning_assessment_revisions_stop"),
        UniqueConstraint("assessment_id", "revision", name="uq_learning_assessment_revisions"),
    )


class LearningAssessmentLink(Base):
    """What one revision linked to, as opaque refs: its targets (with status), the records a cause is about
    (with component and status), and everything it cited. How later evidence and forgetting find assessments."""

    __tablename__ = "learning_assessment_links"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("learning_assessments.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(12), nullable=False)
    ref: Mapped[str] = mapped_column(String(100), nullable=False)
    component: Mapped[str] = mapped_column(String(16), nullable=False, default="", server_default="")
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)

    __table_args__ = (
        CheckConstraint(_in("role", ASSESSMENT_LINK_ROLES), name="ck_learning_assessment_links_role"),
        UniqueConstraint("assessment_id", "revision", "role", "ref", "component", name="uq_learning_assessment_links"),
        Index("ix_learning_assessment_links_ref", "user_id", "ref"),
    )


class LearningAssessmentUse(Base):
    """A conclusion another owner acted on (today: training examples flagged for review). When a newer revision
    changes the conclusions, the use becomes ``reconsider`` and its owner is told; the owner decides what changes."""

    __tablename__ = "learning_assessment_uses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("learning_assessments.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    consumer: Mapped[str] = mapped_column(String(32), nullable=False)
    ref: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="current", server_default="current")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    noted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(_in("state", ASSESSMENT_USE_STATES), name="ck_learning_assessment_uses_state"),
        UniqueConstraint("assessment_id", "consumer", "ref", name="uq_learning_assessment_uses"),
        Index("ix_learning_assessment_uses_ref", "user_id", "consumer", "ref"),
    )


# --------------------------------------------------------------------------- #
# Lessons (docs/learning-system/03-immediate-adaptation.md; implementation, statuses and settings:
# docs/learning-system/03-implementation.md). Memory owns them (services/memory_lessons.py writes every row);
# learning proposes them from supported assessments (services/learning_lessons.py). A lesson is experience for
# her mind to weigh, never a command. ``preference``: what the user said they want, within the scope they gave
# (their words kept); ``strategy``: what she inferred tends to work, from evidence for both the conclusion and where
# it applies. Every change is a revision; a withdrawn lesson stays withdrawn. Evidence and uses are opaque refs.
# --------------------------------------------------------------------------- #

# understanding (post-audit 3): her inference about how things are (an expectation, a relationship, an explanation,
# her own capability, an open question), not advice about what to do.
LESSON_KINDS = ("preference", "strategy", "understanding")
LESSON_ORIGINS = ("stated", "inferred")
LESSON_SCOPES = ("standing", "situation", "episode")
# accepted: an inference the builder judged established, or a stated preference; hypothesis: a tentative inference
# (supplied labelled as a guess while it has live support); episode_only: about one past episode, never a standing
# rule (kept, not supplied); withdrawn: final.
LESSON_STATUSES = ("accepted", "hypothesis", "episode_only", "withdrawn")
# noted (post-audit 4): an occasion that bore on a lesson without supporting or countering it (its conditions didn't
# hold there, or it covered only part of it). Counted for nothing; it shows what has been checked.
LESSON_EVIDENCE_ROLES = ("support", "counter", "noted")
# note: an occasion noted; keep: a reassessment that found it still holds (post-audit 4).
LESSON_OPS = ("create", "support", "counter", "narrow", "revise", "withdraw", "reconsider", "note", "keep")
LESSON_REPORTED = ("applied", "not_relevant", "outweighed", "conflict", "unclear")
LESSON_JOB_STATES = ("pending", "running", "done", "failed")
# assessment: read one assessment revision for lessons; reconsider (post-audit 4): reassess one lesson on the
# evidence it already rests on, after a premise of it changed or her mind questioned its reasoning.
LESSON_JOB_KINDS = ("assessment", "reconsider")
# What a revision was prompted by when no new evidence came: a critique of its reasoning, a changed premise.
LESSON_PROMPTS = ("critique", "premise")
# How a lesson relates to another memory (post-audit 4), the builder's account, never proof:
# depends_on: it holds only while that holds (a premise); builds_on: it combines that with new experience (an
# ingredient of a derived possibility); same_as: the same understanding held there too; distinct: alike in words,
# different in meaning; supports / challenges: that memory bears on it; related: context only (a premise a
# reassessment found it doesn't rest on after all). Only depends_on and builds_on are its basis:
# when their target changes, the lesson is unsettled until reassessed. No link is ever support.
LESSON_LINK_RELATIONS = ("depends_on", "builds_on", "same_as", "distinct", "supports", "challenges", "related")
LESSON_BASIS_RELATIONS = ("depends_on", "builds_on")
LESSON_LINK_TARGETS = ("lesson", "fact", "item")


class MemoryLesson(Base):
    """One lesson as it stands now (its current revision). ``conflicts_with``: lesson ids it was found to point
    against, equally relevant; shown together and flagged, never silently resolved."""

    __tablename__ = "memory_lessons"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    applies_when: Mapped[str] = mapped_column(Text, nullable=False)
    exceptions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    # The user's exact words it rests on (a preference), never reworded.
    user_words: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    conflicts_with: Mapped[list] = mapped_column(JSONB, nullable=False, default=list,
                                                 server_default=text("'[]'::jsonb"))
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What form of understanding it is, in the builder's words ("an expectation about how they joke"): descriptive,
    # never a category code acts on. And the competing explanations kept beside it (post-audit 3).
    form: Mapped[str | None] = mapped_column(Text, nullable=True)
    alternatives: Mapped[list] = mapped_column(JSONB, nullable=False, default=list,
                                               server_default=text("'[]'::jsonb"))
    # Post-audit 4, all optional free text in the builder's words: what part of it is untested (a possibility she
    # has not seen work), whether she has tried it ("untried" / "tried"), what it needs (prerequisites,
    # capabilities, including ones she lacks), and what later observation would support or challenge it.
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    # When it was withdrawn, and when the evidence that withdrew it happened: older work can't bring it back.
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdrawn_occasion_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(_in("kind", LESSON_KINDS), name="ck_memory_lessons_kind"),
        CheckConstraint(_in("origin", LESSON_ORIGINS), name="ck_memory_lessons_origin"),
        CheckConstraint(_in("scope", LESSON_SCOPES), name="ck_memory_lessons_scope"),
        CheckConstraint(_in("status", LESSON_STATUSES), name="ck_memory_lessons_status"),
        CheckConstraint("btrim(statement) <> '' AND btrim(applies_when) <> ''", name="ck_memory_lessons_text"),
        CheckConstraint("revision >= 1", name="ck_memory_lessons_revision"),
        CheckConstraint("(status = 'withdrawn') = (withdrawn_at IS NOT NULL)", name="ck_memory_lessons_withdrawn"),
        # A stated preference keeps the user's words; an inference has none of its own.
        CheckConstraint("(kind = 'preference') = (origin = 'stated')", name="ck_memory_lessons_origin_kind"),
        UniqueConstraint("id", "user_id", name="uq_memory_lessons_id_user"),
        Index("ix_memory_lessons_user", "user_id", "status"),
    )


class MemoryLessonRevision(Base):
    """Every revision of a lesson, whole: what it said, its status, the operation that made it and why, and the
    assessment revision (and lesson job) it came from. Never rewritten."""

    __tablename__ = "memory_lesson_revisions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lesson_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("memory_lessons.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    op: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    applies_when: Mapped[str] = mapped_column(Text, nullable=False)
    exceptions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    user_words: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    form: Mapped[str | None] = mapped_column(Text, nullable=True)
    alternatives: Mapped[list] = mapped_column(JSONB, nullable=False, default=list,
                                               server_default=text("'[]'::jsonb"))
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # A revision made without new evidence (post-audit 4): "critique" (her mind questioned its reasoning and it was
    # read again on the same evidence) or "premise" (something it rests on changed). Never new support.
    prompted_by: Mapped[str | None] = mapped_column(String(16), nullable=True)
    assessment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)  # opaque
    assessment_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)  # opaque
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(_in("op", LESSON_OPS), name="ck_memory_lesson_revisions_op"),
        CheckConstraint(_in("status", LESSON_STATUSES), name="ck_memory_lesson_revisions_status"),
        CheckConstraint(f"prompted_by IS NULL OR {_in('prompted_by', LESSON_PROMPTS)}",
                        name="ck_memory_lesson_revisions_prompted"),
        UniqueConstraint("lesson_id", "revision", name="uq_memory_lesson_revisions"),
    )


class MemoryLessonEvidence(Base):
    """What a lesson rests on or was found against, once per occasion and role. An occasion is one assessed
    exchange (its trigger key): citing it twice, or assessing it again, is never a second occasion.
    ``current`` is false once the assessment it came from was revised and not yet read again."""

    __tablename__ = "memory_lesson_evidence"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lesson_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("memory_lessons.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(12), nullable=False)
    occasion: Mapped[str] = mapped_column(String(160), nullable=False)
    occasion_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    assessment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)  # opaque
    assessment_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The stored records it cites (``message:<id>``, ``attempt:<id>``): how forgetting finds it.
    refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    why: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    added_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(_in("role", LESSON_EVIDENCE_ROLES), name="ck_memory_lesson_evidence_role"),
        UniqueConstraint("lesson_id", "role", "occasion", name="uq_memory_lesson_evidence"),
        Index("ix_memory_lesson_evidence_assessment", "user_id", "assessment_id"),
        Index("ix_memory_lesson_evidence_refs", "refs", postgresql_using="gin"),
    )


class MemoryLessonLink(Base):
    """How a lesson relates to another memory of the same user: another lesson, a fact, or one of her working items
    (post-audit 4). The builder's account of the relationship, never support and never proof. ``target_revision``
    and ``target_status`` are the target as it was when the link was made or last reassessed: when the target's
    current revision or status differs (or it is gone), a basis link (``depends_on`` / ``builds_on``) makes the
    lesson unsettled until a reassessment reads it again. ``target_text``: the target's words then."""

    __tablename__ = "memory_lesson_links"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lesson_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("memory_lessons.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    relation: Mapped[str] = mapped_column(String(16), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(8), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)  # a uuid (lesson, fact, item)
    target_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    target_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    why: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # The lesson revision that made or last reassessed it, and what that reassessment said.
    lesson_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    settled_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(_in("relation", LESSON_LINK_RELATIONS), name="ck_memory_lesson_links_relation"),
        CheckConstraint(_in("target_kind", LESSON_LINK_TARGETS), name="ck_memory_lesson_links_target"),
        ForeignKeyConstraint(["lesson_id", "user_id"], ["memory_lessons.id", "memory_lessons.user_id"],
                             ondelete="CASCADE", name="fk_memory_lesson_links_lesson_user"),
        UniqueConstraint("lesson_id", "target_kind", "target_id", name="uq_memory_lesson_links"),
        Index("ix_memory_lesson_links_target", "user_id", "target_kind", "target_id"),
    )


class MemoryLessonUse(Base):
    """Which lesson revision a consumer had for one decision (its capture attempt), whether it actually received
    it (and why not: cut by the budget, a consumer whose input has no lessons, supply switched off), and what the
    consumer said about it. Receiving is not influence; the report is the consumer's own account."""

    __tablename__ = "memory_lesson_uses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    lesson_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("memory_lessons.id", ondelete="CASCADE"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    consumer: Mapped[str] = mapped_column(String(24), nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)  # opaque
    received: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ref: Mapped[str | None] = mapped_column(String(8), nullable=True)  # its label in the input ("L1")
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reported: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reported_why: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(f"reported IS NULL OR {_in('reported', LESSON_REPORTED)}", name="ck_memory_lesson_uses_reported"),
        CheckConstraint("received OR reported IS NULL", name="ck_memory_lesson_uses_received"),
        UniqueConstraint("attempt_id", "lesson_id", "consumer", name="uq_memory_lesson_uses"),
        Index("ix_memory_lesson_uses_attempt", "user_id", "attempt_id"),
        Index("ix_memory_lesson_uses_lesson", "lesson_id", "created_at"),
    )


class LearningLessonJob(Base):
    """Learning's work of reading one assessment revision for lessons: once per (assessment, revision), leased,
    retried with backoff. A job whose assessment was revised meanwhile ends as ``superseded`` (the newer
    revision has its own job). Post-audit 4: a ``reconsider`` job reassesses one lesson on the evidence it already
    rests on (a premise changed, or her mind questioned its reasoning), once per ``job_key``; ``input`` keeps what
    prompted it."""

    __tablename__ = "learning_lesson_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("learning_assessments.id", ondelete="CASCADE"), nullable=True)
    assessment_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default="assessment", server_default="assessment")
    lesson_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("memory_lessons.id", ondelete="CASCADE"),
                                                        nullable=True)
    job_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    input: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    reason: Mapped[str] = mapped_column(String(300), nullable=False, default="", server_default="")
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                      nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(_in("state", LESSON_JOB_STATES), name="ck_learning_lesson_jobs_state"),
        CheckConstraint(_in("kind", LESSON_JOB_KINDS), name="ck_learning_lesson_jobs_kind"),
        CheckConstraint("(kind = 'assessment') = (assessment_id IS NOT NULL AND assessment_revision IS NOT NULL)",
                        name="ck_learning_lesson_jobs_assessment"),
        CheckConstraint("(kind = 'reconsider') = (lesson_id IS NOT NULL AND job_key IS NOT NULL)",
                        name="ck_learning_lesson_jobs_reconsider"),
        UniqueConstraint("assessment_id", "assessment_revision", name="uq_learning_lesson_jobs"),
        UniqueConstraint("job_key", name="uq_learning_lesson_jobs_key"),
        Index("ix_learning_lesson_jobs_due", "next_attempt_at",
              postgresql_where=text("state IN ('pending', 'running')")),
    )


# --------------------------------------------------------------------------- #
# Learning step 4: predictions and bounded game trials (docs/learning-system/04-implementation.md)
# --------------------------------------------------------------------------- #

# proposed -> presented -> (deferred | rejected | expired | selected); selected -> preparing -> executing
# (<-> paused) -> observing -> assessing -> assessed. Every selected trial ends assessed.
TRIAL_STATUSES = ("proposed", "presented", "deferred", "rejected", "expired", "selected", "preparing", "executing",
                  "paused", "observing", "assessing", "assessed")
TRIAL_PENDING = ("proposed", "presented", "deferred")  # a proposal her mind hasn't taken up
TRIAL_LIVE = ("selected", "preparing", "executing", "paused", "observing", "assessing")  # taken up, not assessed
TRIAL_CLOSED = ("rejected", "expired", "assessed")
TRIAL_HYPOTHESIS = ("supported", "contradicted", "unresolved")
TRIAL_TRIGGER_KINDS = ("struggling", "stuck", "hypothesis", "approach_changed")
TRIAL_TRIGGER_STATES = ("pending", "running", "done", "failed")


class LearningTrial(Base):
    """One bounded experiment in a game, learning's authoritative record of it: the problem it tests, its current
    proposal revision (the revisions themselves are ``learning_trial_revisions``, never rewritten), her mind's
    choice, preparation, execution, its costs against its limits, and how it ended. ``hypothesis_item_id`` /
    ``reminder_item_id`` are the working items memory keeps for it (her hypothesis; a deferral's reminder)."""

    __tablename__ = "learning_trials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("desktop_sessions.id", ondelete="SET NULL"), nullable=True)
    game: Mapped[str] = mapped_column(String(32), nullable=False)
    scenario: Mapped[str] = mapped_column(String(32), nullable=False)
    # The unresolved problem it is about (one pending proposal per problem: new evidence revises it).
    problem_key: Mapped[str] = mapped_column(String(160), nullable=False)
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed", server_default="proposed")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    hypothesis_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reminder_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # A materially different test of the same question, or the trial whose changed approach this one predicts.
    linked_trial_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("learning_trials.id", ondelete="SET NULL"), nullable=True)
    # Her mind's choice: {decision_id, alternative, revision, reason, other_goal, handling, at}; never changed.
    selection: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    selected_alternative: Mapped[str | None] = mapped_column(String(8), nullable=True)
    selected_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The deferral in force {reason, condition, since, expires_at, decision_id}; every one is also kept in order.
    deferral: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    deferrals: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    # {attempts, seconds, blocks}: the agreed limits, fixed when it is selected.
    limits: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    # Counted spending, preparation kept apart from the test: {prep_seconds, test_seconds, attempts, test_blocks,
    # prep_blocks}.
    costs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("game_skill_attempts.id", ondelete="SET NULL"), nullable=True)
    # How it ended, in words, and the kind of ending (completed, limit, stopped, interrupted, not_started, ...).
    ending: Mapped[str | None] = mapped_column(String(24), nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    presentations: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    decide_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                    nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When the stretch now being spent began (selected, resumed): the time budget counts active stretches only.
    active_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    presented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exec_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exec_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(_in("status", TRIAL_STATUSES), name="ck_learning_trials_status"),
        CheckConstraint("revision >= 1", name="ck_learning_trials_revision"),
        CheckConstraint(f"(closed_at IS NOT NULL) = ({_in('status', TRIAL_CLOSED)})", name="ck_learning_trials_closed"),
        # Taken up means her mind chose an alternative of a revision.
        CheckConstraint(f"NOT ({_in('status', TRIAL_LIVE)} OR status = 'assessed') OR "
                        "(selection IS NOT NULL AND selected_alternative IS NOT NULL AND selected_revision IS NOT NULL)",
                        name="ck_learning_trials_selection"),
        UniqueConstraint("id", "user_id", name="uq_learning_trials_id_user"),
        ForeignKeyConstraint(["hypothesis_item_id", "user_id"], ["working_items.id", "working_items.user_id"],
                             ondelete="SET NULL (hypothesis_item_id)", name="fk_learning_trials_hypothesis"),
        ForeignKeyConstraint(["reminder_item_id", "user_id"], ["working_items.id", "working_items.user_id"],
                             ondelete="SET NULL (reminder_item_id)", name="fk_learning_trials_reminder"),
        # One proposal per unresolved problem waiting for her mind: new evidence revises it.
        Index("uq_learning_trials_pending_problem", "user_id", "problem_key", unique=True,
              postgresql_where=text("status IN ('proposed', 'presented', 'deferred')")),
        # One experiment at a time per session: her body runs one.
        Index("uq_learning_trials_live_session", "session_id", unique=True,
              postgresql_where=text("status IN ('selected', 'preparing', 'executing', 'paused', 'observing', "
                                    "'assessing')")),
        Index("ix_learning_trials_due", "next_check_at", postgresql_where=text("closed_at IS NULL")),
        Index("ix_learning_trials_user", "user_id", "created_at"),
    )


class LearningTrialRevision(Base):
    """One proposal revision, whole and checked, as learning made it before her mind saw it: hypothesis,
    alternatives with their predicted observations, uncertainty, prerequisites, observation window, costs, limits
    and recommendation. Immutable (a trigger refuses updates), and no revision is added once her mind selected one:
    expectations are never rewritten after the fact."""

    __tablename__ = "learning_trial_revisions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trial_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("learning_trials.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    proposal: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # What code dropped from the model's answer, with why.
    dropped: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # The evidence it was made from ("experience:12", "event:3", "trial:<id>"), and the start state's position.
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("trial_id", "revision", name="uq_learning_trial_revisions"),
    )


class LearningTrialEvent(Base):
    """What happened to a trial, in order, append-only (a trigger refuses updates): proposal, presentation,
    her mind's decision (its capture attempt), deferral and reconsideration, preparation, readiness checks,
    execution and each attempt, stops and limits, recovery, observations, assessment and what its owners did with
    it."""

    __tablename__ = "learning_trial_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trial_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("learning_trials.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    decision_attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)  # opaque
    skill_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("game_skill_attempts.id", ondelete="SET NULL"), nullable=True)
    experience_id: Mapped[int | None] = mapped_column(
        ForeignKey("game_experiences.id", ondelete="SET NULL"), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_learning_trial_events_trial", "trial_id", "id"),
    )


class LearningTrialAssessment(Base):
    """One reading of a trial's evidence. Code measured the effects and checked each prediction (met / not met /
    unknown) and lists what is missing; the model's interpretation is kept only as far as code's checks allow.
    Later evidence adds a new reading (``n``); earlier ones stay, ``current`` false."""

    __tablename__ = "learning_trial_assessments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trial_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("learning_trials.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    hypothesis: Mapped[str] = mapped_column(String(16), nullable=False)
    test_started: Mapped[bool] = mapped_column(Boolean, nullable=False)
    measurements: Mapped[dict] = mapped_column(JSONB, nullable=False)
    prediction_checks: Mapped[list] = mapped_column(JSONB, nullable=False)
    missing: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    dropped: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(_in("hypothesis", TRIAL_HYPOTHESIS), name="ck_learning_trial_assessments_hypothesis"),
        UniqueConstraint("trial_id", "n", name="uq_learning_trial_assessments"),
        Index("uq_learning_trial_assessments_current", "trial_id", unique=True, postgresql_where=text("current")),
    )


class LearningTrialTrigger(Base):
    """Something learning may investigate for a trial: her hands struggling or stuck on a step of an active goal,
    an open hypothesis of hers in a game, an approach changed mid-trial. One pending per problem: repeats add their
    evidence to it. Investigating it is one model call and makes or revises a proposal, never an action."""

    __tablename__ = "learning_trial_triggers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("desktop_sessions.id", ondelete="CASCADE"),
                                                  nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    problem_key: Mapped[str] = mapped_column(String(160), nullable=False)
    # {problem, evidence: [{kind, text, at, refs}], step, hypothesis_item_id, trial_id}
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                      nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(_in("kind", TRIAL_TRIGGER_KINDS), name="ck_learning_trial_triggers_kind"),
        CheckConstraint(_in("state", TRIAL_TRIGGER_STATES), name="ck_learning_trial_triggers_state"),
        Index("uq_learning_trial_triggers_pending", "user_id", "problem_key", unique=True,
              postgresql_where=text("state IN ('pending', 'running')")),
        Index("ix_learning_trial_triggers_due", "next_attempt_at",
              postgresql_where=text("state IN ('pending', 'running')")),
    )


# Expectations are never rewritten: a revision is immutable and none is added once her mind selected one;
# the event log only grows.
# DDL() formats its text, so a literal percent sign is doubled.
LEARNING_TRIAL_FUNCTIONS_DDL = """
CREATE OR REPLACE FUNCTION learning_trial_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '%%: rows are never rewritten', TG_TABLE_NAME USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION learning_trial_revision_open() RETURNS trigger AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM learning_trials WHERE id = NEW.trial_id
               AND (selected_at IS NOT NULL OR user_id <> NEW.user_id)) THEN
        RAISE EXCEPTION 'learning_trial_revisions: the trial was selected (or is not this user''s)'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""
LEARNING_TRIAL_REVISION_DDL = """
CREATE TRIGGER learning_trial_revisions_immutable BEFORE UPDATE ON learning_trial_revisions
    FOR EACH ROW EXECUTE FUNCTION learning_trial_append_only();
CREATE TRIGGER learning_trial_revisions_open BEFORE INSERT ON learning_trial_revisions
    FOR EACH ROW EXECUTE FUNCTION learning_trial_revision_open();
"""
# Only a foreign key's SET NULL may touch an event (a deleted attempt or experience).
LEARNING_TRIAL_EVENT_DDL = """
CREATE TRIGGER learning_trial_events_append_only BEFORE UPDATE ON learning_trial_events
    FOR EACH ROW WHEN (OLD.skill_attempt_id IS NOT DISTINCT FROM NEW.skill_attempt_id
                       AND OLD.experience_id IS NOT DISTINCT FROM NEW.experience_id)
    EXECUTE FUNCTION learning_trial_append_only();
"""
event.listen(LearningTrial.__table__, "after_create",
             DDL(LEARNING_TRIAL_FUNCTIONS_DDL).execute_if(dialect="postgresql"))
event.listen(LearningTrialRevision.__table__, "after_create",
             DDL(LEARNING_TRIAL_REVISION_DDL).execute_if(dialect="postgresql"))
event.listen(LearningTrialEvent.__table__, "after_create",
             DDL(LEARNING_TRIAL_EVENT_DDL).execute_if(dialect="postgresql"))


class LearningReset(Base):
    """Per-user test-learning cutoff. Raw evidence and generated programs are retained."""
    __tablename__ = "learning_resets"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
