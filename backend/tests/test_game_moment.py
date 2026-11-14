"""What changed in her game since a moment (services/game_moment.py), and what uses it:
her tool results, her quick thoughts, background turns and her corrections. No network,
no database."""
import asyncio
import uuid
from types import SimpleNamespace

from app.ai.context import GameEvent
from app.ai.tools.desktop import tools as desktop_tools
from app.ai.mind.contracts import Scope
from app.services import game_correction, game_moment, mind, mind_minecraft

SID = uuid.uuid4()


def session(**over):
    plan = {"goal": "reach the top of the ice pillar",
            "steps": [{"id": "s1", "state": "active", "do": "climb the pillar"},
                      {"id": "s2", "state": "pending", "do": "wave"}]}
    base = dict(id=SID, state="running", reason_code=None, plan_revision=4, plan=plan, current_step_id="s1")
    return SimpleNamespace(**{**base, **over})


def state(y=63, health=20, **items):
    return {"position": {"x": 0, "y": y, "z": 0}, "health": health,
            "inventory": [{"name": name, "count": count} for name, count in items.items()]}


def moment(sess=None, st=None, lesson=None, at=100.0):
    m = game_moment.capture(None, sess or session(), st if st is not None else state(birch_log=8), at=at)
    return game_moment.Moment(**{**m.__dict__, "lesson": lesson})


def test_nothing_changed_says_nothing():
    assert game_moment.changes(moment(), moment()) == []
    assert game_moment.changes(None, moment()) == []
    assert game_moment.changes(moment(), None) == []


def test_crafting_planks_is_counted():
    before = moment(st=state(birch_log=8, birch_planks=1))
    after = moment(st=state(birch_planks=33))
    assert game_moment.changes(before, after) == ["what you carry: birch_planks 1 -> 33, birch_log 8 -> 0"]


def test_paused_and_playing_again():
    paused = moment(session(state="paused", reason_code="user_pause"))
    running = moment()
    assert game_moment.changes(paused, running) == ["you're playing again (you were paused)"]
    assert game_moment.changes(running, paused)[0].startswith("you're paused now (")


def test_a_lesson_that_finished_names_the_new_skill():
    learning = moment(lesson=("a1", "build up by jumping", "running"))
    learned = moment(lesson=("a1", "build up by jumping", "learned"))
    (line,) = game_moment.changes(learning, learned)
    assert line.startswith('you learned how to "build up by jumping": your hands have it now as learned_')
    assert game_moment.changes(moment(), moment(lesson=("a2", "kill a sheep", "running"))) == [
        'you started learning "kill a sheep"']


def test_goal_and_step_changes():
    other = session(plan_revision=5, plan={"goal": "make a bed", "steps": []}, current_step_id=None)
    assert game_moment.changes(moment(), moment(other)) == ['your goal changed to "make a bed"']
    done = session(current_step_id="s2", plan={**session().plan, "steps": [
        {"id": "s1", "state": "done", "do": "climb the pillar"}, {"id": "s2", "state": "active", "do": "wave"}]})
    assert game_moment.changes(moment(), moment(done)) == ['the step "climb the pillar" is done; now on "wave"']


def test_height_and_hurt_only_when_they_matter():
    assert game_moment.changes(moment(st=state(y=63)), moment(st=state(y=65))) == []
    assert game_moment.changes(moment(st=state(y=63)), moment(st=state(y=95))) == [
        "you went up 32 blocks (height 63 -> 95)"]
    assert game_moment.changes(moment(st=state(health=20)), moment(st=state(health=14))) == [
        "you got hurt: 7 of 10 hearts now (was 10)"]


def test_kinds_filter_and_unknown_inventory():
    before = moment(st=state(birch_log=8))
    after = moment(session(plan_revision=5, plan={"goal": "make a bed", "steps": []}), st=state(birch_log=7))
    assert game_moment.changes(before, after, game_moment.WORLD) == ["what you carry: birch_log 8 -> 7"]
    no_state = game_moment.capture(None, session(), None, at=1.0)
    assert game_moment.changes(no_state, after, frozenset({game_moment.INVENTORY})) == []


class Db:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, model, key):
        return SimpleNamespace(name="Vincent", commentary_mode="occasional")

    def commit(self):
        pass


def _tools(monkeypatch, event=None, *, changed=(), spoke=False):
    monkeypatch.setattr(desktop_tools, "SessionLocal", Db)
    monkeypatch.setattr(desktop_tools.game_moment, "since", lambda db, m, kinds=None: list(changed))
    monkeypatch.setattr(desktop_tools.agent_say, "user_spoke_since", lambda db, uid, at: spoke)
    monkeypatch.setattr(desktop_tools.desktop_game, "tell_hands", lambda db, uid, **kw: "Told them.")
    said = []
    monkeypatch.setattr(desktop_tools.autonomous_speech, "deliver_worded",
                        lambda db, **kw: said.append(kw["text"]) or "Queued to say: it goes out soon.")
    built = {t.name: t for t in desktop_tools.build_desktop_tools(uuid.uuid4(), event, since=moment())}
    return built, said


