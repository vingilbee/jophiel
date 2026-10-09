"""The Minecraft pack in the session system (stage G, phase 3): its
environment, the worker's link (health, ready, disconnect, stopping her
skill), and Columbina's summary and start. Real PostgreSQL
(``TEST_DATABASE_URL``); the pack is a fake; Redis is the in-memory stand-in."""
from __future__ import annotations

import pytest

from app.ai.desktop import pack_client
from app.ai.desktop.pack_client import PackError
from app.core.config import get_settings
from app.db.models import AgentEventJob, DesktopSession
from app.services import desktop_commands, desktop_game, game_pack
from app.services import desktop_sessions as svc
from tests.cognitive_shadow_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_desktop_game import _paired_env
from tests.test_desktop_sessions import _user, alice, bob, db  # noqa: F401  (fixtures)


class FakePack:
    def __init__(self):
        self.spawned = True
        self.reachable = True
        self.running: dict | None = None
        self.stops = 0

    def _check(self):
        if not self.reachable:
            raise PackError("pack unreachable")

    def health(self):
        self._check()
        return {"connected": self.spawned, "spawned": self.spawned}

    def current(self):
        self._check()
        return self.running

    def stop(self):
        self._check()
        self.stops += 1
        stopped, self.running = self.running, None
        return stopped

    def state(self):
        self._check()
        return {"text": "Piqeeee at (10, 64, -3) in the overworld.\nHealth 20/20, food 18/20.", "noticed": [],
                "username": "Piqeeee", "players_online": ["tearDrop12"]}


@pytest.fixture
def pack(monkeypatch, alice):
    fake = FakePack()
    monkeypatch.setattr(pack_client.PackClient, "from_settings", classmethod(lambda cls: fake))
    settings = get_settings().model_copy(update={"desktop_agent_enabled": True,
                                                 "minecraft_pack_owner_email": alice.email.upper()})
    monkeypatch.setattr(game_pack, "get_settings", lambda: settings)
    monkeypatch.setattr(desktop_game, "get_settings", lambda: settings)
    return fake


def _session(db, user_id, env_id):
    return db.query(DesktopSession).filter_by(owner_user_id=user_id, environment_id=env_id).one()


def test_the_pack_belongs_to_its_configured_owner_only(db, alice, bob, pack):
    env = game_pack.ensure_environment(db, alice.id)
    assert env.hosting_kind == "game_pack" and env.allowed_profile_ids == ["minecraft_pack"]
    assert game_pack.ensure_environment(db, alice.id).id == env.id  # once
    assert game_pack.ensure_environment(db, bob.id) is None
    assert desktop_game.describe(db, bob.id) is None


def test_link_marks_her_in_the_world_and_readies_a_session(db, alice, pack):
    desktop_game.start(db, alice.id, goal="Collect 3 oak logs")
    env = game_pack.ensure_environment(db, alice.id)
    session = _session(db, alice.id, env.id)
    assert session.profile_id == "minecraft_pack" and session.state == "starting"
    game_pack.sync(db, pack)
    db.refresh(env)
    db.refresh(session)
    assert env.status == "online"
    assert (session.state, session.control_owner) == ("running", "gameplay")
    assert session.id in svc.driver_candidates(db)


def test_leaving_the_world_disconnects_and_nothing_resumes_by_itself(db, alice, pack):
    desktop_game.start(db, alice.id, goal="Explore")
    game_pack.sync(db, pack)
    pack.spawned = False
    game_pack.sync(db, pack)
    env = game_pack.ensure_environment(db, alice.id)
    session = _session(db, alice.id, env.id)
    assert (env.status, session.state, session.reason_code) == ("offline", "disconnected", "pack_offline")
    pack.spawned = True
    game_pack.sync(db, pack)
    db.refresh(session)
    assert session.state == "disconnected"
    assert "the pack stopped, or lost the server" in desktop_game.describe(db, alice.id).summary


def test_pausing_stops_the_skill_her_hands_started_but_not_one_run_by_hand(db, alice, pack):
    desktop_game.start(db, alice.id, goal="Mine stone")
    game_pack.sync(db, pack)
    pack.running = {"id": "r1", "skill": "mine", "source": "driver"}
    desktop_game.pause(db, alice.id)
    assert pack.stops == 1 and pack.running is None
    pack.running = {"id": "r2", "skill": "follow_player", "source": "user"}
    game_pack.sync(db, pack)
    assert pack.stops == 1 and pack.running is not None


