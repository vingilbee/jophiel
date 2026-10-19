"""Hard thinking (app/ai/mind/hard.py, services/mind_hard.py): a conclusion, when it's dropped,
where it goes. No network, no database (a fake Redis from tests/desktop_support)."""
import json
import uuid
from types import SimpleNamespace

from app.ai.mind.hard import HardInput, HardThought, Problem, parse, render
from app.services import mind, mind_hard
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)

SID = uuid.uuid4()
UID = uuid.uuid4()
PLAN = {"goal": "kill a sheep", "source": "columbina_user_turn",
        "steps": [{"id": "s60", "state": "active", "do": "attack the sheep", "done_when": "the sheep is dead"}]}


def session(**over):
    base = dict(id=SID, owner_user_id=UID, state="running", plan=PLAN, commentary_mode="occasional")
    return SimpleNamespace(**{**base, **over})


def failing_problem():
    return Problem("failing", "Step: attack the sheep. 4 failed.", topic="keeps failing: attack the sheep",
                   query="attack the sheep", anchor={"goal": "kill a sheep", "step_id": "s60"})


def test_a_conclusion_needs_a_kind_and_words_and_tips_only_where_they_fit():
    assert parse({"kind": "", "conclusion": "nothing", "thinking": "hm"}).kind is None
    assert parse({"kind": "decision", "conclusion": "  "}).kind is None
    feeling = parse({"kind": "feeling", "conclusion": "He's teaching me today.", "for_hands": "go", "question": "?"})
    assert (feeling.for_hands, feeling.question) == (None, None)
    guess = parse({"kind": "guess", "conclusion": "The move kills them.", "for_hands": "count the mutton",
                   "thinking": "it said failed\\nbut mutton went up"})
    assert guess.for_hands == "count the mutton" and guess.thinking == "it said failed\\nbut mutton went up"
    question = parse({"kind": "question", "conclusion": "I wonder if he wants me on top.", "question": ""})
    assert question.question == "I wonder if he wants me on top."  # the conclusion, when no words were given


def test_what_she_reads_is_one_text_in_a_fixed_order():
    text = render(HardInput(
        problem=failing_problem(), user="Vincent", now="Health 10 of 10 hearts.", goal="kill a sheep",
        conversation=({"role": "user", "content": "try to kill it", "ago": 30}, {"role": "assistant",
                                                                                   "content": "On it!", "ago": 28}),
        memories="- Vincent likes playing at night", learned=("When a mob is passive: use the learned move",),
        earlier=("Fighting won't work on sheep.",), character=("Honesty.", "Curiosity and understanding.")))
    order = ["The problem (failing):", "Your goal: kill a sheep", "Right now:", "The conversation",
             "Vincent (30 s ago): try to kill it", "you (28 s ago): On it!", "Memories that may bear on it:",
             "What you learned before:", "Your earlier thoughts on this", "What you care about: Honesty.;"]
    positions = [text.index(part) for part in order]
    assert positions == sorted(positions)


def test_a_thought_about_a_step_or_goal_that_is_gone_is_dropped():
    problem = failing_problem()
    assert mind_hard.dropped(None, session(), problem, None) is None
    assert mind_hard.dropped(None, session(), problem, "the goal changed") == "the goal changed"
    assert mind_hard.dropped(None, session(state="ended"), problem, None) == "the game ended"
    over = {**PLAN, "steps": [{**PLAN["steps"][0], "state": "done"}]}
    assert mind_hard.dropped(None, session(plan=over), problem, None) == "that step is over"
    assert mind_hard.dropped(None, session(plan={**PLAN, "goal": "make a bed"}), problem, None) == "the goal changed"
    quiet = Problem("quiet", "Nothing is happening.", topic="quiet: x", query="x")
    assert mind_hard.dropped(None, session(plan={**PLAN, "goal": "make a bed"}), quiet, None) is None


def test_a_goal_the_user_changes_cancels_the_running_thought_and_the_waiting_one(monkeypatch):
    monkeypatch.setattr(mind_hard.desktop_commands, "load_driver_state", lambda sid: {})
    st = mind_hard.HardState(started=True, running=failing_problem(), waiting=failing_problem())
    mind_hard.step(session(plan={**PLAN, "goal": "make a bed"}), st, state={}, plan={**PLAN, "goal": "make a bed"},
                   talk=[], fresh=[], gate_hard=False, pressure="calm", at=100.0, user="Vincent")
    assert st.cancelled == "the goal changed" and st.waiting is None


class Db:
    def __init__(self, sess):
        self.sess = sess

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, model, key):
        return self.sess

    def commit(self):
        pass


def _run(monkeypatch, thought: HardThought, sess=None, cancelled=None):
    sess = sess or session()
    saved, sent = [], []
    monkeypatch.setattr(mind_hard, "SessionLocal", lambda: Db(sess))
    monkeypatch.setattr(mind_hard, "gather", lambda db, s, problem: (SimpleNamespace(), None, "Vincent"))
    monkeypatch.setattr(mind_hard, "render", lambda inp: "input")
    monkeypatch.setattr(mind_hard, "_log_hard", lambda *a: None)
    monkeypatch.setattr(mind_hard, "thinker", lambda: SimpleNamespace(
        think=lambda inp, situation: thought, instructions=lambda trigger, user, situation: "system"))
    monkeypatch.setattr(mind, "save_thought", lambda db, **kw: saved.append(kw) or SimpleNamespace(id=uuid.uuid4()))
    monkeypatch.setattr(mind_hard, "send_on", lambda s, tid, problem, t: sent.append(t.kind))
    st = mind_hard.HardState(running=failing_problem(), cancelled=cancelled)
    mind_hard.run(SID, UID, st, failing_problem(), 100.0)
    assert st.running is None
    return saved, sent


