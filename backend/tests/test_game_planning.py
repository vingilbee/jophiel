"""Her planner: the one author of her game plan (app/ai/mind/plan.py, services/game_planning).

Parsing a decision, merging steps by id and the "what it takes" text need nothing. The
rest (asking for a plan, routing her hands' news to it, applying each decision, dropping
a stale one) needs real PostgreSQL (``TEST_DATABASE_URL``, a throwaway server, never the
dev database); the pack and the planning model are fakes.
"""
from __future__ import annotations

import pytest

from app.ai.mind.plan import INSTRUCTIONS, PlanDecision, PlanStep, parse
from app.services import game_planning
from app.services.game_planning import merged_steps, needs_text

# --------------------------------------------------------------------------- #
# Pure: the decision, the merge, the prefetch text
# --------------------------------------------------------------------------- #


def test_parse_reads_a_decision_and_rejects_nonsense():
    assert parse({"decision": "replan"}).decision is None
    out = parse({"decision": "revise", "why": " I still  need stone. ", "goal": "", "target": "Iron Ore",
                 "steps": [{"keep": "", "do": "mine stone", "done_when": "gained 3 cobblestone"},
                           {"keep": "s4", "do": "", "done_when": ""}, {"keep": "", "do": "", "done_when": ""}],
                 "drop": [{"id": "s3", "why": "can't with wood"}], "rules": ["", "stay dry"],
                 "ask": "got any cobblestone?", "say": "on it"})
    assert (out.decision, out.why, out.target) == ("revise", "I still need stone.", "iron_ore")
    assert [(s.keep, s.do) for s in out.steps] == [(None, "mine stone"), ("s4", "")]
    assert out.drop == {"s3": "can't with wood"} and out.rules == ["stay dry"]
    assert out.ask == "got any cobblestone?" and out.say is None  # never both


def test_the_planning_prompt_names_no_game():
    assert "Minecraft" not in INSTRUCTIONS and "pickaxe" not in INSTRUCTIONS


def _steps(*specs):
    return [{"id": i, "do": f"do {i}", "done_when": f"{i} done", "state": state} for i, state in specs]


def test_merge_keeps_steps_by_id_inserts_new_ones_and_only_drops_what_it_names():
    current = _steps(("s1", "done"), ("s2", "active"), ("s3", "pending"), ("s4", "pending"))
    merged = merged_steps(current, [PlanStep(do="mine stone", done_when="gained 3 cobblestone"),
                                    PlanStep(keep="s4"), PlanStep(keep="s1"), PlanStep(keep="s4")],
                          drop={"s2": "can't"})
    # s1 is done (not open), s4 only once, s3 unmentioned so it stays at the end, s2 dropped.
    assert [s["do"] for s in merged] == ["mine stone", "do s4", "do s3"]
    assert merged_steps(current, [PlanStep(do="x")], {})[1:] == [
        {"do": "do s2", "done_when": "s2 done"}, {"do": "do s3", "done_when": "s3 done"},
        {"do": "do s4", "done_when": "s4 done"}]


def test_needs_text_says_the_chain_in_order():
    found = {"target": "iron_ore", "known": True, "kind": "block", "ready": False, "gaps": [], "steps": [
        {"action": "mine", "item": "cobblestone", "count": 3, "from": "stone", "tool": "wooden_pickaxe",
         "tool_needed": False},
        {"action": "craft", "item": "stone_pickaxe", "count": 1, "table": True},
        {"action": "mine", "item": "raw_iron", "count": 1, "from": "iron_ore", "tool": "stone_pickaxe",
         "tool_needed": True}]}
    text = needs_text(found)
    assert "What it takes to mine iron_ore" in text
    assert text.index("mine stone for cobblestone x3 (with her wooden_pickaxe)") < text.index(
        "craft stone_pickaxe x1 (at a crafting table)") < text.index("mine iron_ore for raw_iron x1 (needs a stone_pickaxe)")
    assert "nothing missing" in needs_text({**found, "ready": True})
    assert "white_bed" in needs_text({"target": "bed", "known": False, "did_you_mean": ["white_bed"]})


def test_a_rule_is_kept_whole_and_its_full_stop_does_not_make_it_another():
    """Seen live 2026-11-05: a rule cut at 120 characters was copied forward cut, and the planner's copy of an old
    rule with a full stop read as a new one, so the old one showed as carried by code and the copy as left out."""
    from app.services.desktop_game import _plan

    long_rule = ("When searching for iron ore, explore into a cave and search its exposed stone rather than "
                 "repeatedly seeking iron from the surface, and come back up before night")
    assert len(long_rule) > 120
    old = ["Follow the user deeper into the cave", long_rule]
    applied = game_planning._rules(old, ["follow the user deeper into the cave.", long_rule])
    assert applied == old  # one copy each, whole
    origins = game_planning._rule_origins(applied, proposed=["Follow the user deeper into the cave."],
                                          previous_plan=old)
    assert origins["carried_by_code"] == [long_rule]  # the planner did propose the first one
    assert origins["left_out_by_limit"] == []
    assert _plan("goal", None, applied).constraints == old


