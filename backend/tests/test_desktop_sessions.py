"""Desktop agent session service and routes (plan §6, §17-18, stage A gate).

Real PostgreSQL only: the guarantees under test (one live session per
environment, row locks, the partial unique indexes) are database behaviour a
mock can't show. Skipped unless ``TEST_DATABASE_URL`` is set; each test gets a
throwaway schema. Use a disposable server, never the dev database, e.g.:

    docker run -d --rm --name desktop-test-pg -e POSTGRES_PASSWORD=test \\
        -p 127.0.0.1:55499:5432 postgres:16
    TEST_DATABASE_URL=postgresql+psycopg://postgres:test@127.0.0.1:55499/postgres \\
        uv run pytest tests/test_desktop_sessions.py
"""
from __future__ import annotations

import threading
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Conversation, DesktopSession, DesktopSessionEvent, User
from app.schemas.desktop import EnvironmentCreate, PlanAppend, PlanReplace, SessionCreate
from app.services import desktop_sessions as svc
from app.services.desktop_sessions import DesktopError
from tests.cognitive_shadow_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)

PLAN = {"goal": "collect wood", "steps": [{"do": "find a tree", "done_when": "a tree is centered"},
                                          {"do": "punch the trunk", "done_when": "logs in hotbar"}]}


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def db(database):
    with database() as session:
        yield session


def _user(db, name="player") -> User:
    user = User(name=name, email=f"{name}-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x")
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def alice(db):
    return _user(db, "alice")


@pytest.fixture
def bob(db):
    return _user(db, "bob")


def _env(db, user, profiles=("minecraft_java",)):
    return svc.register_environment(
        db, user.id, EnvironmentCreate(display_name="Legion VM", allowed_profile_ids=list(profiles))
    )


def _start(db, user, env, **extra) -> DesktopSession:
    session, _ = svc.start_session(
        db, user.id, SessionCreate(environment_id=env.id, profile_id="minecraft_java", **extra)
    )
    return session


def _running(db, user, env) -> DesktopSession:
    session = _start(db, user, env)
    svc.controller_online(db, env.id, controller_generation=1)
    return svc.controller_ready(db, session.id)


def _code(excinfo) -> str:
    return excinfo.value.code


def _event_types(db, session) -> list[tuple[str, str | None]]:
    rows = db.scalars(
        select(DesktopSessionEvent).where(DesktopSessionEvent.session_id == session.id).order_by(DesktopSessionEvent.id)
    )
    return [(row.type, row.payload.get("state")) for row in rows]


# --------------------------------------------------------------------------- #
# Profiles and environments
# --------------------------------------------------------------------------- #


def test_profile_listing_excludes_the_unfinished_template():
    ids = [p.id for p in svc.list_profiles()]
    assert "minecraft_java" in ids
    assert "generic_keyboard_mouse" not in ids  # template: target.process unset


def test_environment_refuses_template_and_unknown_profiles(db, alice):
    with pytest.raises(DesktopError) as excinfo:
        _env(db, alice, profiles=("generic_keyboard_mouse",))
    assert _code(excinfo) == "profile_invalid"
    with pytest.raises(DesktopError) as excinfo:
        _env(db, alice, profiles=("no_such_game",))
    assert _code(excinfo) == "profile_not_found"


def test_environments_are_listed_per_owner(db, alice, bob):
    _env(db, alice)
    assert len(svc.list_environments(db, alice.id)) == 1
    assert svc.list_environments(db, bob.id) == []


# --------------------------------------------------------------------------- #
# Start and ownership (stage A gate)
# --------------------------------------------------------------------------- #


def test_start_creates_a_starting_session_without_control(db, alice):
    session = _start(db, alice, _env(db, alice))
    assert (session.state, session.control_owner, session.state_revision) == ("starting", "none", 1)
    assert session.profile_revision == 1
    assert _event_types(db, session) == [("desktop.session_state", "starting")]


def test_users_cannot_see_or_control_each_others_sessions(db, alice, bob):
    env = _env(db, alice)
    session = _start(db, alice, env)
    for action in (
        lambda: svc.get_session(db, bob.id, session.id),
        lambda: svc.pause_session(db, bob.id, session.id),
        lambda: svc.takeover_session(db, bob.id, session.id),
        lambda: svc.end_session(db, bob.id, session.id),
        lambda: svc.resume_session(db, bob.id, session.id, expected_state_revision=1),
        lambda: svc.list_events(db, bob.id, session.id),
        lambda: svc.revoke_environment(db, bob.id, env.id),
        lambda: svc.start_session(db, bob.id, SessionCreate(environment_id=env.id, profile_id="minecraft_java")),
    ):
        db.rollback()
        with pytest.raises(DesktopError) as excinfo:
            action()
        assert (_code(excinfo), excinfo.value.status) == ("not_found", 404)
    assert svc.list_sessions(db, bob.id) == []
    db.refresh(session)
    assert session.state == "starting"


