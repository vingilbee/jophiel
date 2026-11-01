"""Exercise the step-7 upgrade and downgrade against a real, disposable PostgreSQL schema."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from db_support import database  # noqa: F401


def test_upgrade_from_step6_and_downgrade_when_empty(database):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/b3e7f9a1c5d2_background_coordination.py"
    spec = importlib.util.spec_from_file_location("step7_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with database() as db:
        connection = db.connection()
        migration.op = Operations(MigrationContext.configure(connection))
        migration.downgrade()  # metadata starts at current schema; return to the actual prior shape
        columns = {c["name"]: c for c in inspect(connection).get_columns("coordinator_work")}
        assert "next_attempt_at" not in columns and not columns["conversation_id"]["nullable"]
        migration.upgrade()
        columns = {c["name"]: c for c in inspect(connection).get_columns("coordinator_work")}
        assert "next_attempt_at" in columns and columns["conversation_id"]["nullable"]
        checks = {c["name"]: c["sqltext"] for c in inspect(connection).get_check_constraints("coordinator_work")}
        assert "observation" in checks["ck_coordinator_work_kind"]
        assert "blocked" in checks["ck_coordinator_work_status"]
        db.commit()