def test_a_landed_thought_is_saved_and_sent_on_unless_dropped(monkeypatch):
    guess = HardThought("guess", "The move kills them; count the mutton.", thinking="it said failed",
                        for_hands="check the mutton, not the result")
    saved, sent = _run(monkeypatch, guess)
    expected = {"level": "hard", "status": "done", "trigger": "failing", "conclusion_kind": "guess",
                "for_hands": "check the mutton, not the result", "dropped_reason": None}
    assert {key: saved[0][key] for key in expected} == expected
    assert sent == ["guess"]
    over = session(plan={**PLAN, "steps": [{**PLAN["steps"][0], "state": "done"}]})
    saved, sent = _run(monkeypatch, guess, sess=over)
    assert (saved[0]["status"], saved[0]["dropped_reason"], sent) == ("dropped", "that step is over", [])
    saved, sent = _run(monkeypatch, HardThought(None, None, thinking="nothing to conclude"))
    assert saved == [] and sent == []


def test_tips_intentions_and_questions_go_where_they_belong(isolated_redis):  # noqa: F811
    sess = session()
    mind_hard.send_on(sess, uuid.uuid4(), failing_problem(), HardThought("decision", "Use the learned move.",
                                                                         for_hands="use learned_attack on sheep"))
    mind_hard.send_on(sess, uuid.uuid4(), failing_problem(), HardThought("intention", "Keep 5 planks to get down.",
                                                                         for_hands="keep 5 planks"))
    texts = [m["text"] for m in mind_hard.hands_messages(SID)]
    assert texts == ["(from your own thinking) use learned_attack on sheep",
                     "keep in mind (your own intention): keep 5 planks"]
    assert [i["text"] for i in mind_hard.intentions(SID)] == ["keep 5 planks"]
    mind_hard.send_on(sess, uuid.uuid4(), failing_problem(), HardThought("question", "Does he want me up there?",
                                                                         question="Want me on top, or just the move?"))
    question = mind_hard.take_question(SID)
    assert question["text"] == "Want me on top, or just the move?" and question["goal"] == "kill a sheep"
    assert mind_hard.take_question(SID) is None  # her reply took it: never also asked at a pause


def test_the_question_is_asked_at_a_natural_pause(isolated_redis, monkeypatch):  # noqa: F811
    said, spoke = [], {"recently": True}
    monkeypatch.setattr(mind_hard.agent_say, "user_spoke_since", lambda db, uid, at: spoke["recently"])
    monkeypatch.setattr(mind_hard, "_reply_in_flight", lambda db, uid: False)
    monkeypatch.setattr(mind_hard.agent_say, "deliver", lambda db, **kw: said.append(kw) or "Said it.")
    mind_hard.set_question(SID, {"text": "Want me on top?", "at": 1.0, "goal": "kill a sheep", "topic": "t"})
    assert mind_hard.ask_if_due(Db(None), session(), 100.0) is None and said == []  # they just spoke
    spoke["recently"] = False
    assert mind_hard.ask_if_due(Db(None), session(), 110.0) == "asked: Said it."
    assert said[0]["text"] == "Want me on top?" and said[0]["reasons"][0].startswith("question:")
    assert mind_hard.ask_if_due(Db(None), session(), 120.0) is None  # asked once
    mind_hard.set_question(SID, {"text": "Still?", "at": 1.0, "goal": "an old goal"})
    assert mind_hard.ask_if_due(Db(None), session(), 130.0) == "question dropped: the goal changed"


def test_her_mouth_hands_a_problem_to_her_slow_thinking(isolated_redis, monkeypatch):  # noqa: F811
    monkeypatch.setattr(mind, "enabled", lambda: True)
    monkeypatch.setattr(mind_hard.agent_say, "user_spoke_since", lambda db, uid, at: False)
    from app.services import desktop_game

    monkeypatch.setattr(desktop_game, "_live", lambda db, uid: session())
    answer = mind_hard.hand_off(None, UID, what="what harder skills he means")
    assert answer.startswith("You'll think it over in the background")
    problem = mind_hard.handoff(SID, "Vincent")
    assert problem.trigger == "handoff" and "what harder skills he means" in problem.text
    assert problem.anchor["goal"] == "kill a sheep"
    assert mind_hard.handoff(SID, "Vincent") is None
    # a hand-off wins the waiting slot over anything else
    assert mind_hard._better(problem, failing_problem()) is problem
    assert mind_hard._better(failing_problem(), problem) is problem


def test_hard_thought_logs_are_replayable(tmp_path, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "mind_hard_log_path", str(tmp_path / "hard.jsonl"))
    mind_hard._log_hard(SID, failing_problem(), "system text", "input text",
                        HardThought("decision", "Go around.", latency_ms=1200))
    record = json.loads((tmp_path / "hard.jsonl").read_text())
    assert (record["system"], record["input"], record["kind"], record["trigger"]) == (
        "system text", "input text", "decision", "failing")
