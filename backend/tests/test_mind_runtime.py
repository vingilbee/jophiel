"""The general mind's attention and lifecycle (services/mind_hard.py, mind_conversation.py;
docs/COLUMBINA_INNER_STATE_PLAN.md): what replaying ticks can't show. No network, no database."""
import json
import uuid
from types import SimpleNamespace

import pytest

from app.ai.mind.contracts import Moment, Rhythm, Scope
from app.ai.mind.hard import HardThought, Problem
from app.ai.mind.situation import GENERAL
from app.core.config import get_settings
from app.services import mind, mind_conversation, mind_hard, mind_minecraft

UID = uuid.uuid4()


def problem(trigger="failing", topic=None, **anchor):
    return Problem(trigger, f"{trigger} problem", topic=topic or f"{trigger}: x", query="x", anchor=anchor)


class FakeSource:
    """The smallest source: problems are handed in, nothing in the world."""

    rhythm = Rhythm("poll")

    def __init__(self, name, *, shadow=False, gone=None):
        self.name, self.shadow, self.gone = name, shadow, gone
        self.found: list[Problem] = []

    def new_state(self):
        return None

    def problems(self, scope, state, moment, *, gate_hard):
        found, self.found = self.found, []
        return found

    def drop_waiting(self, scope, moment, problem):
        return None

    def quiet_topics(self, user):
        return ()

    def dropped(self, db, scope, problem, cancelled):
        return cancelled or self.gone

    def outdated(self, db, scope, problem, token):
        return []

    def gather(self, db, scope, problem, base):
        return base, None

    def situation(self):
        return GENERAL

    def current_goal(self, db, scope):
        return None

    def body(self, scope):
        return None

    def thought_session(self, scope):
        return None


@pytest.fixture
def started(monkeypatch):
    """Hard thoughts that start are recorded instead of run."""
    mind_hard.reset()
    log = []

    def start(source, scope, att, problem, at):
        att.running, att.running_scope, att.last_started_at = problem, scope, at
        att.served[source.name] = at
        log.append((source.name, problem.trigger))

    monkeypatch.setattr(mind_hard, "start", start)
    monkeypatch.setattr(mind_hard, "handoff", lambda key, user: None)
    monkeypatch.setattr(get_settings(), "mind_hard_gap_seconds", 20.0)
    return log


def tick(source, scope, at, pressure="calm"):
    return mind_hard.step(source, scope, mind_hard.ActivityHard(started=True),
                          Moment("t", pressure, ["something"]), gate_hard=False, at=at, user="Vincent")


def test_two_sources_share_one_attention_priority_first_then_fairness(started):
    game, chat = FakeSource("game"), FakeSource("chat")
    g, c = Scope(UID, "game", uuid.uuid4()), Scope(UID, "chat", uuid.uuid4())
    game.found = [problem("quiet")]
    chat.found = [problem("failing")]
    tick(game, g, 100.0)  # the quiet one starts: nothing else is waiting yet
    assert started == [("game", "quiet")]
    assert tick(chat, c, 101.0) == "hard waiting (failing)"  # one at a time, per user
    mind_hard.attention_for(UID).running = None
    game.found = [problem("big")]
    assert tick(game, g, 125.0) == "hard: failing"  # failing outranks big
    mind_hard.attention_for(UID).running = None
    chat.found = [problem("big")]
    tick(chat, c, 150.0)  # both have a "big" waiting now: the source served least recently goes first
    assert started[-1] == ("game", "big")


def test_a_noisy_source_only_replaces_its_own_waiting_problem(started):
    noisy, calm = FakeSource("noisy"), FakeSource("calm")
    n, c = Scope(UID, "noisy", uuid.uuid4()), Scope(UID, "calm", uuid.uuid4())
    noisy.found = [problem("big")]
    tick(noisy, n, 100.0)
    calm.found = [problem("big", topic="calm: y")]
    tick(calm, c, 101.0)
    for at in range(102, 110):
        noisy.found = [problem("big", topic=f"big: {at}")]
        tick(noisy, n, float(at))
    att = mind_hard.attention_for(UID)
    assert set(att.waiting) == {n.key, c.key}  # one slot each, however much the noisy one finds
    att.running = None
    assert tick(calm, c, 130.0) == "hard: big" and started[-1] == ("calm", "big")


