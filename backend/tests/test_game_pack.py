"""The Minecraft pack in the session system (stage G, phase 3): its
environment, the worker's link (health, ready, disconnect, stopping her
skill), and Columbina's summary and start. Real PostgreSQL
(``TEST_DATABASE_URL``); the pack is a fake; Redis is the in-memory stand-in."""
from __future__ import annotations

import time

import pytest

from app.ai.desktop import pack_client
from app.ai.desktop.pack_client import PackError
from app.core.config import get_settings
from app.db.models import DesktopSession
from app.services import desktop_commands, desktop_game, game_pack
from app.services import desktop_sessions as svc
from tests.db_support import database  # noqa: F401  (fixture)
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

    def manifest(self):
        self._check()
        return [{"name": "craft", "description": "Craft an item.", "done_when": "crafted",
                 "stops_when": "missing ingredients", "params": {"item": {}, "count": {}, "time_limit": {}}},
                {"name": "place", "description": "Place a block.", "done_when": "placed",
                 "stops_when": "nowhere to put it", "params": {"block": {}}}]

    modes_now = {"guard": False, "gather": [], "pickup": False}

    def modes(self):
        self._check()
        return dict(self.modes_now)

    def set_modes(self, change):
        self._check()
        if any(name == "iron" for name in change.get("gather", [])):
            raise PackError("no block is called iron (did you mean iron_ore?)", 400)
        self.modes_now = {**self.modes_now, **change}
        return dict(self.modes_now)

    def recipe(self, item):
        self._check()
        if item == "bed":
            return {"item": "bed", "known": False, "did_you_mean": ["white_bed", "red_bed"]}
        return {"item": item, "known": True, "craftable": True, "have": 0, "crafting_table_nearby": False,
                "crafting_table_carried": True, "variants": 16,
                "recipes": [{"needs": {"white_wool": 3, "oak_planks": 3}, "makes": 1, "needs_crafting_table": True,
                             "missing": {"white_wool": 1}}]}


@pytest.fixture
def pack(monkeypatch, alice):
    fake = FakePack()
    monkeypatch.setattr(pack_client.PackClient, "from_settings", classmethod(lambda cls: fake))
    settings = get_settings().model_copy(update={"desktop_agent_enabled": True,
                                                 "minecraft_pack_owner_email": alice.email.upper()})
    monkeypatch.setattr(game_pack, "get_settings", lambda: settings)
    monkeypatch.setattr(desktop_game, "get_settings", lambda: settings)
    monkeypatch.setattr(desktop_game, "_manual_cache", {"at": 0.0, "lines": []})
    monkeypatch.setattr(game_pack, "_out_since", None)
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
    game_pack.sync(db, pack, now=100.0)
    game_pack.sync(db, pack, now=100.0 + game_pack.OFFLINE_GRACE_SECONDS)
    env = game_pack.ensure_environment(db, alice.id)
    session = _session(db, alice.id, env.id)
    assert (env.status, session.state, session.reason_code) == ("offline", "disconnected", "pack_offline")
    assert desktop_game.describe(db, alice.id, for_reply=True) is None
    pack.spawned = True
    game_pack.sync(db, pack)
    db.refresh(session)
    assert session.state == "disconnected"
    assert "the pack stopped, or lost the server" in desktop_game.describe(db, alice.id).summary


def test_pausing_stops_the_skill_her_hands_started_but_not_one_run_by_hand(db, alice, pack):
    desktop_game.start(db, alice.id, goal="Mine stone")
    game_pack.sync(db, pack)
    pack.running = {"id": "r1", "skill": "mine", "source": "driver"}
    desktop_game.pause(db, alice.id, user_asked=True)
    assert pack.stops == 1 and pack.running is None
    pack.running = {"id": "r2", "skill": "follow_player", "source": "user"}
    game_pack.sync(db, pack)
    assert pack.stops == 1 and pack.running is not None


def test_a_new_plan_leaves_her_skill_to_her_hands(db, alice, pack):
    """Her hands stop it unless it still fits; stopping it here cut one follow off 15 times."""
    desktop_game.start(db, alice.id, goal="Follow tearDrop12")
    game_pack.sync(db, pack)
    pack.running = {"id": "r1", "skill": "follow_player", "source": "driver"}
    desktop_game.set_plan(db, alice.id, goal="Follow tearDrop12 to the cave")
    assert pack.stops == 0 and pack.running is not None