def test_start_rejects_a_conversation_the_user_does_not_own(db, alice, bob):
    conversation = Conversation(user_id=bob.id, title="bob's")
    db.add(conversation)
    db.commit()
    with pytest.raises(DesktopError) as excinfo:
        _start(db, alice, _env(db, alice), conversation_id=conversation.id)
    assert _code(excinfo) == "not_found"


def test_start_rejects_profiles_outside_the_environment_policy(db, alice, monkeypatch):
    env = _env(db, alice)
    env.allowed_profile_ids = ["some_other_game"]
    db.commit()
    with pytest.raises(DesktopError) as excinfo:
        _start(db, alice, env)
    assert (_code(excinfo), excinfo.value.status) == ("not_authorized", 403)


def test_second_live_session_on_one_environment_is_refused(db, alice):
    env = _env(db, alice)
    _start(db, alice, env)
    with pytest.raises(DesktopError) as excinfo:
        _start(db, alice, env)
    assert _code(excinfo) == "already_owned"


def test_simultaneous_starts_yield_exactly_one_owner(database, alice):
    with database() as setup:
        env = _env(setup, alice)
    barrier = threading.Barrier(6)
    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt():
        with database() as db:
            barrier.wait()
            try:
                _start(db, alice, env)
                result = "won"
            except DesktopError as exc:
                result = exc.code
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=attempt) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["already_owned"] * 5 + ["won"]


def test_ending_frees_the_environment_for_a_new_session(db, alice):
    env = _env(db, alice)
    first = _start(db, alice, env)
    svc.end_session(db, alice.id, first.id)
    second = _start(db, alice, env)
    assert second.id != first.id


def test_idempotency_key_returns_the_same_session(db, alice):
    env = _env(db, alice)
    request = SessionCreate(environment_id=env.id, profile_id="minecraft_java", idempotency_key="tool-call-123")
    first, created = svc.start_session(db, alice.id, request)
    again, created_again = svc.start_session(db, alice.id, request)
    assert (created, created_again, again.id) == (True, False, first.id)

    changed = request.model_copy(update={"commentary_mode": "quiet"})
    with pytest.raises(DesktopError) as excinfo:
        svc.start_session(db, alice.id, changed)
    assert _code(excinfo) == "idempotency_conflict"