def test_urgent_pressure_in_one_activity_holds_hard_thinking_for_all(started):
    game, chat = FakeSource("game"), FakeSource("chat")
    g, c = Scope(UID, "game", uuid.uuid4()), Scope(UID, "chat", uuid.uuid4())
    tick(game, g, 100.0, pressure="urgent")
    chat.found = [problem("failing")]
    assert tick(chat, c, 101.0) == "hard postponed (failing): urgent"
    tick(game, g, 102.0, pressure="calm")
    assert started == [("chat", "failing")]


def test_a_duplicate_event_is_one_thought(started):
    chat = FakeSource("chat")
    c = Scope(UID, "chat", uuid.uuid4())
    assert mind_hard.offer(chat, c, problem("open", event="e1"), 100.0) == "hard: open"
    mind_hard.attention_for(UID).running = None
    assert mind_hard.offer(chat, c, problem("open", event="e1"), 200.0) == "hard: duplicate event"
    assert started == [("chat", "open")]


def test_an_activity_that_ends_loses_its_waiting_problem(started):
    game = FakeSource("game")
    g = Scope(UID, "game", uuid.uuid4())
    game.found = [problem("quiet")]
    tick(game, g, 100.0)
    game.found = [problem("big")]
    tick(game, g, 101.0)
    mind._activity_source[g.activity] = "game"
    mind._minds[g.activity] = mind.ActivityMind()
    mind._forget_gone(game, set())
    assert mind_hard.attention_for(UID).waiting == {}


class Db:
    def __init__(self, sess=None):
        self.sess = sess

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, model, key):
        return self.sess

    def commit(self):
        pass


@pytest.fixture
def thinking(monkeypatch, tmp_path):
    """A real run() with a fake thinker and database; returns what was saved and logged."""
    saved, said = [], []
    monkeypatch.setattr(get_settings(), "mind_shadow_log_path", str(tmp_path / "shadow.jsonl"))
    monkeypatch.setattr(get_settings(), "mind_hard_log_path", str(tmp_path / "hard.jsonl"))
    monkeypatch.setattr(mind, "save_thought", lambda db, **kw: saved.append(kw) or SimpleNamespace(id=uuid.uuid4()))
    monkeypatch.setattr(mind, "_set_thinking", lambda key, topic: None)
    monkeypatch.setattr(mind.agent_say, "deliver", lambda db, **kw: said.append(kw) or "Said it.")
    monkeypatch.setattr(mind_hard, "conversation", lambda db, uid, now, cid=None: ())
    monkeypatch.setattr(mind_hard, "_character", lambda db: ())
    monkeypatch.setattr(mind_hard, "_earlier", lambda db, sid, topic, now: ())
    from app.services import long_term_memory

    monkeypatch.setattr(long_term_memory, "retrieve_for_prompt", lambda uid, query: None)

    def think(answer):
        monkeypatch.setattr(mind_hard, "thinker", lambda: SimpleNamespace(
            think=lambda inp, situation: answer, instructions=lambda trigger, user, situation: "system"))
    return SimpleNamespace(saved=saved, said=said, think=think, shadow=tmp_path / "shadow.jsonl")


def test_a_game_that_ends_while_she_thinks_drops_the_thought(monkeypatch, thinking):
    sid = uuid.uuid4()
    ended = SimpleNamespace(id=sid, owner_user_id=UID, state="ended", plan=None, commentary_mode="occasional",
                            name="Vincent")
    monkeypatch.setattr(mind_hard, "SessionLocal", lambda: Db(ended))
    monkeypatch.setattr(mind_minecraft.SOURCE, "gather", lambda db, scope, problem, base: (base, None))
    thinking.think(HardThought("decision", "Go around.", for_hands="go around"))
    att = mind_hard.Attention()
    mind_hard.run(mind_minecraft.SOURCE, Scope(UID, "minecraft", sid), att, problem("quiet"), 100.0)
    assert (thinking.saved[0]["status"], thinking.saved[0]["dropped_reason"]) == ("dropped", "the game ended")
    assert att.running is None