def test_link_stops_a_driver_skill_left_running_without_a_playing_session(db, alice, pack):
    game_pack.ensure_environment(db, alice.id)
    pack.running = {"id": "r1", "skill": "explore", "source": "driver"}
    game_pack.sync(db, pack)
    assert pack.stops == 1


def test_pack_unreachable_counts_as_offline(db, alice, pack):
    desktop_game.start(db, alice.id, goal="Explore")
    game_pack.sync(db, pack)
    pack.reachable = False
    game_pack.sync(db, pack, now=100.0)
    game_pack.sync(db, pack, now=100.0 + game_pack.OFFLINE_GRACE_SECONDS)
    env = game_pack.ensure_environment(db, alice.id)
    assert env.status == "offline"


def test_one_missed_health_check_does_not_disconnect(db, alice, pack):
    """Seen live 2026-11-24: one slow /health disconnected a session whose player never left the server."""
    desktop_game.start(db, alice.id, goal="Make a bed")
    game_pack.sync(db, pack, now=100.0)
    pack.reachable = False
    game_pack.sync(db, pack, now=101.0)
    game_pack.sync(db, pack, now=102.0)
    pack.reachable = True
    game_pack.sync(db, pack, now=103.0)
    pack.reachable = False
    game_pack.sync(db, pack, now=104.0)
    game_pack.sync(db, pack, now=104.0 + game_pack.OFFLINE_GRACE_SECONDS - 1)
    env = game_pack.ensure_environment(db, alice.id)
    session = _session(db, alice.id, env.id)
    assert (env.status, session.state) == ("online", "running")


def test_summary_describes_her_own_player_and_what_her_hands_do(db, alice, pack):
    assert desktop_game.describe(db, alice.id) is None
    game_pack.sync(db, pack)
    idle = desktop_game.describe(db, alice.id)
    assert idle.idle and "Minecraft as your own player [minecraft_pack]" in idle.summary
    assert "your hands" not in idle.summary  # how to play comes with a session, not on every turn
    desktop_game.start(db, alice.id, goal="Collect 3 oak logs")
    game_pack.sync(db, pack)
    env = game_pack.ensure_environment(db, alice.id)
    session = _session(db, alice.id, env.id)
    desktop_commands.save_driver_state(session.id, {
        "kind": "skills", "notes": "chopping the oak to my left",
        "running": {"skill": "mine", "params": {"block": "oak_log", "count": 3}},
        "outcomes": [{"skill": "go_to", "status": "done", "reason": None, "at": 0}],
        "modes_did": [{"text": "gather: mined 2 iron_ore on the way", "at": time.time() - 600}],
    })
    context = desktop_game.describe(db, alice.id)
    assert "Game: Minecraft (your own player), you are playing" in context.summary
    assert "Your hands are doing: mine(block=oak_log, count=3)." in context.summary
    assert "Recent results: go_to -> done" in context.summary
    assert "What your background modes did lately: gather: mined 2 iron_ore on the way (10 min ago)." in context.summary
    assert "Your player right now (from the game): Piqeeee at (10, 64, -3)" in context.summary
    assert ('In Minecraft you are "Piqeeee" (never target yourself). Other players: "tearDrop12". '
            'alice is probably "tearDrop12" (the only other player; not confirmed') in context.summary
    assert "Show preview" not in context.summary
    assert context.activity.startswith("Right now you are playing Minecraft as your own player (goal: Collect 3 oak logs)")


def test_the_user_tells_her_which_player_they_are_and_it_sticks(db, alice, bob, pack):
    from app.services import game_players

    game_pack.ensure_environment(db, alice.id)
    game_pack.sync(db, pack)
    assert "is you, not the user" in desktop_game.this_is_me(db, alice.id, player="Piqeeee")
    assert "isn't a minecraft player name" in desktop_game.this_is_me(db, alice.id, player="tear drop")
    assert desktop_game.this_is_me(db, alice.id, player="tearDrop12").startswith('Saved: the user is "tearDrop12"')
    desktop_game.start(db, alice.id, goal="Follow tearDrop12")  # who is who is told while she plays
    game_pack.sync(db, pack)
    summary = desktop_game.describe(db, alice.id).summary
    assert 'alice plays as "tearDrop12" (online): when they say "me", that\'s them.' in summary
    assert "probably" not in summary
    assert game_players.who(db, alice.id, "minecraft") == "alice (tearDrop12 in the game)"
    desktop_game.this_is_me(db, alice.id, player="Steve_2")  # changed
    assert game_players.user_player(db, alice.id, "minecraft") == "Steve_2"
    assert 'alice plays as "Steve_2" (not online right now)' in desktop_game.describe(db, alice.id).summary
    assert game_players.user_player(db, bob.id, "minecraft") is None
    assert desktop_game.this_is_me(db, bob.id, player="bob_1").startswith("Not saved: you don't have")