def test_concurrent_retries_with_one_key_create_one_session(database, alice):
    with database() as setup:
        env = _env(setup, alice)
    request = SessionCreate(environment_id=env.id, profile_id="minecraft_java", idempotency_key="tool-call-race")
    barrier = threading.Barrier(4)
    ids: list[uuid.UUID] = []
    lock = threading.Lock()

    def attempt():
        with database() as db:
            barrier.wait()
            session, _ = svc.start_session(db, alice.id, request)
        with lock:
            ids.append(session.id)

    threads = [threading.Thread(target=attempt) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(ids) == 4 and len(set(ids)) == 1


def test_duration_above_the_configured_maximum_is_refused(db, alice):
    with pytest.raises(DesktopError) as excinfo:
        _start(db, alice, _env(db, alice), max_duration_seconds=get_settings().desktop_max_session_seconds + 1)
    assert _code(excinfo) == "limit_reached"


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


def test_controller_ready_moves_starting_to_running_only_when_online(db, alice):
    env = _env(db, alice)
    session = _start(db, alice, env)
    with pytest.raises(DesktopError) as excinfo:
        svc.controller_ready(db, session.id)
    assert _code(excinfo) == "environment_offline"
    db.rollback()
    svc.controller_online(db, env.id, controller_generation=1)
    session = svc.controller_ready(db, session.id)
    assert (session.state, session.control_owner, session.state_revision) == ("running", "gameplay", 2)


def test_every_stop_fences_input_and_reports_pending_release(db, alice):
    env = _env(db, alice)
    session = _running(db, alice, env)
    generation = session.execution_generation

    paused = svc.pause_session(db, alice.id, session.id)
    assert (paused.session.state, paused.session.control_owner, paused.release_pending) == ("paused", "none", True)
    assert paused.session.execution_generation == generation + 1

    resumed = svc.resume_session(db, alice.id, session.id, expected_state_revision=paused.session.state_revision)
    assert resumed.session.control_owner == "gameplay"

    manual = svc.takeover_session(db, alice.id, session.id)
    assert (manual.session.state, manual.session.control_owner, manual.release_pending) == ("manual", "manual", True)
    assert manual.session.execution_generation == generation + 2

    ended = svc.end_session(db, alice.id, session.id)
    assert (ended.session.state, ended.session.control_owner) == ("ended", "none")
    assert ended.release_pending is False  # manual held input, not gameplay
    assert ended.session.ended_at is not None
    assert ended.session.execution_generation == generation + 3


def test_pause_and_end_are_idempotent(db, alice):
    session = _running(db, alice, _env(db, alice))
    first = svc.pause_session(db, alice.id, session.id)
    revision = first.session.state_revision
    second = svc.pause_session(db, alice.id, session.id)
    assert second.session.state_revision == revision and second.release_pending is False

    svc.end_session(db, alice.id, session.id)
    revision = svc.get_session(db, alice.id, session.id).state_revision
    again = svc.end_session(db, alice.id, session.id)
    assert again.session.state_revision == revision


def test_ended_sessions_cannot_be_resumed_paused_or_planned(db, alice):
    session = _running(db, alice, _env(db, alice))
    ended = svc.end_session(db, alice.id, session.id).session
    for action in (
        lambda: svc.resume_session(db, alice.id, session.id, expected_state_revision=ended.state_revision),
        lambda: svc.pause_session(db, alice.id, session.id),
        lambda: svc.takeover_session(db, alice.id, session.id),
        lambda: svc.replace_plan(db, alice.id, session.id, PlanReplace(expected_plan_revision=0, **PLAN)),
    ):
        db.rollback()
        with pytest.raises(DesktopError) as excinfo:
            action()
        assert _code(excinfo) == "session_ended"


def test_end_beats_a_resume_built_on_the_old_snapshot(db, alice):
    session = _running(db, alice, _env(db, alice))
    snapshot = svc.pause_session(db, alice.id, session.id).session.state_revision
    svc.end_session(db, alice.id, session.id)
    with pytest.raises(DesktopError) as excinfo:
        svc.resume_session(db, alice.id, session.id, expected_state_revision=snapshot)
    assert _code(excinfo) == "session_ended"
    assert svc.get_session(db, alice.id, session.id).state == "ended"


def test_resume_needs_the_current_revision(db, alice):
    session = _running(db, alice, _env(db, alice))
    paused = svc.pause_session(db, alice.id, session.id).session
    with pytest.raises(DesktopError) as excinfo:
        svc.resume_session(db, alice.id, session.id, expected_state_revision=paused.state_revision - 1)
    assert _code(excinfo) == "stale_revision"


def test_concurrent_end_and_resume_never_leave_a_running_session(database, alice):
    with database() as setup:
        env = _env(setup, alice)
        session = _running(setup, alice, env)
        revision = svc.pause_session(setup, alice.id, session.id).session.state_revision
    barrier = threading.Barrier(2)
    errors: list[str] = []

    def end():
        with database() as db:
            barrier.wait()
            svc.end_session(db, alice.id, session.id)

    def resume():
        with database() as db:
            barrier.wait()
            try:
                svc.resume_session(db, alice.id, session.id, expected_state_revision=revision)
            except DesktopError as exc:
                errors.append(exc.code)

    threads = [threading.Thread(target=end), threading.Thread(target=resume)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    with database() as db:
        assert svc.get_session(db, alice.id, session.id).state == "ended"
    # Either resume lost the race outright, or it won and end ended it after.
    assert errors in ([], ["session_ended"])


def test_disconnect_never_auto_resumes(db, alice):
    env = _env(db, alice)
    session = _running(db, alice, env)
    svc.controller_offline(db, env.id)
    db.refresh(session)
    assert (session.state, session.control_owner, session.reason_code) == ("disconnected", "none", "controller_lost")

    svc.controller_online(db, env.id, controller_generation=2)
    db.refresh(session)
    assert session.state == "disconnected"  # reconnect restores health, not control

    resumed = svc.resume_session(db, alice.id, session.id, expected_state_revision=session.state_revision)
    assert resumed.session.state == "running"


def test_resume_refuses_while_the_controller_is_offline(db, alice):
    env = _env(db, alice)
    session = _running(db, alice, env)
    paused = svc.pause_session(db, alice.id, session.id).session
    svc.controller_offline(db, env.id)
    db.refresh(paused)
    assert paused.state == "paused"  # only running/starting sessions become disconnected
    with pytest.raises(DesktopError) as excinfo:
        svc.resume_session(db, alice.id, session.id, expected_state_revision=paused.state_revision)
    assert _code(excinfo) == "environment_offline"


def test_revoking_an_environment_ends_its_session_and_blocks_new_ones(db, alice):
    env = _env(db, alice)
    session = _running(db, alice, env)
    svc.revoke_environment(db, alice.id, env.id)
    db.refresh(session)
    assert (session.state, session.reason_code) == ("ended", "environment_revoked")
    with pytest.raises(DesktopError) as excinfo:
        _start(db, alice, env)
    assert _code(excinfo) == "not_authorized"
    db.rollback()
    with pytest.raises(DesktopError):
        svc.controller_online(db, env.id, controller_generation=3)


def test_overdue_sessions_end_with_limit_reached(db, alice):
    session = _start(db, alice, _env(db, alice), max_duration_seconds=60)
    assert svc.expire_overdue(db, now=session.started_at + timedelta(seconds=59)) == 0
    assert svc.expire_overdue(db, now=session.started_at + timedelta(seconds=60)) == 1
    db.refresh(session)
    assert (session.state, session.reason_code) == ("ended", "limit_reached")


def test_lifecycle_history_is_persisted_in_order(db, alice):
    session = _running(db, alice, _env(db, alice))
    svc.pause_session(db, alice.id, session.id)
    svc.end_session(db, alice.id, session.id)
    assert [state for _, state in _event_types(db, session)] == ["starting", "running", "paused", "ended"]
    revisions = [e.state_revision for e in svc.list_events(db, alice.id, session.id)]
    assert revisions == [1, 2, 3, 4]
    assert [e.id for e in svc.list_events(db, alice.id, session.id, after_id=0, limit=2)] == \
        [e.id for e in svc.list_events(db, alice.id, session.id)][:2]


# --------------------------------------------------------------------------- #
# Plan
# --------------------------------------------------------------------------- #


def test_plan_at_start_is_revision_one_with_service_assigned_ids(db, alice):
    session = _start(db, alice, _env(db, alice), plan=PLAN)
    assert session.plan_revision == 1
    assert [s["id"] for s in session.plan["steps"]] == ["s1", "s2"]
    assert all(s["state"] == "pending" for s in session.plan["steps"])
    assert session.execution_generation == 1  # the first plan fences nothing


def test_replace_bumps_revision_and_fences_input(db, alice):
    session = _start(db, alice, _env(db, alice), plan=PLAN)
    generation = session.execution_generation
    replaced = svc.replace_plan(db, alice.id, session.id, PlanReplace(
        expected_plan_revision=1, goal="build a shelter", steps=[{"do": "dig in", "done_when": "walls around"}],
    ))
    assert replaced.plan_revision == 2
    assert replaced.execution_generation == generation + 1
    assert [s["id"] for s in replaced.plan["steps"]] == ["s3"]  # ids never reused
    assert replaced.plan["goal"] == "build a shelter"


def test_append_keeps_revision_and_generation(db, alice):
    session = _start(db, alice, _env(db, alice), plan=PLAN)
    generation = session.execution_generation
    appended = svc.append_plan_steps(db, alice.id, session.id, PlanAppend(
        expected_plan_revision=1, steps=[{"do": "craft planks", "done_when": "planks in inventory"}],
    ))
    assert (appended.plan_revision, appended.execution_generation) == (1, generation)
    assert [s["id"] for s in appended.plan["steps"]] == ["s1", "s2", "s3"]
    assert ("desktop.plan_appended", None) in _event_types(db, session)


def test_append_after_a_replace_is_rejected_as_stale(db, alice):
    session = _start(db, alice, _env(db, alice), plan=PLAN)
    svc.replace_plan(db, alice.id, session.id, PlanReplace(expected_plan_revision=1, **PLAN))
    with pytest.raises(DesktopError) as excinfo:
        svc.append_plan_steps(db, alice.id, session.id, PlanAppend(
            expected_plan_revision=1, steps=[{"do": "x", "done_when": "y"}],
        ))
    assert _code(excinfo) == "stale_plan_revision"


def test_plans_are_bounded_by_open_steps(db, alice, monkeypatch):
    session = _start(db, alice, _env(db, alice), plan=PLAN)
    limit = get_settings().desktop_plan_max_steps
    extra = [{"do": f"step {i}", "done_when": "done"} for i in range(limit - 1)]
    with pytest.raises(DesktopError) as excinfo:
        svc.append_plan_steps(db, alice.id, session.id, PlanAppend(expected_plan_revision=1, steps=extra))
    assert _code(excinfo) == "plan_too_long"
    db.rollback()
    with pytest.raises(DesktopError):
        svc.replace_plan(db, alice.id, session.id, PlanReplace(
            expected_plan_revision=1, goal="too much", steps=extra + extra[:2],
        ))


def test_append_without_a_plan_is_refused(db, alice):
    session = _start(db, alice, _env(db, alice))
    with pytest.raises(DesktopError) as excinfo:
        svc.append_plan_steps(db, alice.id, session.id, PlanAppend(
            expected_plan_revision=1, steps=[{"do": "x", "done_when": "y"}],
        ))
    assert _code(excinfo) == "no_plan"


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(database, monkeypatch):
    from app.api import deps
    from app.api.routes import desktop as routes
    from app.core.limiter import limiter
    from app.db.session import get_db
    from app.main import app

    enabled = get_settings().model_copy(update={"desktop_agent_enabled": True})
    monkeypatch.setattr(routes, "get_settings", lambda: enabled)
    monkeypatch.setattr(limiter, "enabled", False)
    current: dict[str, User] = {}

    def db_override():
        with database() as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[deps.get_current_user] = lambda: current["user"]
    test_client = TestClient(app, headers={"Origin": get_settings().cors_origins[0]})
    test_client.as_user = lambda user: current.__setitem__("user", user)  # type: ignore[attr-defined]
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


def test_routes_are_hidden_while_the_flag_is_off(database, alice, monkeypatch):
    from app.api import deps
    from app.api.routes import desktop as routes
    from app.db.session import get_db
    from app.main import app

    # Independent of the local .env, and must never reach a real database.
    disabled = get_settings().model_copy(update={"desktop_agent_enabled": False})
    monkeypatch.setattr(routes, "get_settings", lambda: disabled)

    def no_db():
        raise AssertionError("a disabled route touched the database")
        yield  # pragma: no cover

    app.dependency_overrides[get_db] = no_db
    app.dependency_overrides[deps.get_current_user] = lambda: alice
    try:
        response = TestClient(app).get("/api/desktop/environments")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404


def test_route_lifecycle_and_error_codes(client, database, alice, bob):
    client.as_user(alice)
    env = client.post("/api/desktop/environments", json={
        "display_name": "Legion VM", "allowed_profile_ids": ["minecraft_java"],
    })
    assert env.status_code == 201, env.text
    env_id = env.json()["id"]

    start = {"environment_id": env_id, "profile_id": "minecraft_java", "plan": PLAN, "idempotency_key": "key-00001"}
    created = client.post("/api/desktop/sessions", json=start)
    assert created.status_code == 201, created.text
    session = created.json()
    assert (session["state"], session["plan"]["steps"][0]["id"]) == ("starting", "s1")
    assert client.post("/api/desktop/sessions", json=start).status_code == 200  # idempotent replay

    conflict = client.post("/api/desktop/sessions", json={"environment_id": env_id, "profile_id": "minecraft_java"})
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "already_owned"

    url = f"/api/desktop/sessions/{session['id']}"
    paused = client.post(f"{url}/pause").json()
    assert paused["state"] == "paused"
    offline = client.post(f"{url}/resume", json={"expected_state_revision": paused["state_revision"]})
    assert offline.status_code == 409 and offline.json()["detail"]["code"] == "environment_offline"

    client.as_user(bob)
    assert client.get(url).status_code == 404
    assert client.post(f"{url}/end").status_code == 404

    client.as_user(alice)
    ended = client.post(f"{url}/end", json={"reason": "done_for_today"}).json()
    assert (ended["state"], ended["reason_code"]) == ("ended", "done_for_today")
    events = client.get(f"{url}/events").json()
    assert [e["payload"].get("state") for e in events if e["type"] == "desktop.session_state"] == \
        ["starting", "paused", "ended"]


def test_route_mutations_require_an_allowed_origin(client, alice):
    client.as_user(alice)
    response = client.post(
        "/api/desktop/environments",
        json={"display_name": "VM", "allowed_profile_ids": ["minecraft_java"]},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_route_rejects_unbounded_plan_text(client, alice):
    client.as_user(alice)
    env_id = client.post("/api/desktop/environments", json={
        "display_name": "VM", "allowed_profile_ids": ["minecraft_java"],
    }).json()["id"]
    too_long = {"goal": "g", "steps": [{"do": "x" * 121, "done_when": "y"}]}
    response = client.post("/api/desktop/sessions", json={
        "environment_id": env_id, "profile_id": "minecraft_java", "plan": too_long,
    })
    assert response.status_code == 422