def test_a_cancelled_thought_is_dropped_with_why_and_never_sent_on(monkeypatch, thinking):
    source = FakeSource("game")
    monkeypatch.setattr(mind_hard, "SessionLocal", lambda: Db())
    routed = []
    monkeypatch.setattr(mind_hard, "route", lambda *a, **k: routed.append(a) or {})
    thinking.think(HardThought("decision", "Go around.", for_hands="go around"))
    att = mind_hard.Attention(cancelled="the goal changed")
    mind_hard.run(source, Scope(UID, "game", uuid.uuid4()), att, problem("failing"), 100.0)
    assert thinking.saved[0]["dropped_reason"] == "the goal changed" and routed == []


def test_a_body_that_cannot_take_a_tip_says_so(monkeypatch):
    from app.services import autonomous_speech
    monkeypatch.setattr(FakeSource, "speaking", lambda *a: {}, raising=False)
    monkeypatch.setattr(autonomous_speech, "submit", lambda *a, **k: None)
    monkeypatch.setattr(mind_hard, "SessionLocal", lambda: Db())
    routed = mind_hard.route(FakeSource("chat"), Scope(UID, "chat", uuid.uuid4()), uuid.uuid4(), problem(),
                             HardThought("decision", "Ask first.", for_hands="ask first"), goal=None)
    assert routed["tip"].status == "unsupported"


# --------------------------------------------------------------------------- #
# The conversation source, in shadow
# --------------------------------------------------------------------------- #

NOTES = [{"id": "n1", "kind": "her_offer", "text": "want me to pick one?"},
         {"id": "n2", "kind": "their_open_question", "text": "what would you watch tonight?"}]


def test_an_open_question_of_theirs_is_a_problem_worth_revisiting():
    cid = uuid.uuid4()
    found = mind_conversation.open_problem(cid, NOTES)
    assert found.trigger == "open" and "what would you watch tonight?" in found.text
    assert found.anchor == {"note": "n2", "event": f"{cid}:n2"}
    assert mind_conversation.open_problem(cid, NOTES[:1]) is None


def test_conversation_shadow_is_off_by_default(monkeypatch):
    monkeypatch.setattr(get_settings(), "mind_conversation_shadow_enabled", False)
    assert mind_conversation.observe(user_id=UID, conversation_id=uuid.uuid4(), notes=NOTES) is None


def test_conversation_shadow_thinks_with_no_game_and_touches_nothing_live(monkeypatch, thinking):
    """No DesktopSession: a thought from a message, only in the shadow log; live attention,
    thoughts, mouth and body untouched."""
    mind_hard.reset()
    monkeypatch.setattr(get_settings(), "mind_conversation_shadow_enabled", True)
    monkeypatch.setattr(mind_hard, "SessionLocal", lambda: Db(SimpleNamespace(name="Vincent")))
    monkeypatch.setattr(mind_hard._shadow_pool, "submit", lambda fn, *args: fn(*args))
    monkeypatch.setattr(mind_hard._pool, "submit", lambda *a: pytest.fail("a shadow thought used the live pool"))
    from app.services import cognitive_live

    monkeypatch.setattr(cognitive_live, "load_state", lambda uid, cid: {"notes": NOTES})
    thinking.think(HardThought("question", "I should just pick one for them.", thinking="they want a pick",
                               question="Want me to just pick?"))
    cid = uuid.uuid4()
    assert mind_conversation.observe(user_id=UID, conversation_id=cid, notes=NOTES) == "hard: open"
    record = json.loads(thinking.shadow.read_text().splitlines()[-1])
    assert (record["source"], record["activity"], record["trigger"], record["status"]) == (
        "conversation", str(cid), "open", "done")
    assert record["question"] == "Want me to just pick?"
    assert thinking.saved == [] and thinking.said == []  # never saved, never said
    assert mind_hard.take_question(str(cid)) is None  # no question left for her mouth
    assert (UID, False) not in mind_hard._attention  # live attention never touched
    # The same note again (her next reply left it open too): not a second thought.
    assert mind_conversation.observe(user_id=UID, conversation_id=cid, notes=NOTES) == "hard: duplicate event"


