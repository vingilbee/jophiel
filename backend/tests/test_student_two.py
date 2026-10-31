"""Student 2 live (app/ai/state/student_two.py, services/student_two.py): the situation built from live state in its
training shape, the decision parsed and checked, live state read without writing, and its work carried out.
Pure tests need nothing; the rest use real PostgreSQL (``TEST_DATABASE_URL``). No model runs: answers are scripted."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.ai.Cognitive.simple import events as E
from app.ai.state import student_two as S
from app.db.models import CoordinatorWork, MemoryContext, MemoryManagerState, WorkingItem
from app.services import chat as chat_service
from app.services import desktop_game, memory_manager, student_two
from tests.db_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_desktop_game import _paired_env, enabled  # noqa: F401  (autouse fixture)
from tests.test_desktop_sessions import _user, alice, bob, db  # noqa: F401  (fixtures)
from app.services import desktop_sessions as svc

NOW = datetime(2026, 10, 28, 21, 15, tzinfo=timezone.utc)
DIALS = {"length": "a_little", "warmth": "somewhat", "playfulness": "none", "directness": "somewhat"}
SAY = dict(intention="answer", work="no_change", communication="respond", what_to_communicate="sure",
           move="react_briefly", dials=DIALS, about=["M"])


def _live(**over):
    base = dict(trigger="user_message", domain="chat", now=NOW, new_message="what were we doing?",
                lines=(S.Line("user", "let's plan the trip", NOW - timedelta(minutes=5)),
                       S.Line("columbina", "Lisbon or Porto?", NOW - timedelta(minutes=4))))
    return S.Live(**(base | over))


# --- the situation (pure) ----------------------------------------------------------------------------


def test_a_chat_situation_renders_like_training_with_refs_to_live_records():
    goal = S.Goal("g-1", "Plan the trip", "user", "active", "the user is satisfied with it", NOW - timedelta(hours=1))
    work = S.Work("coordinator:w-1", "thinking", "which city", "running", NOW - timedelta(seconds=30))
    situation = S.snapshot(_live(goals=(goal,), work=(work,)))
    text = S.render(situation, None)
    lines = text.split("\n")
    assert lines[:3] == ["TRIGGER: user message",
                         "TIME: Wednesday 2026-10-28, 21:15, evening for the user; their last message 0 s ago; "
                         "her last message 4 min ago.",
                         "SETTING: just talking; no shared activity is running."]
    assert "- 5 min ago [U1] User: let's plan the trip" in lines and "- 4 min ago Columbina: Lisbon or Porto?" in lines
    assert "[M] NEW MESSAGE FROM THE USER (just now; nothing done about it yet): what were we doing?" in lines
    assert any(line.startswith("[G1] Plan the trip — open, active; set by the user 1.0 h ago") for line in lines)
    assert any(line.startswith("[W1] thinking — which city (for [nothing]): running") for line in lines)
    assert "REQUESTS FROM THE USER: none recorded." in lines and "ACTIVITY" not in text
    assert situation.refs == {"G1": "g-1", "W1": "coordinator:w-1"}
    assert not any(line in E.EXCHANGE for line in lines)  # qwen-4's rendering
    assert E.EXCHANGE[1] in S.render(situation, "exchange-line-v1").split("\n")  # qwen-5's


def test_a_game_situation_has_its_activity_step_and_her_thoughts():
    game = S.Activity("s-1", "running", "Playing Minecraft together; the plan: get iron; doing now: mine stone")
    goal = S.Goal("g-1", "Get iron", "user", "active", "has iron ingots", NOW - timedelta(minutes=20), "objective")
    step = S.Work("step:s-1:3", "execution", "mine stone", "running", NOW - timedelta(minutes=2), goal_key="g-1")
    thought = S.Thought("t-1", "question", "Should we go down the cave?", NOW - timedelta(minutes=1))
    situation = S.snapshot(_live(domain="minecraft", activity=game, goals=(goal,), work=(step,), thought=thought))
    text = S.render(situation, None)
    assert "SETTING: playing Minecraft together" in text and "Permission:" in text
    assert "[A1] ACTIVITY (running): Playing Minecraft together; the plan: get iron; doing now: mine stone" in text
    assert "[W1] execution — mine stone (for [G1]): running" in text
    assert ("[T1] HER PRIVATE THOUGHT (question, set aside 1 min ago, before [M] arrived; not said to anyone): "
            "Should we go down the cave?") in text
    assert situation.refs == {"G1": "g-1", "W1": "step:s-1:3", "A1": "s-1", "T1": "t-1"}


def test_a_finished_thought_is_her_just_finished_thought_with_the_thinking_behind_it():
    thought = S.Thought("coordinator:w-1", "conclusion", "Stay a year, then decide.", NOW - timedelta(seconds=5),
                        origin="just_finished")
    thinking = S.Work("coordinator:w-1", "thinking", "the Berlin job", "completed", NOW - timedelta(minutes=2),
                      outcome="Stay a year, then decide.", for_thought=True)
    situation = S.snapshot(_live(trigger="thought_result", new_message="", thought=thought, work=(thinking,)))
    assert E.invariants(situation.snapshot) == []
    text = S.render(situation, None)
    assert text.startswith("TRIGGER: thought result") and "NEW MESSAGE: none." in text
    assert "[T1] HER PRIVATE THOUGHT (conclusion, just finished; not said to anyone): Stay a year" in text
    assert "[W1] thinking — the Berlin job (for [T1]): completed" in text
    d, _ = S.parse(json.dumps(dict(SAY, about=["T1"])))
    assert S.check(d, situation) == []
    d, _ = S.parse(json.dumps(dict(SAY, about=["M"])))  # there's no new message to answer
    assert S.check(d, situation)
    failed = S.Work("coordinator:w-1", "thinking", "the Berlin job", "failed", NOW - timedelta(minutes=2),
                    outcome="didn't reach a conclusion (deadline)")
    situation = S.snapshot(_live(trigger="work_outcome", new_message="", work=(failed,)))
    assert E.invariants(situation.snapshot) == [] and "HER PRIVATE THOUGHT: none." in S.render(situation, None)


def test_decisions_are_parsed_and_checked_never_repaired():
    situation = S.snapshot(_live())
    assert S.parse("no json here")[1] == "not_json"
    d, problem = S.parse("<think></think>" + json.dumps(SAY))
    assert problem == "" and S.check(d, situation) == []
    d, _ = S.parse(json.dumps(dict(SAY, work="pause", target="G1")))  # there's no goal here
    assert S.check(d, situation) and S.recall_need(d) == "not_required"
    d, _ = S.parse(json.dumps(dict(SAY, work="request_thinking", target="M", think_about="the trip",
                                   think_form="whether")))
    assert S.check(d, situation) == [] and S.thinking_need(d) == "required"


def test_the_mouth_gets_the_decision_and_never_a_claim_that_work_is_done():
    situation = S.snapshot(_live())
    d, _ = S.parse(json.dumps(dict(SAY, work="request_recall", target="M")))
    text = S.brief(d, situation)
    assert "What to get across: sure" in text and "look up your memory about their new message" in text
    assert "don't say it has already happened" in text
    pursue, _ = S.parse(json.dumps(dict(SAY, work="pursue", target="M")))
    assert "with your tools or specialists" in S.brief(pursue, situation)


def test_a_server_that_is_down_is_unavailable_not_silence(monkeypatch):
    monkeypatch.setattr(student_two, "manifest", lambda: {"name": "x", "system": "s", "rendering": None})
    outcome = student_two.decide(_live())  # tests/conftest.py refuses the HTTP call
    assert outcome.status == "unavailable" and outcome.decision is None and "ConnectionRefused" in outcome.error
    unreadable = student_two.decide(_live(), ask=lambda m, t: "I think she should")
    assert unreadable.status == "invalid" and unreadable.problems == ["not_json"]


# --- live state (real PostgreSQL) ----------------------------------------------------------------------


def _talk(db, user, *lines):
    conversation = chat_service.get_or_create_conversation(db, user_id=user.id, conversation_id=None)
    rows = [chat_service.add_message(db, conversation=conversation, role=role, content=text) for role, text in lines]
    db.commit()
    return conversation, rows


def test_gather_reads_the_conversation_before_the_message_and_the_focused_goals(db, alice):
    conversation, rows = _talk(db, alice, ("user", "let's plan the trip"), ("assistant", "Lisbon or Porto?"),
                               ("user", "what were we doing?"))
    goal = memory_manager.set_goal(db, alice.id, "Plan the trip")
    db.commit()
    contexts = db.scalar(select(func.count()).select_from(MemoryContext))
    live = student_two.gather(db, user_id=alice.id, conversation_id=conversation.id, trigger_message_id=rows[-1].id,
                              message=rows[-1].content, zone="Asia/Jakarta")
    assert [line.text for line in live.lines] == ["let's plan the trip", "Lisbon or Porto?"]
    assert live.domain == "chat" and live.activity is None and live.now.utcoffset() == timedelta(hours=7)
    assert [g.text for g in live.goals] == ["Plan the trip"] and live.goals[0].key == str(goal.id)
    assert db.scalar(select(func.count()).select_from(MemoryContext)) == contexts  # it only reads


def test_gather_for_a_new_user_writes_nothing(db, alice):
    conversation, rows = _talk(db, alice, ("user", "hi"))
    live = student_two.gather(db, user_id=alice.id, conversation_id=conversation.id, trigger_message_id=rows[0].id,
                              message="hi", zone=None)
    assert live.goals == () and live.lines == ()
    assert db.get(MemoryManagerState, alice.id) is None and db.scalar(select(func.count()).select_from(MemoryContext)) == 0


def test_a_game_conversation_is_the_minecraft_setting_with_its_goal_and_step(db, alice):
    _paired_env(db, alice)
    desktop_game.start(db, alice.id, goal="go to the Nether", steps=[{"do": "look around", "done_when": "seen"}])
    session = svc.list_sessions(db, alice.id, live_only=True)[0]
    svc.controller_ready(db, session.id)
    conversation, rows = _talk(db, alice, ("user", "how's it going?"))
    session.conversation_id = conversation.id
    db.commit()
    live = student_two.gather(db, user_id=alice.id, conversation_id=conversation.id, trigger_message_id=rows[0].id,
                              message="how's it going?", zone=None)
    assert live.domain == "minecraft" and live.activity.key == str(session.id)
    assert [g.key for g in live.goals][0] == str(session.goal_item_id)
    situation = S.snapshot(live)
    assert "A1" in situation.refs and situation.refs["G1"] == str(session.goal_item_id)


def test_its_work_is_carried_out_on_the_record_its_ref_names(db, alice):
    conversation, rows = _talk(db, alice, ("user", "let's stop planning for now"))
    goal = memory_manager.set_goal(db, alice.id, "Plan the trip")
    work = CoordinatorWork(request_id=uuid.uuid4(), user_id=alice.id, conversation_id=conversation.id, kind="mind",
                           status="queued", input={"message": "which city"}, input_versions={},
                           deadline_at=datetime.now(timezone.utc) + timedelta(minutes=3), idempotency_key="k1")
    db.add(work)
    db.commit()
    live = student_two.gather(db, user_id=alice.id, conversation_id=conversation.id, trigger_message_id=rows[0].id,
                              message=rows[0].content, zone=None)

    def outcome(**fields):
        return student_two.decide(live, ask=lambda m, t: json.dumps(dict(SAY, **fields)))

    paused = outcome(work="pause", target="G1")
    assert paused.ok and student_two.apply(db, paused, user_id=alice.id, message_id=rows[0].id) == "goal suspended"
    db.refresh(goal)
    assert goal.status == "suspended"
    cancelled = outcome(work="cancel", target="W1")
    assert student_two.apply(db, cancelled, user_id=alice.id, message_id=rows[0].id) == "background work cancelled"
    db.refresh(work)
    assert work.status == "cancelled"
    taken = outcome(work="pursue", target="M")
    assert student_two.apply(db, taken, user_id=alice.id, message_id=rows[0].id) == "taken up by her reply (its tools)"
    assert db.scalar(select(WorkingItem).where(WorkingItem.id == goal.id)).status == "suspended"
