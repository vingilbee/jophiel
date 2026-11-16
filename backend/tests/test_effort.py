"""Her effort (docs/learning-system/AUDIT-2026-11-04-learning-from-experience.md, stage 1): the story of what she works toward.

The record's shape, collapsing, time and rendering need nothing. Memory's side (her items under the goal
they serve, the user's rules, places), the builder and the planner keeping rules need real PostgreSQL
(``TEST_DATABASE_URL``, a throwaway server, never the dev database).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.ai.mind import effort as effort_mod
from app.ai.mind.effort import ActiveTime, Effort, Entry, collapse, render
from app.ai.mind.hard import HardInput, Problem, follows_id
from app.ai.mind.hard import parse as parse_thought
from app.ai.mind.hard import render as render_thought
from app.ai.mind.plan import parse as parse_plan

T0 = datetime(2026, 11, 4, 15, 44, tzinfo=timezone.utc)


def _at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _action(minutes, text, key, *, failed=False, detail="target=block:iron_ore"):
    return Entry(at=_at(minutes), kind="action", text=text, key=key, failed=failed, details=(detail,))


# --------------------------------------------------------------------------- #
# Pure: collapsing, time, rendering, what the models answer
# --------------------------------------------------------------------------- #


def test_repeats_collapse_but_a_conclusion_between_them_breaks_the_run():
    fail = "hands: go_to → failed: no iron_ore in sight within 64 blocks"
    entries = [_action(1, fail, "k", failed=True), _action(2, fail, "k", failed=True),
               Entry(at=_at(3), kind="conclusion", text="decision: explore the cave", ref="D111111"),
               _action(4, fail, "k", failed=True, detail="target=player:x")]
    out = collapse(entries)
    assert [(e.kind, e.count) for e in out] == [("action", 2), ("conclusion", 1), ("action", 1)]
    assert out[0].until == _at(2) and out[0].details == ("target=block:iron_ore", "target=block:iron_ore")


def test_a_run_still_going_is_not_shown_as_lost():
    """Seen live 2026-11-05: her planner was asked 0.1 s into a bed craft, read "result not seen", and doubted
    the bed she then had. A run with no end yet is still going; one that ended unseen still says so."""
    from app.db.models import DecisionExecution
    from app.services.effort import _action_entry

    def run(**kw):
        return DecisionExecution(kind="skill_run", result="unknown", started_at=_at(0),
                                 intended={"skill": "craft", "params": {"item": "white_bed"}}, executed={}, **kw)

    assert _action_entry(run(ended_at=None, deviation=None)).text == "hands: craft → started, no result yet"
    lost = _action_entry(run(ended_at=_at(1), deviation="stopping it (danger) failed: the pack didn't answer"))
    assert lost.text.startswith("hands: craft → result not seen (stopping it")


def test_active_and_paused_time_are_counted_apart():
    active = ActiveTime(since=_at(0), running_at_start=True,
                        changes=[(_at(10), False), (_at(15), True)])
    assert active.split(_at(30)) == (25 * 60, 5 * 60)
    # 20 min of play back from minute 30: 15 after the resume, then 5 before the pause.
    assert active.window_start(_at(30), seconds=20 * 60) == _at(5)
    assert active.window_start(_at(30), seconds=60 * 60) == _at(0)


def _effort(**overrides) -> Effort:
    base = dict(outcome="Find a full set of iron armor", whose="the user's", outcome_ref="16539475", since=T0,
                active_s=26 * 60, paused_s=2 * 60, approach="follow tearDrop12", approach_since=_at(22),
                approach_changes=11, rules=(("c0ffee12", "Find it myself, don't follow", "No, don't follow me."),),
                places=(("the cave Vincent showed", "-42 105 -210"),), counted=("mine: raw_iron +1, coal +8",),
                spots="Where your hands' runs ended: 9 different spots (16-block areas), height 34 to 112.",
                senses=("Openings: detected only under open sky.",), coverage=("You're under rock.",),
                timeline=(Entry(at=_at(5), kind="conclusion", text="decision: explore the cave", ref="D211a81"),
                          Entry(at=_at(12), kind="conclusion", text="decision: explore the cave", ref="Dc8eb39",
                                follows="D211a81"),
                          Entry(at=_at(20), kind="user", text='the user: "No, don\'t follow me."')),
                full_since=_at(0))
    return Effort(**{**base, **overrides})


def test_render_keeps_outcome_and_approach_apart_and_says_who_judges():
    lines = render(_effort(), _at(28), user="Vincent")
    text = "\n".join(lines)
    assert lines[0].startswith('Outcome (the user\'s, [16539475], set 15:44): "Find a full set of iron armor"')
    assert "Now: 16:12. Time on it: 28 min since it was set (26 min playing, 2 min paused or away)." in text
    assert 'Your current approach (the plan, not the outcome): "follow tearDrop12", since 16:06; the plan\'s own goal ' \
           "changed 11 times" in text
    assert '[c0ffee12] Find it myself, don\'t follow (their words: "No, don\'t follow me.")' in text
    assert "what counts as progress is yours to judge" in text
    assert "  15:56 [Dc8eb39], carrying out D211a81 decision: explore the cave" in text


def test_a_long_timeline_summarises_runs_but_keeps_conclusions_and_the_users_words(monkeypatch):
    monkeypatch.setattr(effort_mod, "MAX_LISTED", 6)
    timeline = []
    for i in range(5):
        timeline.append(Entry(at=_at(i * 4), kind="conclusion", text=f"decision {i}", ref=f"D00000{i}"))
        timeline += [_action(i * 4 + 1, f"hands: go_to → failed: no path {i}", None, failed=True),
                     _action(i * 4 + 2, "hands: go_to → done", None)]
    timeline.append(Entry(at=_at(21), kind="user", text='the user: "Deeper."'))
    text = "\n".join(render(_effort(timeline=tuple(timeline)), _at(22)))
    for i in range(5):
        assert f"[D00000{i}] decision {i}" in text
    assert 'the user: "Deeper."' in text
    assert "hands: 2 runs, 1 failed (failed: no path 0) (summarised)" in text


def test_her_thought_reads_the_effort_instead_of_the_plans_goal():
    problem = Problem("failing", "mine iron failed 4 times", topic="keeps failing: mine", query="iron")
    text = render_thought(HardInput(problem=problem, goal="follow tearDrop12",
                                    effort=("Outcome (the user's, [1], set 15:44): \"iron armor\"",)))
    assert "Your effort (recorded by code; judging it is yours):" in text and "Your goal:" not in text
    assert "Your goal: follow tearDrop12" in render_thought(HardInput(problem=problem, goal="follow tearDrop12"))


def test_follows_is_only_what_the_model_named():
    assert follows_id("carrying out [D1a2b3c]") == "D1a2b3c"
    assert follows_id("D1A2B3C") is None and follows_id("") is None and follows_id("the cave one") is None
    out = parse_thought({"kind": "decision", "conclusion": "go down", "thinking": "", "for_hands": "",
                         "question": "", "follows": "D211a81"})
    assert out.follows == "D211a81"


def test_the_planner_names_rules_places_and_what_it_carries_out():
    out = parse_plan({"decision": "keep", "why": "w", "goal": "", "target": "", "steps": [], "drop": [], "rules": [],
                      "ask": "", "say": "", "follows": "D211a81",
                      "user_rules": [{"rule": "Find it myself", "words": "This is your task."},
                                     {"rule": "no words", "words": ""}],
                      "lift_rules": [" c0ffee12 "],
                      "places": [{"name": "the cave", "where": "-42 105 -210", "words": "Direction is the cave."},
                                 {"name": "", "where": "", "words": ""}]})
    assert out.follows == "D211a81" and out.user_rules == [("Find it myself", "This is your task.")]
    assert out.lift_rules == ["c0ffee12"] and out.places == [("the cave", "-42 105 -210", "Direction is the cave.")]


def test_shape_and_coverage_come_from_her_own_view():
    from app.services import mind_minecraft

    state = {"text": "Health 10.\nAround you: underground: stone 2 m above your head, rock all the way up.\n"
                     "Ways (walking along the ground from where you stand):\n"
                     "  ahead (south): walk 6 m to (1 2 3), going down 4 m, then a stone wall 3 m high\n"
                     "Open space you're in (air connected to where you stand): 240 open blocks, 9 × 14 m across.\n"
                     "Where it goes on (passages out of what was counted, widest first):\n"
                     "  ahead (south): it goes on past 12 m, level with you and down to 4 m below\n"
                     "Land out to 48 m: level all around.\n"
                     "Way out: no opening to the sky found through open space within 24 m."}
    assert "Ways" not in mind_minecraft._brief(state)
    shape = mind_minecraft._shape(state)
    assert "going down 4 m" in shape and "Way out:" in shape and "Land out to" not in shape
    assert "9 × 14 m across" in shape and "down to 4 m below" in shape  # the passages underground
    [line] = mind_minecraft.coverage(state)
    assert "the openings list is empty here by design" in line and "go" not in line.split(":")[0]
    assert "open sky" in mind_minecraft.coverage({"text": "Around you: outdoors, open sky above.\nWays (x):"})[0]
    assert "No open space was reported" in mind_minecraft.coverage({"text": "Around you: outdoors.\nWays (x):"})[-1]


# --------------------------------------------------------------------------- #
# Real PostgreSQL: memory, the builder, the planner keeping the user's rules
# --------------------------------------------------------------------------- #

from app.db.models import (  # noqa: E402
    ChatMessage,
    Conversation,
    DecisionExecution,
    DesktopSessionEvent,
    MemoryEvidence,
    MindThought,
    WorkingItem,
)
from app.services import effort, game_planning, memory_manager  # noqa: E402
from app.services.memory_manager import MemoryError  # noqa: E402
from tests.db_support import database  # noqa: E402,F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: E402,F401  (autouse fixture)
from tests.test_desktop_sessions import _user, alice, db  # noqa: E402,F401  (fixtures)
from tests.test_game_planning import ScriptedPlanner, _run, _session, planning  # noqa: E402,F401
from app.ai.mind.plan import PlanDecision  # noqa: E402


def _goal(db, session, text="Find a full set of iron armor"):
    goal = memory_manager.on_plan_set(db, session, goal_text=text, source="columbina_user_turn")
    db.commit()
    return goal


def _say(db, user_id, role, content, at=None):
    conversation = db.query(Conversation).filter_by(user_id=user_id).first()
    if conversation is None:
        conversation = Conversation(user_id=user_id)
        db.add(conversation)
        db.flush()
    message = ChatMessage(conversation_id=conversation.id, role=role, content=content)
    if at is not None:
        message.created_at = at
    db.add(message)
    db.commit()
    return message


def test_her_items_and_the_users_rules_go_under_the_goal_they_serve(db, alice, planning):
    session = _session(db, alice, goal="follow tearDrop12")
    goal = _goal(db, session)
    decision = memory_manager.add_item(db, alice.id, kind="decision", text_="explore the cave", owner="columbina",
                                       epistemic="inferred", parent_id=goal.id)
    with pytest.raises(MemoryError):
        memory_manager.add_item(db, alice.id, kind="commitment", text_="x", owner="columbina", owed_to="user",
                                epistemic="inferred", parent_id=goal.id)
    with pytest.raises(MemoryError):
        memory_manager.add_constraint(db, alice.id, goal, "find it myself", user_words="  ")
    rule = memory_manager.add_constraint(db, alice.id, goal, "Find it myself", user_words="This is your task.")
    again = memory_manager.add_constraint(db, alice.id, goal, "find it  myself", user_words="This is your task.")
    place = memory_manager.add_reference(db, alice.id, goal, "the cave Vincent showed", where="-42 105 -210")
    db.commit()
    assert again.id == rule.id and decision.parent_id == goal.id and rule.status == "active"
    assert [e.id for e in memory_manager.serving(db, goal)] == [decision.id, rule.id]
    assert [e.id for e in memory_manager.references(db, goal)] == [place.id]
    memory_manager.lift_constraint(db, alice.id, memory_manager.ref(rule), reason="the user took it back")
    db.commit()
    assert db.get(WorkingItem, rule.id).status == "lifted"


def test_the_effort_is_built_from_what_happened(db, alice, planning):
    session = _session(db, alice, goal="follow tearDrop12")
    goal = _goal(db, session)
    start = goal.created_at
    db.add(DesktopSessionEvent(session_id=session.id, type="desktop.plan_set", state_revision=1,
                               occurred_at=start + timedelta(minutes=1),
                               payload={"goal": "follow tearDrop12", "source": "columbina_user_turn"}))
    for minute in (2, 3):
        db.add(DecisionExecution(id=uuid.uuid4(), user_id=alice.id, kind="skill_run", executor="hands",
                                 game_session_id=session.id, result="failed",
                                 intended={"skill": "go_to", "params": {"target": "block:iron_ore", "time_limit": 60}},
                                 executed={"reason": "no iron_ore in sight within 64 blocks",
                                           "data": {"x": 1, "y": 40, "z": 2}},
                                 started_at=start + timedelta(minutes=minute),
                                 captured_at=start + timedelta(minutes=minute),
                                 expires_at=start + timedelta(days=30)))
    db.add(DecisionExecution(id=uuid.uuid4(), user_id=alice.id, kind="skill_run", executor="hands",
                             game_session_id=session.id, result="not_executed", intended={"skill": "craft"},
                             captured_at=start + timedelta(minutes=3), expires_at=start + timedelta(days=30)))
    thought = MindThought(user_id=alice.id, session_id=session.id, source="minecraft", activity_id=session.id,
                          level="hard", text="I'll explore the cave.", conclusion_kind="decision", trigger="failing",
                          topic="keeps failing: go_to", follows=None, created_at=start + timedelta(minutes=4))
    db.add(thought)
    _say(db, alice.id, "user", "No, don't follow me. This is your task.", at=start + timedelta(minutes=5))
    db.commit()
    built = effort.for_session(db, session, now=start + timedelta(minutes=6))
    assert built.outcome == "Find a full set of iron armor" and built.approach.startswith("follow tearDrop12")
    kinds = [(e.kind, e.count) for e in built.timeline]
    assert kinds == [("plan", 1), ("action", 2), ("conclusion", 1), ("user", 1)]  # not_executed left out
    assert built.timeline[2].ref == effort.short_id(thought.id)
    assert built.spots.startswith("Where your hands' runs ended: 1 different spots")
    text = "\n".join(effort.render(built, start + timedelta(minutes=6)))
    assert "hands: go_to → failed: no iron_ore in sight within 64 blocks ×2 (target=block:iron_ore ×2)" in text


def test_the_planner_reads_her_effort_and_keeps_the_users_rule_only_when_they_said_it(db, alice, planning):
    session = _session(db, alice, goal="follow tearDrop12")
    goal = _goal(db, session)
    _say(db, alice.id, "user", "No, don't follow me. This is your task.")
    game_planning.request(db, session, "struggling: go_to failed 4 times")
    db.commit()
    planner = ScriptedPlanner(PlanDecision(
        "keep", why="I'll find the iron myself.", follows="D211a81",
        user_rules=[("Find the iron myself, don't follow", "This is your task."), ("made up", "never said this")],
        places=[("the cave Vincent showed", "-42 105 -210", "")]))
    assert _run(db, session, planner).startswith("kept")
    text = planner.texts[0]
    assert "## Her effort (recorded by code; judging it is yours)" in text
    assert 'Outcome (the user\'s' in text and '"Find a full set of iron armor"' in text
    rules = [e for e in memory_manager.serving(db, goal) if e.kind == "constraint"]
    assert [(r.text, r.user_words) for r in rules] == [("Find the iron myself, don't follow", "This is your task.")]
    assert db.query(MemoryEvidence).filter_by(item_id=rules[0].id, role="origin").count() == 1
    assert [p.name for p in memory_manager.references(db, goal)] == ["the cave Vincent showed"]
    assert db.query(MindThought).filter_by(kind="plan").one().follows == "D211a81"
    # Next time she plans, the rule is in force in what she reads.
    text = game_planning.build_input(db, db.get(type(session), session.id), ["struggling: again"], None)
    assert f"[{memory_manager.ref(rules[0])}] Find the iron myself, don't follow" in text


def test_a_long_stretch_with_nothing_recorded_is_marked_for_her_to_judge():
    timeline = (Entry(at=_at(5), kind="conclusion", text="decision: explore the cave", ref="D211a81"),
                Entry(at=_at(5 + 4 * 60), kind="user", text='the user: "new map, same goal"'))
    text = "\n".join(render(_effort(timeline=timeline), _at(5 + 4 * 60 + 1)))
    assert "  — 4 h 00 min with nothing recorded —" in text
    assert "Nothing was recorded" not in text  # the last thing was a minute ago
    assert "Nothing was recorded for 60 min before now." in "\n".join(render(_effort(timeline=timeline), _at(5 + 5 * 60)))


def test_going_in_and_out_makes_her_think_hard_at_most_once_per_gap():
    from app.services import mind_minecraft

    st = mind_minecraft.Triggers()
    flip = ["you went underground"]
    assert mind_minecraft.surprise_from_gate(flip, None, {}, st, now=1000.0) is not None
    assert mind_minecraft.surprise_from_gate(["you're outdoors again"], None, {}, st, now=1030.0) is None
    assert mind_minecraft.surprise_from_gate(["you lost 3 hearts"], None, {}, st, now=1031.0) is not None
    assert mind_minecraft.surprise_from_gate(flip, None, {}, st, now=1000.0 + mind_minecraft.PLACE_SURPRISE_GAP_S) is not None


def test_a_revision_can_change_her_approach(db, alice, planning):
    from app.ai.mind.plan import PlanStep

    session = _session(db, alice, goal="follow tearDrop12")
    _goal(db, session)
    memory_manager.add_constraint(db, alice.id, memory_manager.session_goal(db, session),
                                  "Find the iron myself", user_words="This is your task.")
    db.commit()
    game_planning.request(db, session, "stuck: following goes nowhere")
    db.commit()
    planner = ScriptedPlanner(PlanDecision("revise", why="Not following any more.", goal="search the cave myself",
                                           steps=[PlanStep(keep="s2")]))
    assert _run(db, session, planner).startswith("set (revised)")
    db.refresh(session)
    assert session.plan["goal"] == "search the cave myself"
    assert "Find the iron myself" in session.plan["constraints"]  # her hands keep the user's rule


def test_a_rethink_right_after_a_plan_change_waits(db, alice, planning):
    from app.db.models import AgentEventJob

    session = _session(db, alice)
    db.add(DesktopSessionEvent(session_id=session.id, type="desktop.plan_set", state_revision=1,
                               payload={"goal": "mine some iron", "source": "columbina_mind"}))
    db.commit()
    game_planning.request(db, session, "rethink: try the cave")
    db.commit()
    job = db.query(AgentEventJob).filter_by(source=game_planning.SOURCE).one()
    waited = job.not_before
    assert (waited - datetime.now(timezone.utc)).total_seconds() > game_planning.RETHINK_GAP_S - 10
    game_planning.request(db, session, "stuck: no path")  # news that can't wait
    db.commit()
    db.refresh(job)
    assert job.not_before <= datetime.now(timezone.utc)


def test_resuming_for_the_user_brings_a_suspended_goal_back(db, alice, planning):
    from app.services import desktop_game

    session = _session(db, alice, state="paused")
    goal = _goal(db, session)
    memory_manager.resolve(db, alice.id, goal.id, status="suspended", reason="on hold while paused")
    db.commit()
    db.refresh(session)
    assert "Resumed" in desktop_game.resume(db, alice.id)
    db.refresh(goal)
    db.refresh(session)
    assert goal.status == "active" and session.state == "running"


# --------------------------------------------------------------------------- #
# Stage 2: she judges whether it's getting anywhere; nothing counts it for her
# --------------------------------------------------------------------------- #

def test_her_judgment_of_the_effort_is_kept_as_she_gave_it_and_only_where_it_fits():
    from app.ai.mind.hard import assessment

    out = {"kind": "guess", "conclusion": "The ore may be there but out of sight.", "progress": "no",
           "cause": "perception", "check": "go up to the ridge and look for cave mouths",
           "if_supported": "a cave in view", "leaves_unknown": "no cave seen doesn't mean there's none"}
    assert parse_thought(out).assessment == {k: out[k] for k in ("progress", "cause", "check", "if_supported",
                                                                  "leaves_unknown")}
    # Getting somewhere: no cause or check, whatever the model filled in.
    assert assessment({**out, "progress": "yes"}) == {"progress": "yes"}
    # A cause the model made up isn't kept, nor a check without a cause.
    assert assessment({**out, "cause": "laziness"}) == {"progress": "no"}
    assert assessment({"progress": "", "cause": "", "check": ""}) is None


def test_she_is_asked_to_judge_only_when_there_is_an_effort():
    from app.ai.mind.hard import HardThinker

    brain = HardThinker.__new__(HardThinker)
    with_effort = brain.instructions("review", "Vincent", effort=True)
    without = brain.instructions("review", "Vincent")
    assert '"perception"' in with_effort and "No rule of time or tries decides it" in with_effort
    assert "not to announce" in with_effort
    assert '"progress", "cause", "check", "if_supported", "leaves_unknown": always empty' in without
    assert "look back over the whole effort" in with_effort


def test_small_surprises_are_added_up_and_never_push_out_a_look_back():
    from app.services import mind_hard

    review = Problem("review", "A step of your plan just ended.", topic="looking back: find iron", query="iron")
    surprise = Problem("surprise", "x", topic="surprise: you got cobblestone +1", query="cobblestone")
    kept = mind_hard._better(review, surprise)
    kept = mind_hard._better(kept, surprise)
    assert kept.trigger == "review" and kept.anchor["meanwhile"] == {"surprise: you got cobblestone +1": 2}
    # A newer surprise replaces an older one, carrying it as a counted line.
    newer = mind_hard._better(surprise, Problem("surprise", "y", topic="surprise: you went underground", query="u"))
    assert newer.topic == "surprise: you went underground"
    assert newer.anchor["meanwhile"] == {"surprise: you got cobblestone +1": 1}
    # A look back replaces a waiting surprise and keeps it; other problems behave as before.
    assert mind_hard._better(newer, review).anchor["meanwhile"] == {"surprise: you got cobblestone +1": 1,
                                                                      "surprise: you went underground": 1}
    big = Problem("big", "z", topic="before: x", query="x")
    assert mind_hard._better(review, big) is big
    text = mind_hard.with_meanwhile(kept).text
    assert text.startswith("A step of your plan just ended.") and "- surprise: you got cobblestone +1 (×2)" in text


def test_her_earlier_judgments_show_on_her_timeline():
    from app.services.effort import judged

    assert judged(None) == "" and judged({"cause": "ability"}) == ""
    assert judged({"progress": "no", "cause": "perception", "check": "go up and look"}) == (
        " [getting anywhere: no; cause: perception; check: go up and look]")
    entry = Entry(at=_at(5), kind="conclusion", ref="Da1b2c3",
                  text="guess" + judged({"progress": "no", "cause": "perception", "check": "x" * 40}) + ": " + "y" * 150)
    line = effort_mod._entry_line(entry)
    assert line.endswith("y" * 150)  # her judgment never crowds out the conclusion itself


def test_the_planner_looks_first_and_writes_intentions_not_stand_in_counts():
    from app.ai.mind.plan import INSTRUCTIONS, SCHEMA

    order = list(SCHEMA["properties"])
    assert order.index("sees") < order.index("cannot_see") < order.index("progress") < order.index("decision")
    assert "Never a count that stands in for an intention" in INSTRUCTIONS
    assert "keep the intention anyway" in INSTRUCTIONS and "No rule of time or tries decides it" in INSTRUCTIONS
    decision = parse_plan({"sees": "Outdoors on grass; no ore in view.", "cannot_see": "What's under the ground.",
                           "progress": "no", "decision": "revise", "why": "Digging down is Vincent's way.",
                           "goal": "dig down to a cave", "target": "iron_ore",
                           "steps": [{"keep": "", "do": "dig straight down here until I break into open space",
                                      "done_when": "in an open space that goes on"}],
                           "drop": [], "rules": [], "ask": "", "say": "", "follows": "", "user_rules": [],
                           "lift_rules": [], "places": []})
    assert (decision.sees, decision.cannot_see, decision.progress) == (
        "Outdoors on grass; no ore in view.", "What's under the ground.", "no")
    assert decision.steps[0].done_when == "in an open space that goes on"
    assert parse_plan({"decision": "keep", "progress": "maybe"}).progress is None


def test_the_open_space_and_her_height_reach_her_mind():
    from app.services import mind_minecraft

    state = {"text": "Health 10.\nYour height: 59 now, 64 a minute ago; between 59 and 76 over the last 5 min.\n"
                     "Around you: underground: stone 1 m above your head, rock all the way up.\n"
                     "Open space you're in (air connected to where you stand): 6 open blocks, 1 × 1 m across.\n"
                     "Where it goes on (passages out of what was counted, widest first):\n"
                     "  up: it goes on higher than 4 m above your head (1 open block wide)\n"}
    assert "Your height: 59 now" in mind_minecraft._brief(state)
    shape = mind_minecraft._shape(state)
    assert "1 × 1 m across" in shape and "higher than 4 m above your head" in shape
    assert any(line.startswith("Open space:") for line in mind_minecraft.SENSES)