def test_a_shadow_thought_about_a_question_answered_meanwhile_is_dropped(monkeypatch, thinking):
    mind_hard.reset()
    monkeypatch.setattr(get_settings(), "mind_conversation_shadow_enabled", True)
    monkeypatch.setattr(mind_hard, "SessionLocal", lambda: Db(SimpleNamespace(name="Vincent")))
    monkeypatch.setattr(mind_hard._shadow_pool, "submit", lambda fn, *args: fn(*args))
    from app.services import cognitive_live

    monkeypatch.setattr(cognitive_live, "load_state", lambda uid, cid: {"notes": NOTES[:1]})  # n2 closed
    thinking.think(HardThought("decision", "Pick a film.", for_hands=""))
    mind_conversation.observe(user_id=UID, conversation_id=uuid.uuid4(), notes=NOTES)
    record = json.loads(thinking.shadow.read_text().splitlines()[-1])
    assert (record["status"], record["dropped_reason"]) == ("dropped", "it's been answered")


# --------------------------------------------------------------------------- #
# Review findings (2026-10-19): desktop mode, starvation, event work waking itself
# --------------------------------------------------------------------------- #

def test_the_general_loop_runs_without_desktop_mode_and_minecraft_needs_it(monkeypatch):
    monkeypatch.setattr(get_settings(), "mind_enabled", True)
    monkeypatch.setattr(get_settings(), "desktop_agent_enabled", False)
    polled = []

    class Screen(FakeSource):
        def enabled(self):
            return True

        def poll(self, db):
            polled.append(True)
            return []

    monkeypatch.setattr(mind, "sources", lambda: [Screen("screen")])
    monkeypatch.setattr(mind, "SessionLocal", lambda: Db())
    monkeypatch.setattr(mind, "_last_prune", 10**12)
    mind.run_once()
    assert polled == [True]  # a non-game source is polled with desktop mode off
    assert not mind_minecraft.enabled() and not mind_minecraft.SOURCE.enabled()


def test_a_source_that_keeps_finding_urgent_problems_cannot_starve_another(started):
    """Reproduces the review: a game finding a new failing problem every round kept winning
    while a conversation problem waited. Waiting improves priority, so it gets its turn."""
    game, chat = FakeSource("game"), FakeSource("chat")
    g, c = Scope(UID, "game", uuid.uuid4()), Scope(UID, "chat", uuid.uuid4())
    game.found = [problem("failing")]
    tick(game, g, 100.0)
    mind_hard.offer(chat, c, problem("big", topic="chat: y"), 100.0)
    at = 100.0
    for _ in range(6):
        mind_hard.attention_for(UID).running = None
        at += 25.0
        game.found = [problem("failing", topic=f"failing: {at}")]
        tick(game, g, at)
    picks = [name for name, _ in started]
    assert "chat" in picks and picks.index("chat") <= 5


def test_live_event_work_wakes_itself_after_a_gap_and_after_a_thought(started, monkeypatch):
    """No polled source is running: a live (not shadow) event source's waiting problem still starts."""
    import time as clock

    class Chat(FakeSource):
        rhythm = Rhythm("event")

    chat = Chat("chat")
    c = Scope(UID, "chat", uuid.uuid4())
    att = mind_hard.attention_for(UID)
    att.last_started_at = clock.time() - 19.8  # the gap ends in 0.2 s
    assert mind_hard.offer(chat, c, problem("open", topic="open: 1"), clock.time()) == "hard waiting (open): gap"
    for _ in range(50):
        if started:
            break
        clock.sleep(0.05)
    assert started == [("chat", "open")]  # the gap timer woke it
    mind_hard.offer(chat, Scope(UID, "chat", uuid.uuid4()), problem("open", topic="open: 2"), clock.time())
    assert len(started) == 1  # waiting behind the running one
    monkeypatch.setattr(get_settings(), "mind_hard_gap_seconds", 0.0)
    monkeypatch.setattr(mind_hard, "SessionLocal", lambda: Db(SimpleNamespace(name="Vincent")))
    monkeypatch.setattr(mind_hard, "gather", lambda db, source, scope, problem: (None, None, "Vincent"))
    mind_hard.run(chat, c, att, att.running, clock.time())  # the running thought lands
    assert started[-1] == ("chat", "open") and len(started) == 2  # and the next one starts