def test_summary_shows_what_happened_lately_and_where_she_last_spoke(db, alice, pack):
    from datetime import datetime, timedelta, timezone

    from app.db.models import GameExperience
    from app.services import chat as chat_service

    desktop_game.start(db, alice.id, goal="Follow tearDrop12")
    game_pack.sync(db, pack)
    session = _session(db, alice.id, game_pack.ensure_environment(db, alice.id).id)
    now = datetime.now(timezone.utc)

    def happened(seconds_ago, kind, payload):
        db.add(GameExperience(user_id=alice.id, session_id=session.id, game="minecraft", kind=kind,
                              payload=payload, occurred_at=now - timedelta(seconds=seconds_ago)))

    happened(400, "notice", {"kind": "morning", "urgency": "info", "text": "morning"})  # too long ago
    happened(120, "notice", {"kind": "mode", "urgency": "info", "text": "pickup: picked up bread x4"})
    happened(100, "skill_outcome", {"skill": "follow_player", "status": "done"})  # routine: not news
    happened(50, "notice", {"kind": "hostile", "urgency": "urgent", "text": "skeleton 5 m west"})
    happened(45, "notice", {"kind": "hostile", "urgency": "urgent", "text": "skeleton 5 m west"})
    happened(30, "notice", {"kind": "died", "urgency": "urgent",
                            "text": "died with skeleton close by; dropped where you fell (gone in 5 min): bread x4"})
    conversation = chat_service.get_or_create_conversation(db, user_id=alice.id, conversation_id=None)
    message = chat_service.add_message(db, conversation=conversation, role="assistant", content="following you!")
    message.created_at = (now - timedelta(seconds=60)).replace(tzinfo=None)
    db.commit()

    summary = desktop_game.describe(db, alice.id).summary
    lately = summary[summary.index("What happened in your game lately"):]
    assert "morning" not in lately and "follow player" not in lately
    assert lately.index("picked up bread x4") < lately.index("you last spoke to the user here") \
        < lately.index("skeleton 5 m west (x2)") < lately.index("died with skeleton close by")
    assert "bread x4 (30 s ago)" in lately


def test_look_reads_the_game_instead_of_a_picture(db, alice, pack):
    desktop_game.start(db, alice.id, goal="Explore")
    game_pack.sync(db, pack)
    look = desktop_game.pack_look(db, alice.id)
    assert "not a picture" in look and "Health 20/20" in look


def test_with_the_pack_minecraft_on_the_vm_is_retired(db, alice, pack):
    vm = _paired_env(db, alice)  # allows minecraft_java
    pack_env = game_pack.ensure_environment(db, alice.id)
    assert desktop_game.describe(db, alice.id) is None
    game_pack.sync(db, pack)
    summary = desktop_game.describe(db, alice.id).summary
    # A VM with no game left to play there is only her desktop (general desktop control): not in the game summary.
    assert "Legion VM" not in summary and not desktop_game.describe(db, alice.id).vm
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


def _playing(db, alice, pack, goal="Make a bed"):
    desktop_game.start(db, alice.id, goal=goal, steps=[{"do": "craft a white bed", "done_when": "a white_bed"}])
    game_pack.sync(db, pack)
    env = game_pack.ensure_environment(db, alice.id)
    return _session(db, alice.id, env.id)