# --------------------------------------------------------------------------- #
# Real PostgreSQL: asking, routing, applying
# --------------------------------------------------------------------------- #

from app.ai.desktop import pack_client  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.models import AgentEventJob, DesktopSession, MindThought  # noqa: E402
from app.services import autonomous_speech, desktop_game, desktop_sessions, game_pack, mind, mind_minecraft  # noqa: E402
from tests.db_support import database  # noqa: E402,F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: E402,F401  (autouse fixture)
from tests.test_desktop_sessions import _user, alice, db  # noqa: E402,F401  (fixtures)
from tests.test_game_pack import FakePack  # noqa: E402

IRON = {"target": "iron_ore", "known": True, "kind": "block", "ready": False, "gaps": [], "steps": [
    {"action": "mine", "item": "cobblestone", "count": 3, "from": "stone", "tool": "wooden_pickaxe",
     "tool_needed": False}]}


class PlanningPack(FakePack):
    def __init__(self):
        super().__init__()
        self.asked: list[str] = []

    def needs(self, name, count=1):
        self.asked.append(name)
        return {**IRON, "target": name}


class ScriptedPlanner:
    """Answers with the next decision; ``during`` runs while it "thinks" (to change the world)."""

    def __init__(self, *decisions, during=None):
        self.decisions = list(decisions)
        self.during = during
        self.texts: list[str] = []

    def plan(self, text, *, user="the user", situation=None, max_steps=12):
        self.texts.append(text)
        if self.during:
            self.during()
        return self.decisions.pop(0)


@pytest.fixture
def planning(monkeypatch, database, alice):
    settings = get_settings().model_copy(update={
        "desktop_agent_enabled": True, "mind_enabled": True, "mind_planner_enabled": True,
        "minecraft_pack_owner_email": alice.email, "redis_url": ""})
    for module in (game_planning, mind, mind_minecraft, desktop_game, game_pack):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(game_planning, "SessionLocal", database)
    monkeypatch.setattr(mind, "SessionLocal", database, raising=False)
    monkeypatch.setattr(desktop_game, "_manual_cache", {"at": 0.0, "lines": []})
    monkeypatch.setattr("app.services.game_pack.stop_driver_skill", lambda client=None: False)
    monkeypatch.setattr(mind, "_log", lambda record: None)
    fake = PlanningPack()
    monkeypatch.setattr(pack_client.PackClient, "from_settings", classmethod(lambda cls: fake))
    said: list[dict] = []

    def deliver(db, *, user_id, text, reasons, commentary_mode, **proposal):
        said.append({"text": text, "reasons": reasons})
        return "Proposed."
    monkeypatch.setattr(autonomous_speech, "submit", deliver)
    fake.said = said
    return fake


def _session(db, alice, *, state="running", steps=None, goal="mine some iron") -> DesktopSession:
    env = game_pack.ensure_environment(db, alice.id)
    env.status = "online"
    session = DesktopSession(
        owner_user_id=alice.id, environment_id=env.id, profile_id="minecraft_pack", profile_revision=1,
        state=state, control_owner="gameplay" if state == "running" else "none", max_duration_seconds=3600,
        plan={"goal": goal, "target": "iron_ore", "source": "columbina_user_turn", "constraints": ["stay dry"],
              "if_stuck": None, "steps": steps or _steps(("s1", "done"), ("s2", "active"), ("s3", "pending"))},
        plan_revision=1, next_step_number=4, reason_code="driver_stuck" if state == "paused" else None)
    db.add(session)
    db.commit()
    return session


def _plan_jobs(db):
    return db.query(AgentEventJob).filter_by(source=game_planning.SOURCE).all()


def _run(db, session, planner):
    """As the worker does: claim the job (it's running, not waiting), then plan."""
    from app.services import agent_events

    job = agent_events.claim(db)
    assert job is not None and job.source == game_planning.SOURCE
    out = game_planning.run(session.owner_user_id, session.id, list(job.reasons), dict(job.payload), planner=planner)
    db.expire_all()
    return out


def test_a_goal_from_her_reply_waits_for_her_planner_and_her_summary_says_so(db, alice, planning):
    session = _session(db, alice)
    reply = desktop_game.set_goal(db, alice.id, goal="Mine some iron", target="Iron Ore", rules=["no swimming"])
    assert "working out how" in reply and session.plan_revision == 1  # nothing replaced yet
    [job] = _plan_jobs(db)
    assert job.payload["goal"]["text"] == "Mine some iron" and job.payload["goal"]["target"] == "iron_ore"
    summary = desktop_game.describe(db, alice.id)
    assert summary.planner is True
    assert 'You\'re working out how to do what the user asked: "Mine some iron"' in summary.summary


