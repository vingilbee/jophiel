"""Message order is a database fact: ``chat_messages.conversation_sequence`` is allocated by a BEFORE INSERT
trigger under the conversation row lock. Recall, observation, the coordinator and autonomous speech order a
conversation by it. Real PostgreSQL (``TEST_DATABASE_URL``); each test runs in its own schema."""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.models import ChatMessage, Conversation
from app.services import chat as chat_service
from db_support import create_conversation, create_user, database, db_env  # noqa: F401


def test_every_append_path_allocates_contiguous_positions(db_env) -> None:
    with db_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        turn_id = uuid.uuid4()
        chat_service.add_message(db, conversation=conversation, role="user", content="hi", turn_id=turn_id)
        chat_service.add_message(db, conversation=conversation, role="assistant", content="hello",
                                 turn_id=turn_id)
        chat_service.add_message(db, conversation=conversation, role="assistant", content="A nudge.")  # unprompted
        db.add(ChatMessage(conversation_id=conversation.id, role="user", content="raw insert",
                           source_channel="whatsapp"))  # the WhatsApp batcher's direct insert
        db.commit()
        positions = list(db.scalars(select(ChatMessage.conversation_sequence)
                                    .where(ChatMessage.conversation_id == conversation.id)
                                    .order_by(ChatMessage.conversation_sequence)))
        counter = db.scalar(select(Conversation.last_message_sequence).where(Conversation.id == conversation.id))
    assert positions == [1, 2, 3, 4] and counter == 4


def test_concurrent_appends_never_duplicate_a_position(db_env) -> None:
    with db_env.db() as db:
        user = create_user(db)
        conversation_id = create_conversation(db, user).id
    errors = []

    def append(index: int) -> None:
        try:
            for step in range(10):
                with db_env.db() as db:
                    db.add(ChatMessage(conversation_id=conversation_id, role="user", content=f"{index}-{step}"))
                    db.commit()
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=append, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    with db_env.db() as db:
        positions = sorted(db.scalars(select(ChatMessage.conversation_sequence)
                                      .where(ChatMessage.conversation_id == conversation_id)))
    assert positions == list(range(1, 81))


def test_positions_cannot_be_supplied_or_changed(db_env) -> None:
    with db_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        message = chat_service.add_message(db, conversation=conversation, role="user", content="hi")
        conversation_id, message_id = conversation.id, message.id
    with db_env.db() as db:
        with pytest.raises((IntegrityError, DBAPIError)):
            db.execute(text("INSERT INTO chat_messages (id, conversation_id, role, content, status, source_channel, "
                            "conversation_sequence, created_at) VALUES (:id, :c, 'user', 'x', 'completed', 'web', "
                            "99, now())"), {"id": uuid.uuid4(), "c": conversation_id})
            db.commit()
        db.rollback()
        with pytest.raises((IntegrityError, DBAPIError)):
            db.execute(update(ChatMessage).where(ChatMessage.id == message_id).values(conversation_sequence=7))
            db.commit()


def test_migration_backfills_positions_and_round_trips_in_an_isolated_database() -> None:
    import os
    import subprocess
    import sys
    from pathlib import Path

    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run the migration round trip")
    name = "sequence_migration_" + uuid.uuid4().hex[:12]
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    target = make_url(url).set(database=name).render_as_string(hide_password=False)
    backend = Path(__file__).resolve().parents[1]

    def alembic(*args: str) -> None:
        subprocess.run([sys.executable, "-m", "alembic", *args], cwd=backend, check=True, capture_output=True,
                       env={**os.environ, "DATABASE_URL": target, "PYTHONPATH": str(backend)})

    engine = create_engine(target)
    try:
        alembic("upgrade", "a7c4e9b2d1f3")
        user, conversation, first, second = (uuid.uuid4() for _ in range(4))
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO users (id, name, email, hashed_password, created_at) "
                                    "VALUES (:u, 'x', :e, 'h', now())"), {"u": user, "e": f"{user}@x"})
            connection.execute(text("INSERT INTO conversations (id, user_id, title, created_at, updated_at) "
                                    "VALUES (:c, :u, 't', now(), now())"), {"c": conversation, "u": user})
            rows = [  # (turn, role, created_at): the second turn's rows share one timestamp
                (first, "user", "2026-01-01 10:00:00"), (first, "assistant", "2026-01-01 10:00:30"),
                (None, "assistant", "2026-01-01 10:01:00"),
                (second, "assistant", "2026-01-01 10:02:00"), (second, "user", "2026-01-01 10:02:00"),
            ]
            ids = []
            for turn, role, created in rows:
                message = uuid.uuid4()
                ids.append((role, turn))
                connection.execute(text("INSERT INTO chat_messages (id, turn_id, conversation_id, role, content, "
                                        "created_at) VALUES (:i, :t, :c, :r, 'x', :at)"),
                                   {"i": message, "t": turn, "c": conversation, "r": role, "at": created})
            for turn, created in ((first, "2026-01-01 10:00:00"), (second, "2026-01-01 10:02:00")):
                connection.execute(text("INSERT INTO chat_jobs (id, turn_id, user_id, conversation_id, payload, "
                                        "status, attempts, created_at, updated_at) VALUES (:i, :t, :u, :c, '{}', "
                                        "'completed', 1, :at, now())"),
                                   {"i": uuid.uuid4(), "t": turn, "u": user, "c": conversation, "at": created})
        alembic("upgrade", "head")
        with engine.begin() as connection:
            ordered = connection.execute(text(
                "SELECT role, turn_id, conversation_sequence FROM chat_messages ORDER BY conversation_sequence"
            )).all()
            counter = connection.execute(text("SELECT last_message_sequence FROM conversations")).scalar_one()
        assert [(row.role, row.turn_id) for row in ordered] == [
            ("user", first), ("assistant", first), ("assistant", None), ("user", second), ("assistant", second)]
        assert [row.conversation_sequence for row in ordered] == [1, 2, 3, 4, 5] and counter == 5
        engine.dispose()
        alembic("downgrade", "a7c4e9b2d1f3")
        with engine.begin() as connection:
            tables = set(connection.execute(text(
                "SELECT tablename FROM pg_tables WHERE tablename LIKE 'cognitive_dialogue%'")).scalars())
            columns = set(connection.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'chat_messages'")).scalars())
        assert tables == set() and "conversation_sequence" not in columns
        engine.dispose()
        alembic("upgrade", "head")
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
