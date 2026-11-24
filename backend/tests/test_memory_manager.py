"""Her working memory (docs/COLUMBINA_MEMORY_SESSION1_SPEC.md §12): goals that outlive their steps,
contexts kept apart, the database's own rules, memories standing on their own, the goal's effect on
play, the due checks, and the reset. Real PostgreSQL (``TEST_DATABASE_URL``); in-memory Redis."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.config import get_settings
from app.db.models import ChatMessage, MemoryContext, MemoryEvidence, MemoryManagerState, WorkingItem
from app.services import chat as chat_service
from app.services import desktop_game, memory_manager, memory_reset
from app.services import desktop_sessions as svc
from app.services.desktop_sessions import DesktopError
from app.services.memory_manager import MemoryError, Source
from tests.db_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_desktop_game import _paired_env, enabled  # noqa: F401  (autouse fixture)
from tests.test_desktop_sessions import _user, alice, bob, db  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def memory_on(monkeypatch, tmp_path):
    settings = get_settings().model_copy(update={
        "memory_manager_enabled": True, "memory_manager_log_path": str(tmp_path / "memory.jsonl")})
    monkeypatch.setattr(memory_manager, "get_settings", lambda: settings)
    return settings


def _message(db, user, content: str) -> ChatMessage:
    conversation = chat_service.get_or_create_conversation(db, user_id=user.id, conversation_id=None)
    message = chat_service.add_message(db, conversation=conversation, role="user", content=content)
    db.commit()
    return message


def _playing(db, user, goal="go to the Nether"):
    _paired_env(db, user)
    desktop_game.start(db, user.id, goal=goal, steps=[{"do": "look around", "done_when": "seen"}])
    session = svc.list_sessions(db, user.id, live_only=True)[0]
    svc.controller_ready(db, session.id)
    db.refresh(session)
    return session


def _goals(db, user, status="active"):
    return list(db.scalars(select(WorkingItem).where(WorkingItem.user_id == user.id, WorkingItem.kind == "goal",
                                                     WorkingItem.status == status)))


# --- 1. the goal outlives its steps ---------------------------------------------------------


def test_small_instructions_replace_the_plan_never_the_goal(db, alice):
    session = _playing(db, alice)
    goal = _goals(db, alice)[0]
    assert goal.text == "go to the Nether" and goal.owner == "user" and session.goal_item_id == goal.id
    for step in ("gather wood", "gather materials toward a Nether portal", "mine cobblestone",
                 "reach dry land near the stone"):
        desktop_game.set_plan(db, alice.id, goal=step, steps=[{"do": step, "done_when": "done"}])
        db.refresh(session)
        assert session.goal_item_id == goal.id and session.plan["goal_id"] == str(goal.id)
    desktop_game.note_promise(db, alice.id, promise="kill a sheep for wool if I see one",
                              message_id=_message(db, alice, "if you see a sheep, kill it").id)
    assert [g.text for g in _goals(db, alice)] == ["go to the Nether"]
    view = memory_manager.view(db, alice.id, user_name="Vincent")
    assert view.splitlines()[1].startswith("  Goal [") and "go to the Nether" in view.splitlines()[1]
    assert 'Your game plan for it right now: "reach dry land near the stone"' in view
    assert "You promised [" in view and "kill a sheep" in view
    summary = desktop_game.describe(db, alice.id).summary
    assert 'in your memory (the user\'s' in summary and '"go to the Nether"' in summary
    assert "Your current plan for it: reach dry land near the stone." in summary


def test_a_new_goal_is_kept_next_to_the_old_one(db, alice):
    session = _playing(db, alice)
    first = _goals(db, alice)[0]
    desktop_game.set_plan(db, alice.id, goal="build a house", steps=[{"do": "get logs", "done_when": "16"}],
                          new_goal=True, message_id=_message(db, alice, "let's build a house instead").id)
    db.refresh(session)
    second = db.get(WorkingItem, session.goal_item_id)
    assert second.text == "build a house" and second.id != first.id
    assert {g.text for g in _goals(db, alice)} == {"go to the Nether", "build a house"}
    assert db.scalar(select(MemoryEvidence.message_id).where(MemoryEvidence.item_id == second.id)) is not None


# --- 2. contexts kept apart, 3. a topic switch and back -------------------------------------


def test_a_new_world_puts_the_old_worlds_goals_on_hold(db, alice):
    session = _playing(db, alice, goal="smelt the raw iron")
    old = _goals(db, alice)[0]
    context, changed = memory_manager.switch(db, alice.id, label="new map", kind="world", new=True)
    db.commit()
    db.refresh(old)
    assert old.status == "suspended" and old in changed
    db.refresh(session)
    assert session.goal_item_id == old.id and session.state == "paused"
    desktop_game.set_plan(db, alice.id, goal="go to the Nether", steps=[{"do": "chop wood", "done_when": "logs"}])
    db.refresh(session)
    new = db.get(WorkingItem, session.goal_item_id)
    assert new.text == "go to the Nether" and new.context_id == context.id
    view = memory_manager.view(db, alice.id)
    focus, earlier = view.split("Earlier, closed:")
    assert "go to the Nether" in focus and "smelt the raw iron" not in focus
    assert "smelt the raw iron" in earlier and "don't treat it as true here" in earlier


def test_switching_topic_and_back_keeps_both(db, alice):
    _playing(db, alice)
    memory_manager.switch(db, alice.id, label="Interview prep", kind="project")
    memory_manager.set_goal(db, alice.id, "prepare answers for Friday")
    db.commit()
    view = memory_manager.view(db, alice.id)
    assert view.startswith("Focus: Interview prep (project)") and "prepare answers for Friday" in view
    assert "Also open elsewhere:" in view and "go to the Nether" in view.split("Also open elsewhere:")[1]
    memory_manager.switch(db, alice.id, label="Minecraft world")
    db.commit()
    view = memory_manager.view(db, alice.id)
    assert view.startswith("Focus: Minecraft world") and "prepare answers" in view.split("Also open elsewhere:")[1]


def test_her_own_plan_changes_in_the_background_dont_move_her_attention(db, alice):
    session = _playing(db, alice)
    memory_manager.switch(db, alice.id, label="Interview prep", kind="project")
    db.commit()
    desktop_game.set_plan(db, alice.id, goal="collect wood", steps=[{"do": "chop", "done_when": "logs"}],
                          source="columbina_planning_turn", since_revision=session.plan_revision)
    assert memory_manager.focused(db, alice.id).label == "Interview prep"
    db.refresh(session)
    assert db.get(WorkingItem, session.goal_item_id).text == "go to the Nether"


# --- 4/5. the tick: restart, no game ---------------------------------------------------------


def test_the_tick_lapses_once_and_then_skips(db, alice):
    entry = memory_manager.note_intention(db, alice.id, "look into that cave")
    db.commit()
    later = datetime.now(timezone.utc) + timedelta(hours=3)
    assert memory_manager.tick_due(db, later) == 1
    db.refresh(entry)
    assert entry.status == "lapsed" and entry.closed_at is not None
    state = db.get(MemoryManagerState, alice.id)
    assert state.last_decision["decision"] == "apply"
    # A fresh process (nothing in memory) picks the user up again only when the next check is due.
    assert memory_manager.tick_due(db, later) == 0
    assert memory_manager.tick_due(db, state.next_check_at) == 1
    db.refresh(state)
    assert state.last_decision["decision"] == "skip"


def test_overdue_promises_need_a_look_but_stay_open(db, alice):
    entry = memory_manager.note_commitment(db, alice.id, "send the link",
                                           due_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    db.commit()
    memory_manager.tick_due(db, datetime.now(timezone.utc) + timedelta(minutes=10))
    db.refresh(entry)
    assert entry.status == "needs_review" and entry.closed_at is None
    assert entry in memory_manager.pending(db, alice.id)


def test_the_tick_runs_without_any_game(db, alice):
    memory_manager.state(db, alice.id)
    db.commit()
    assert memory_manager.tick_due(db) == 1
    assert db.get(MemoryManagerState, alice.id).last_decision["reason"] == "nothing due"


# --- 6. refused writes ------------------------------------------------------------------------


def test_stale_and_background_writes_are_refused(db, alice):
    goal = memory_manager.set_goal(db, alice.id, "reach the Nether")
    db.commit()
    with pytest.raises(MemoryError, match="changed since"):
        memory_manager.resolve(db, alice.id, goal.id, status="suspended", reason="x", expected_revision=0)
    with pytest.raises(MemoryError, match="only change your own intentions"):
        memory_manager.resolve(db, alice.id, goal.id, status="abandoned", reason="x", background=True)
    with pytest.raises(MemoryError, match="what shows it's done"):
        memory_manager.resolve(db, alice.id, goal.id, status="completed", reason="done")
    done, _ = memory_manager.resolve(db, alice.id, memory_manager.ref(goal), status="completed", reason="said so",
                                     source=Source("message", _message(db, alice, "we made it!").id))
    with pytest.raises(MemoryError, match="already completed"):
        memory_manager.resolve(db, alice.id, goal.id, status="active", reason="again")
    assert done.closed_at is not None


def test_closing_a_goal_leaves_its_sub_goals_to_the_caller(db, alice):
    goal = memory_manager.set_goal(db, alice.id, "plan the trip")
    child = memory_manager.set_goal(db, alice.id, "book flights", parent_id=goal.id)
    db.commit()
    _, children = memory_manager.resolve(db, alice.id, goal.id, status="abandoned", reason="trip cancelled")
    assert children == [child] and child.status == "active"


# --- 8. memories stand on their own --------------------------------------------------------


def test_deleting_the_source_message_keeps_the_goal(db, alice):
    message = _message(db, alice, "help me prepare for Friday's interview")
    goal = memory_manager.set_goal(db, alice.id, "prepare for Friday's interview", source=Source("message", message.id))
    db.commit()
    assert "no longer available" not in memory_manager.view(db, alice.id)
    db.delete(db.get(ChatMessage, message.id))
    db.commit()
    db.refresh(goal)
    assert goal.status == "active"
    assert db.scalar(select(MemoryEvidence).where(MemoryEvidence.item_id == goal.id)) is None
    assert "(where it came from is no longer available)" in memory_manager.view(db, alice.id)


def test_a_source_of_another_user_is_never_linked(db, alice, bob):
    theirs = _message(db, bob, "bob's words")
    goal = memory_manager.set_goal(db, alice.id, "x", source=Source("message", theirs.id))
    db.commit()
    assert db.scalar(select(MemoryEvidence).where(MemoryEvidence.item_id == goal.id)) is None


# --- 9. the database's own rules -----------------------------------------------------------


def _raw(db, user, context, **values):
    row = WorkingItem(id=uuid.uuid4(), user_id=user.id, context_id=context.id, owner="user", epistemic="reported",
                      text="t", **values)
    db.add(row)
    db.flush()
    return row


@pytest.mark.parametrize("bad", [
    {"kind": "goal", "status": "active", "owed_to": "user"},                         # owed_to on a goal
    {"kind": "commitment", "status": "open"},                                         # no owed_to
    {"kind": "commitment", "status": "open", "owed_to": "user"},                      # self-owed (owner user)
    {"kind": "intention", "status": "active"},                                        # no lapse time
    {"kind": "goal", "status": "fulfilled"},                                          # not a goal status
    {"kind": "goal", "status": "completed"},                                          # closed without closed_at
])
def test_the_database_refuses_bad_items(db, alice, bad):
    general = memory_manager.ensure_general(db, alice.id)
    db.commit()
    with pytest.raises(IntegrityError):
        _raw(db, alice, general, **bad)
    db.rollback()


def test_parents_are_goals_in_the_same_context_without_loops(db, alice):
    general = memory_manager.ensure_general(db, alice.id)
    other = memory_manager.open_context(db, alice.id, kind="project", label="Other")
    goal = _raw(db, alice, general, kind="goal", status="active")
    intention = _raw(db, alice, general, kind="intention", status="active", due_at=datetime.now(timezone.utc))
    db.commit()
    with pytest.raises(DBAPIError, match="a parent must be a goal"):
        _raw(db, alice, general, kind="goal", status="active", parent_id=intention.id)
    db.rollback()
    with pytest.raises(IntegrityError):  # the composite key: same context only
        _raw(db, alice, other, kind="goal", status="active", parent_id=goal.id)
    db.rollback()
    child = _raw(db, alice, general, kind="goal", status="active", parent_id=goal.id)
    db.commit()
    goal = db.get(WorkingItem, goal.id)
    goal.parent_id = child.id
    with pytest.raises(DBAPIError, match="may not loop"):
        db.flush()
    db.rollback()


def test_a_referenced_context_cannot_be_deleted(db, alice):
    general = memory_manager.ensure_general(db, alice.id)
    memory_manager.set_goal(db, alice.id, "x", context_id=general.id)
    db.commit()
    with pytest.raises(IntegrityError):
        db.execute(delete(MemoryContext).where(MemoryContext.id == general.id))
        db.flush()
    db.rollback()


# --- 10. the goal's effect on play ---------------------------------------------------------


def test_closing_or_suspending_the_goal_pauses_play(db, alice):
    session = _playing(db, alice)
    goal = _goals(db, alice)[0]
    memory_manager.resolve(db, alice.id, goal.id, status="suspended", reason="later")
    db.commit()
    db.refresh(session)
    assert session.state == "paused" and session.reason_code == "goal_suspended"
    with pytest.raises(DesktopError, match="suspended"):
        svc.resume_session(db, alice.id, session.id, expected_state_revision=session.state_revision)
    db.rollback()
    memory_manager.resolve(db, alice.id, goal.id, status="active", reason="back on")
    db.commit()
    db.refresh(session)
    assert session.state == "paused"  # nothing resumes on its own
    svc.resume_session(db, alice.id, session.id, expected_state_revision=session.state_revision)
    memory_manager.resolve(db, alice.id, goal.id, status="completed", reason="we're in",
                           source=Source("message", _message(db, alice, "we're in the Nether!").id))
    db.commit()
    db.refresh(session)
    assert session.reason_code == "goal_completed"
    with pytest.raises(DesktopError, match="set a new goal"):
        svc.resume_session(db, alice.id, session.id, expected_state_revision=session.state_revision)
    db.rollback()
    reply = desktop_game.set_plan(db, alice.id, goal="explore the Nether", steps=[{"do": "walk", "done_when": "x"}])
    db.refresh(session)
    assert session.state == "running" and "playing again" in reply
    assert db.get(WorkingItem, session.goal_item_id).text == "explore the Nether"


# --- 11. reset -------------------------------------------------------------------------------


def test_reset_scopes(db, alice):
    session = _playing(db, alice)
    memory_reset.reset(db, user_ids=[alice.id], scopes=["conversations"])
    assert len(_goals(db, alice)) == 1
    memory_reset.reset(db, user_ids=[alice.id], scopes=["working"])
    assert db.scalar(select(WorkingItem).where(WorkingItem.user_id == alice.id)) is None
    assert db.scalar(select(MemoryContext).where(MemoryContext.user_id == alice.id)) is None
    db.refresh(session)
    assert session.goal_item_id is None and session.state == "running"


# --- 7. the tools ------------------------------------------------------------


def test_the_tools_act_for_their_own_user_with_the_message_as_evidence(database, alice, monkeypatch):
    from app.ai.tools.memory import build_memory_tools
    from app.ai.tools.memory import tools as tool_module

    monkeypatch.setattr(tool_module, "SessionLocal", database)
    with database() as setup:
        message = _message(setup, alice, "let's plan my interview")
    tools = {t.name: t for t in build_memory_tools(alice.id, message_id=message.id)}
    assert set(tools) == {"set_goal", "update_item", "note_commitment", "change_promise", "note_intention",
                          "note_question", "switch_context"}
    assert asyncio.run(tools["switch_context"].ainvoke({"name": "Interview prep", "kind": "project"})) \
        == "Now in: Interview prep."
    kept = asyncio.run(tools["set_goal"].ainvoke({"goal": "prepare answers for Friday"}))
    short = kept.split("[")[1].split("]")[0]
    assert asyncio.run(tools["update_item"].ainvoke({"item": short, "status": "completed", "reason": "done"})) \
        .startswith("Done:")
    background = {t.name for t in build_memory_tools(alice.id, background=True)}
    assert background == {"note_intention", "update_item"}
    stranger = {t.name: t for t in build_memory_tools(uuid.uuid4())}
    assert asyncio.run(stranger["update_item"].ainvoke({"item": short, "status": "active", "reason": "x"})) \
        .startswith("Not done:")
    with database() as check:
        assert check.scalar(select(MemoryEvidence).where(MemoryEvidence.message_id == message.id,
                                                         MemoryEvidence.role == "resolution")) is not None


# --------------------------------------------------------------------------- #
# Her promises in view: the newest never hidden behind an old backlog
# --------------------------------------------------------------------------- #


def _her_promises_like_the_spotify_evening(db, user_id):
    """The 03:17 view: three cardio reminders with times (two overdue), two older WhatsApp promises, then six
    rewordings of one Spotify promise, oldest to newest."""
    now = datetime.now(timezone.utc)
    made = {}

    def promise(text, *, minutes_ago, due=None, status=None):
        entry = memory_manager.note_commitment(db, user_id, text, due_at=due)
        entry.created_at = now - timedelta(minutes=minutes_ago)
        if status:
            entry.status = status
        made[text] = entry
        return entry

    promise("Remind him about cardio at 3 PM", minutes_ago=900, due=now + timedelta(hours=2))
    promise("Remind him about cardio (first)", minutes_ago=950, due=now - timedelta(hours=5), status="needs_review")
    promise("Remind him about cardio (second)", minutes_ago=940, due=now - timedelta(hours=4), status="needs_review")
    promise("Tell him when a WhatsApp notification arrives", minutes_ago=2000)
    promise("Don't reply to WhatsApp unless he asks", minutes_ago=1990)
    for n in range(6):
        promise(f"Switch Spotify back to Into You (wording {n})", minutes_ago=60 - n)
    db.commit()
    return made


def test_her_newest_promises_are_in_view_and_the_rest_are_still_named(db, alice):
    made = _her_promises_like_the_spotify_evening(db, alice.id)
    view = memory_manager.view(db, alice.id)
    shown, _, hidden = view.partition("more open promises (older):")
    # The timed one still ahead comes first; then hers newest first: the latest Spotify wordings, not the backlog.
    assert shown.index("cardio at 3 PM") < shown.index("wording 5") < shown.index("wording 4")
    assert "wording 5" in shown and "WhatsApp" not in shown
    # Everything left out is still reachable by its id.
    for text, entry in made.items():
        if text not in shown:
            assert f"[{memory_manager.ref(entry)}]" in hidden or "…" in hidden


def test_noting_a_promise_shows_her_other_open_ones_to_see_a_repeat(db, alice, bob):
    from app.ai.tools.memory.tools import build_memory_tools

    first = memory_manager.note_commitment(db, alice.id, "Switch Spotify back to Into You if it moves on")
    db.commit()
    tools = {t.name: t for t in build_memory_tools(alice.id)}
    kept = asyncio.run(tools["note_commitment"].ainvoke({
        "what": "Keep Into You playing, press Previous if it changes", "when": "Spotify moves to another song"}))
    assert kept.startswith("Kept: promise [")
    assert f'Also open: [{memory_manager.ref(first)}] "Switch Spotify back to Into You if it moves on"' in kept
    assert "keep one" in kept
    # Another user's promises are never shown, and with none other open there's nothing to add.
    bobs = {t.name: t for t in build_memory_tools(bob.id)}
    alone = asyncio.run(bobs["note_commitment"].ainvoke({"what": "Remind Bob about the dentist"}))
    assert alone.startswith("Kept: promise [") and "Also open" not in alone