def test_her_hands_needing_the_user_is_just_stuck_and_goes_to_her_planner(db, alice, planning):
    session = _session(db, alice)
    assert desktop_sessions.driver_progress(db, session.id, plan_revision=1, step_id="s2", status="needs_human",
                                            note="the shore is water")
    db.refresh(session)
    assert (session.state, session.reason_code) == ("paused", "driver_stuck")  # hers to lift
    [job] = _plan_jobs(db)
    assert job.reasons == ["stuck: needs the user: the shore is water"]
    assert db.query(AgentEventJob).filter_by(source="desktop").count() == 0  # no background turn


def test_keep_keeps_the_plan_tries_again_and_is_her_thought(db, alice, planning):
    """The 14:57:50 moment: the plan was right; a turn with nothing to fix replaced it."""
    session = _session(db, alice, state="paused")
    game_planning.request(db, session, "struggling: mine stone failed 4 times")
    db.commit()
    planner = ScriptedPlanner(PlanDecision("keep", why="The plan still works: stone, then the stone pickaxe."))
    assert _run(db, session, planner).startswith("kept")
    db.refresh(session)
    assert session.plan_revision == 1 and session.state == "running"
    thought = db.query(MindThought).one()
    assert (thought.level, thought.kind, thought.text) == ("hard", "plan", "The plan still works: stone, then the stone pickaxe.")
    text = planner.texts[0]
    order = [text.index(h) for h in ("## Why you're planning now", "## The goal and the plan", "## What it takes",
                                       "## What is happening", "## What she can do")]
    assert order == sorted(order)
    assert "[s2] active: do s2" in text and "[s1] done" in text
    assert "mine stone for cobblestone x3" in text and planning.asked == ["iron_ore"]


def test_revise_merges_by_id_and_keeps_the_users_rules(db, alice, planning):
    session = _session(db, alice, state="paused")
    game_planning.request(db, session, "stuck: no tool that can harvest iron_ore")
    db.commit()
    decision = PlanDecision("revise", why="Iron needs a stone pickaxe first.", rules=["avoid lava"],
                            steps=[PlanStep(do="mine 3 stone", done_when="gained 3 cobblestone"), PlanStep(keep="s3")],
                            drop={"s2": "can't harvest it with wood"})
    assert _run(db, session, ScriptedPlanner(decision)).startswith("set (revised)")
    db.refresh(session)
    assert [s["do"] for s in session.plan["steps"]] == ["mine 3 stone", "do s3"]
    assert session.plan["goal"] == "mine some iron" and session.plan["source"] == "columbina_mind"
    assert session.plan["constraints"] == ["stay dry", "avoid lava"] and session.plan["target"] == "iron_ore"
    assert session.state == "running"


def test_a_revision_into_the_users_new_goal_leaves_the_old_goals_rules_behind(db, alice, planning):
    """Seen live 2026-11-05: "make a bed, you don't have to follow me" while the plan was still the iron one;
    her planner revised it into the bed and "Follow the user deeper into the cave" came along for 11 minutes."""
    from app.services import memory_manager

    session = _session(db, alice, state="paused")
    session.plan = {**session.plan, "constraints": ["Follow the user deeper into the cave", "stay dry"]}
    memory_manager.on_plan_set(db, session, goal_text="mine some iron", source="columbina_user_turn")
    db.commit()
    iron = session.plan["made_for_goal"]
    bed = memory_manager.begin_goal(db, session, "Make a bed", by_user=True)
    db.commit()
    db.refresh(session)
    assert session.plan["goal_id"] == str(bed.id) and session.plan["made_for_goal"] == iron
    assert game_planning.made_for_other_goal(session.plan)
    assert "came with a plan made for an earlier goal" in "\n".join(game_planning.plan_lines(session.plan))

    game_planning.request(db, session, "stuck: no cobblestone for the furnace")
    db.commit()
    decision = PlanDecision("revise", why="The bed needs wool and planks.", goal="gather wool, then craft a bed",
                            rules=["stay dry."], steps=[PlanStep(do="get 3 wool", done_when="gained 3 wool")])
    assert _run(db, session, ScriptedPlanner(decision)).startswith("set (revised)")
    db.refresh(session)
    assert session.plan["constraints"] == ["stay dry."]  # listed again: kept; the cave rule ended with iron
    assert session.plan["made_for_goal"] == str(bed.id) and not game_planning.made_for_other_goal(session.plan)