def test_she_sees_her_skills_her_failed_tries_and_her_promises(db, alice, pack):
    session = _playing(db, alice, pack)
    desktop_commands.save_driver_state(session.id, {"kind": "skills", "step": {
        "id": session.plan["steps"][0]["id"], "do": "craft a white bed",
        "since": time.time() - 130, "tries": 5, "failed": 4, "failures": [
            {"skill": "craft", "params": {"item": "white_bed"}, "status": "failed", "reason": "missing ingredients",
             "data": {}}] * 3 + [
            {"skill": "craft", "params": {"item": "bed"}, "status": "refused", "reason": "no item is called bed",
             "data": {}}]}})
    assert desktop_game.note_promise(db, alice.id, promise="tell them when the bed is ready").startswith(
        "Noted in your memory")
    summary = desktop_game.describe(db, alice.id).summary
    assert "Your hands' skills (all they can do, one at a time):" in summary
    assert "Before learning a move (or offering to): if you can name the skill above that does it" in summary
    assert "- craft(item, count): Craft an item. Stops when: missing ingredients." in summary
    assert ("This step so far (counted): 2 min, 5 tries, 4 failed: craft(item=white_bed) -> missing ingredients x3; "
            "craft(item=bed) -> no item is called bed.") in summary
    assert desktop_game.drop_promise(db, alice.id, number=1) == "Dropped. Nothing promised now."


def test_setting_the_same_goal_again_and_again_shows_as_a_loop(db, alice, pack):
    _playing(db, alice, pack, goal="Gather wood")
    for tree in ("oak", "birch"):  # word for word the same would change nothing at all
        desktop_game.set_plan(db, alice.id, goal="Gather wood!", steps=[
            {"do": f"chop {tree} logs", "done_when": f"gained 5 {tree}_log"}])
    assert "You've set this same goal 3 times in the last 5 minutes" in desktop_game.describe(db, alice.id).summary


def test_how_to_make_says_what_is_missing_and_close_names(db, alice, pack):
    _playing(db, alice, pack)
    text = desktop_game.how_to_make(db, alice.id, item="White Bed")
    assert text.startswith("white_bed: you have 0.")
    assert "Recipe 1: 3 white_wool, 3 oak_planks -> 1 (needs a crafting table); you're missing 1 white_wool." in text
    assert "16 variants" in text and "you carry one but must place it first" in text
    assert desktop_game.how_to_make(db, alice.id, item="bed") == (
        "There's no item called bed. Close names: white_bed, red_bed.")


def test_she_knows_what_she_cant_do_yet_and_how_her_moves_behave(db, alice, pack):
    from app.db.models import GameLesson, GameProposal

    db.add(GameProposal(user_id=alice.id, game="minecraft", normalized_key="new_skill:pillar_upward",
                        title="Add a safe upward-pillar action", area="new_skill", problem="p", suggestion="s",
                        evidence=[], status="open"))
    db.add(GameProposal(user_id=alice.id, game="minecraft", normalized_key="x:done", title="Already built",
                        area="other", problem="p", suggestion="s", evidence=[], status="implemented"))
    db.add(GameLesson(user_id=alice.id, game="minecraft", normalized_key="crafting:table", level="skill",
                      situation="crafting a bed", advice="place a crafting table first", status="trial",
                      evidence=[]))
    db.commit()
    desktop_game.start(db, alice.id, goal="Collect 3 oak logs")  # what she learned playing comes while she plays
    game_pack.sync(db, pack)
    summary = desktop_game.describe(db, alice.id).summary
    assert "What you know you can't do yet (you asked for it to be built; not done): Add a safe upward-pillar action." in summary
    assert "Already built" not in summary
    assert "- When crafting a bed: place a crafting table first (tentative: seen once)" in summary


def test_background_modes_change_without_touching_the_plan(db, alice, pack):
    """Seen live: she said she'd watch for mobs and grab iron while following, and could do neither."""
    desktop_game.start(db, alice.id, goal="Follow tearDrop12")
    game_pack.sync(db, pack)
    session = svc.list_sessions(db, alice.id, live_only=True)[0]
    revision = session.plan_revision
    reply = desktop_game.set_modes(db, alice.id, guard=True, gather=["Iron Ore", "coal_ore"])
    assert reply == "Background modes now: guard on; gathering iron_ore, coal_ore on the way."
    assert pack.modes_now == {"guard": True, "gather": ["iron_ore", "coal_ore"], "pickup": False}
    db.refresh(session)
    assert session.plan_revision == revision and pack.stops == 0
    with pytest.raises(desktop_game.DesktopError) as bad:
        desktop_game.set_modes(db, alice.id, gather=["iron"])
    assert bad.value.code == "bad_modes" and "iron_ore" in bad.value.message
    assert desktop_game.set_modes(db, alice.id, guard=False, gather=[]) == "Background modes now: all off."
    assert "Background modes (set_my_modes)" in desktop_game.describe(db, alice.id).summary