def test_link_stops_a_driver_skill_left_running_without_a_playing_session(db, alice, pack):
    game_pack.ensure_environment(db, alice.id)
    pack.running = {"id": "r1", "skill": "explore", "source": "driver"}
    game_pack.sync(db, pack)
    assert pack.stops == 1


def test_pack_unreachable_counts_as_offline(db, alice, pack):
    desktop_game.start(db, alice.id, goal="Explore")
    game_pack.sync(db, pack)
    pack.reachable = False
    game_pack.sync(db, pack)
    env = game_pack.ensure_environment(db, alice.id)
    assert env.status == "offline"


def test_summary_describes_her_own_player_and_what_her_hands_do(db, alice, pack):
    summary = desktop_game.describe(db, alice.id).summary
    assert "Minecraft as your own player [minecraft_pack]: you're not in the world" in summary
    desktop_game.start(db, alice.id, goal="Collect 3 oak logs")
    game_pack.sync(db, pack)
    env = game_pack.ensure_environment(db, alice.id)
    session = _session(db, alice.id, env.id)
    desktop_commands.save_driver_state(session.id, {
        "kind": "skills", "notes": "chopping the oak to my left",
        "running": {"skill": "mine", "params": {"block": "oak_log", "count": 3}},
        "outcomes": [{"skill": "go_to", "status": "done", "reason": None, "at": 0}],
    })
    context = desktop_game.describe(db, alice.id)
    assert "Game: Minecraft (your own player), you are playing" in context.summary
    assert "Your hands are doing: mine(block=oak_log, count=3)." in context.summary
    assert "Recent results: go_to -> done" in context.summary
    assert "Your player right now (from the game): Piqeeee at (10, 64, -3)" in context.summary
    assert 'In Minecraft you are "Piqeeee" (never target yourself). Other players: "tearDrop12". The user is "tearDrop12"' in context.summary
    assert "Show preview" not in context.summary
    assert context.activity.startswith("Right now you are playing Minecraft as your own player (goal: Collect 3 oak logs)")


def test_look_reads_the_game_instead_of_a_picture(db, alice, pack):
    desktop_game.start(db, alice.id, goal="Explore")
    game_pack.sync(db, pack)
    look = desktop_game.pack_look(db, alice.id)
    assert "not a picture" in look and "Health 20/20" in look


def test_with_the_pack_minecraft_on_the_vm_is_retired(db, alice, pack):
    vm = _paired_env(db, alice)  # allows minecraft_java
    pack_env = game_pack.ensure_environment(db, alice.id)
    summary = desktop_game.describe(db, alice.id).summary
    assert 'Your game computer "Legion VM"' in summary and "Games you have: none" in summary
    assert "Minecraft as your own player [minecraft_pack]" in summary
    # Columbina asking for the old id gets her own player.
    desktop_game.start(db, alice.id, goal="Collect logs", game="minecraft_java")
    assert _session(db, alice.id, pack_env.id).profile_id == "minecraft_pack"
    desktop_game.end(db, alice.id)
    # Starting it on the VM directly (card, API) is refused.
    with pytest.raises(svc.DesktopError) as refused:
        svc.start_session(db, alice.id, svc.SessionCreate(environment_id=vm.id, profile_id="minecraft_java"))
    assert refused.value.code == "replaced_by_pack"


def test_without_the_pack_the_vm_still_plays_minecraft(db, bob, pack):
    vm = _paired_env(db, bob)  # bob isn't the pack's owner
    desktop_game.start(db, bob.id, goal="Collect logs", game="minecraft_java")
    assert _session(db, bob.id, vm.id).profile_id == "minecraft_java"


def test_discoveries_wake_her_unless_quiet(db, alice, pack):
    desktop_game.start(db, alice.id, goal="Explore")
    game_pack.sync(db, pack)
    env = game_pack.ensure_environment(db, alice.id)
    session = _session(db, alice.id, env.id)
    svc.driver_noticed(db, session.id, kind="discovery", text="a village, 70 m north")
    jobs = db.query(AgentEventJob).filter_by(user_id=alice.id).all()
    assert any("discovery: a village, 70 m north" in r for job in jobs for r in job.reasons)
    db.query(AgentEventJob).delete()
    db.commit()
    session.commentary_mode = "quiet"
    db.commit()
    svc.driver_noticed(db, session.id, kind="discovery", text="diamond ore, 6 m")
    assert db.query(AgentEventJob).filter_by(user_id=alice.id).count() == 0
