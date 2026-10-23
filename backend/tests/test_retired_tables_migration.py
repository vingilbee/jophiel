"""Migration e4c8a2f6b9d3 drops the retired Cognitive V0 shadow and dialogue-memory tables and their guards, leaves
the message-order trigger alone, and downgrades to their exact earlier schema. Real PostgreSQL
(``TEST_DATABASE_URL``), in a database of its own."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

RETIRED = {"cognitive_shadow_jobs", "cognitive_shadow_traces", "cognitive_dialogue_memory_snapshots",
           "cognitive_dialogue_memory_heads", "cognitive_dialogue_memory_jobs", "cognitive_dialogue_interpretations"}
GUARDS = {"cognitive_shadow_jobs_guard", "cognitive_shadow_traces_guard", "cognitive_dialogue_memory_snapshots_guard",
          "cognitive_dialogue_interpretations_guard"}


def _schema(engine):
    with engine.connect() as connection:
        tables = set(connection.execute(text(
            "SELECT tablename FROM pg_tables WHERE tablename LIKE 'cognitive\\_%'")).scalars())
        functions = set(connection.execute(text(
            "SELECT proname FROM pg_proc WHERE pronamespace = 'public'::regnamespace")).scalars())
        shape = connection.execute(text(
            "SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull FROM pg_attribute a "
            "JOIN pg_class c ON c.oid = a.attrelid WHERE c.relname LIKE 'cognitive\\_%' AND c.relkind = 'r' "
            "AND a.attnum > 0 AND NOT a.attisdropped ORDER BY 1, 2")).all()
        objects = set(connection.execute(text(
            "SELECT conname FROM pg_constraint WHERE conrelid::regclass::text LIKE 'cognitive\\_%' "
            "UNION SELECT indexname FROM pg_indexes WHERE tablename LIKE 'cognitive\\_%' "
            "UNION SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgrelid::regclass::text LIKE 'cognitive\\_%'"
        )).scalars())
    return tables, functions, shape, objects


def test_retired_tables_drop_and_round_trip() -> None:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run the migration round trip")
    name = "retired_tables_" + uuid.uuid4().hex[:12]
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
        alembic("upgrade", "b3e7f9a1c5d2")
        before = _schema(engine)
        assert before[0] == RETIRED and GUARDS <= before[1]
        alembic("upgrade", "e4c8a2f6b9d3")
        tables, functions, _, _ = _schema(engine)
        assert tables == set() and not GUARDS & functions
        assert {"chat_messages_assign_sequence", "chat_messages_position_guard"} <= functions  # message order stays
        alembic("downgrade", "b3e7f9a1c5d2")
        assert _schema(engine) == before  # the exact earlier schema, empty
        alembic("upgrade", "head")
        assert _schema(engine)[0] == set()
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