def test_tool_results_tell_what_changed_once(monkeypatch):
    tools, _ = _tools(monkeypatch, changed=["what you carry: birch_planks 1 -> 33"])
    first = asyncio.run(tools["tell_my_hands"].ainvoke({"message": "go"}))
    assert first.startswith("Told them.\n(Since this turn began, counted by the game: what you carry: birch_planks")
    assert asyncio.run(tools["tell_my_hands"].ainvoke({"message": "go"})) == "Told them."


def test_a_background_turn_leaves_it_once_the_user_spoke(monkeypatch):
    tools, said = _tools(monkeypatch, GameEvent(("stuck",), plan_revision=1), spoke=True)
    assert asyncio.run(tools["tell_my_hands"].ainvoke({"message": "go"})).startswith(
        "Not done: the user said something after you woke")
    assert asyncio.run(tools["say"].ainvoke({"text": "Looking for another sheep."})).startswith("Not said:")
    assert said == []


def test_a_background_say_checks_its_words_after_the_game_moved(monkeypatch):
    tools, said = _tools(monkeypatch, GameEvent(("stuck",), plan_revision=1),
                         changed=['you learned how to "kill a sheep"'])
    first = asyncio.run(tools["say"].ainvoke({"text": "I still can't hit it."}))
    assert first.startswith("Not said yet.") and said == []
    assert asyncio.run(tools["say"].ainvoke({"text": "Got it!"})).startswith("Queued")
    assert said == ["Got it!"]


def test_a_quick_thought_the_game_moved_past_is_not_said(monkeypatch):
    saved, said = [], []

    class ThoughtDb(Db):
        def get(self, model, key):
            return SimpleNamespace(state="running", commentary_mode="occasional", name="Vincent")

    class Thinker:
        def think(self, level, text, user, situation):
            return SimpleNamespace(text="it slipped away", say="Aw, it got away!", latency_ms=500, error=None,
                                   kind="feeling", noticed="sheep gone")

    monkeypatch.setattr(mind, "SessionLocal", ThoughtDb)
    monkeypatch.setattr(mind, "save_thought", lambda db, **kw: saved.append(kw))
    from app.services import autonomous_speech

    monkeypatch.setattr(autonomous_speech, "submit", lambda db, **kw: said.append(kw["text"]) or "Proposed.")
    monkeypatch.setattr(mind, "_log", lambda record: None)
    monkeypatch.setattr(mind.time, "time", lambda: 101.0)
    monkeypatch.setattr(mind_minecraft.game_moment, "since", lambda db, m, kinds=None: ["what you carry: mutton 0 -> 2"])
    sid = uuid.uuid4()
    mind._minds[sid] = mind.ActivityMind(quick_since=moment())
    mind._think_quick(mind_minecraft.SOURCE, Scope(uuid.uuid4(), "minecraft", sid), "light", "tick", 100.0, Thinker())
    assert said == [] and saved[-1]["status"] == "stale"  # not even proposed


def test_a_correction_is_said_only_when_the_game_moved(monkeypatch):
    delivered, written, logged = [], [], []
    monkeypatch.setattr(game_correction, "SessionLocal", Db)
    monkeypatch.setattr(game_correction.autonomous_speech, "submit",
                        lambda db, **kw: delivered.append(kw) or "Proposed: queued to wake her mind.")
    monkeypatch.setattr(mind, "_log", logged.append)

    def write(said, changed, user, voice=""):
        written.append((said, changed, user))
        return "Oh wait, it just worked: I've got the trick now."
    monkeypatch.setattr(game_correction, "write", write)

    monkeypatch.setattr(game_correction.game_moment, "since", lambda db, m, kinds=None: [])
    assert game_correction.check(uuid.uuid4(), "I haven't learned it yet.", moment()) is None
    assert written == [] and delivered == []

    learned = ['you learned how to "build up": your hands have it now as learned_build_up']
    monkeypatch.setattr(game_correction.game_moment, "since", lambda db, m, kinds=None: learned)
    when = moment()
    outcome = game_correction.check(uuid.uuid4(), "I haven't learned it yet. [[sticker:thinking]]", when)
    assert outcome.startswith("Proposed")  # her mind decides whether it is said; no direct delivery
    assert written == [("I haven't learned it yet.", learned, "Vincent")]
    assert delivered[0]["text"].startswith("Oh wait") and delivered[0]["reasons"][0].startswith("correction:")
    assert delivered[0]["activity_id"] == when.session_id and delivered[0]["source"] == "minecraft"
    assert logged[-1]["correction"].startswith("Oh wait")