def test_when_rules_overflow_the_planners_win_over_the_old_plans():
    previous = [f"old rule {n}" for n in range(5)]
    applied = game_planning._revised_rules([], previous, [], ["new rule"])
    assert len(applied) == 5 and applied[-1] == "new rule" and "old rule 4" not in applied
    assert applied[:4] == previous[:4]  # in the plan's order


def test_a_users_goal_is_set_as_theirs_with_its_target_and_rules(db, alice, planning):
    session = _session(db, alice)
    desktop_game.set_goal(db, alice.id, goal="build a shelter", target="oak_planks", rules=["stay close"])
    decision = PlanDecision("new_goal", why="A shelter first: planks, then walls.", goal="build a shelter",
                            steps=[PlanStep(do="mine 4 oak_log", done_when="gained 4 oak_log")])
    assert _run(db, session, ScriptedPlanner(decision)).startswith("set (new goal)")
    db.refresh(session)
    plan = session.plan
    assert (plan["goal"], plan["target"], plan["source"]) == ("build a shelter", "oak_planks", "columbina_user_turn")
    assert plan["constraints"] == ["stay close"]


def test_an_answer_about_a_plan_that_changed_meanwhile_is_dropped(db, alice, planning, database):
    session = _session(db, alice)
    desktop_game.set_goal(db, alice.id, goal="mine some iron")

    def user_says_follow_me():
        with database() as other:
            desktop_game.do_now(other, alice.id, do="follow tearDrop12", done_when="the user says to stop")
    decision = PlanDecision("new_goal", why="Iron.", goal="mine iron", steps=[PlanStep(do="find iron")])
    assert _run(db, session, ScriptedPlanner(decision, during=user_says_follow_me)).startswith("dropped")
    db.refresh(session)
    assert session.plan["goal"] == "follow tearDrop12"
    assert db.query(MindThought).one().status == "stale"


def test_a_command_done_at_once_drops_a_goal_still_waiting(db, alice, planning):
    session = _session(db, alice)
    desktop_game.set_goal(db, alice.id, goal="mine some iron")
    desktop_game.do_now(db, alice.id, do="follow tearDrop12", done_when="the user says to stop")
    db.expire_all()
    assert [job.status for job in _plan_jobs(db)] == ["done"]
    assert game_planning.planning_now(db, session.id) is None


def test_wait_for_user_pauses_and_asks_in_any_mode(db, alice, planning):
    session = _session(db, alice)
    game_planning.request(db, session, "stuck: no land route north")
    db.commit()
    decision = PlanDecision("wait_for_user", why="Only Vincent knows a way round.", ask="Is there a way around the water?")
    assert _run(db, session, ScriptedPlanner(decision)) == "paused to wait for the user"
    db.refresh(session)
    assert (session.state, session.reason_code) == ("paused", "columbina_pause")
    assert planning.said == [{"text": "Is there a way around the water?", "reasons": ["ask: Only Vincent knows a way round."]}]


def test_a_failed_planning_call_raises_so_the_job_is_retried(db, alice, planning):
    session = _session(db, alice)
    game_planning.request(db, session, "plan_low")
    db.commit()
    with pytest.raises(RuntimeError, match="planning failed"):
        _run(db, session, ScriptedPlanner(PlanDecision(None, error="timeout")))
    db.refresh(session)
    assert session.plan_revision == 1


def test_shared_tentative_lesson_changes_a_later_plan_and_records_the_exact_revision(db, alice, planning):
    import uuid
    from datetime import datetime, timezone
    from app.ai.learning import lessons as L
    from app.services import memory_lessons as ML
    from app.db.models import MemoryLessonUse
    session = _session(db, alice, state="paused")
    c = L.Change("create", None, None, None, "strategy", "situation",
                 "Inspect the descent before committing; damage cause remains unknown",
                 "minecraft descending into an uninspected shaft", [], [], ["experience:991"], None,
                 "one costly outcome")
    ML.apply(db, alice.id, [c], ML.Source("notice:991", datetime.now(timezone.utc), None, None),
             embed=lambda _: None)
    game_planning.request(db, session, "stuck: reconsider the descent")
    db.commit()

    class Planner(ScriptedPlanner):
        def plan(self, text, **kw):
            assert "damage cause remains unknown" in text and "Status: hypothesis" in text
            return super().plan(text, **kw)
    planner = Planner(PlanDecision("revise", why="Inspect before descent because the previous cost is unresolved",
                                   steps=[PlanStep(do="inspect the shaft from above", done_when="shaft inspected")],
                                   drop={"s2": "inspect first", "s3": "inspect first"}))
    assert _run(db, session, planner).startswith("set (revised)")
    db.refresh(session)
    assert session.plan["steps"][0]["do"] == "inspect the shaft from above"
    use = db.query(MemoryLessonUse).filter_by(consumer="planner").one()
    assert use.received and use.revision == 1 and use.reported is None
