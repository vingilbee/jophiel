"""The mind tick (what changed, pressure, the text) and the gates' request/response handling."""
from app.ai.mind import gates as g
from app.ai.mind.tick import TickInput, changes, danger, news, pressure, render, worse


def state(**over) -> dict:
    base = {
        "health": 20, "food": 18, "oxygen": 20, "position": {"x": 0, "y": 64, "z": 0},
        "held": {"name": "stone_sword", "count": 1}, "inventory": [{"name": "bread", "count": 3}],
        "time": {"phase": "day", "day": 3}, "weather": "clear",
        "nearby": [{"label": "Vincent", "kind": "player", "distance": 4, "side": "ahead"}],
        "players": [{"name": "Vincent", "holding": "stone_pickaxe", "moving": "standing still", "pose": "standing",
                     "recent": []}],
        "skill": {"name": "follow_player", "params": {"player": "Vincent"}},
        "text": "Columbina at (0, 64, 0)...",
    }
    return {**base, **over}


def tick(at: float = 100.0, **kwargs) -> TickInput:
    return TickInput(at=at, state=kwargs.pop("state", state()), **kwargs)


def test_pressure_urgent_busy_calm():
    calm = tick(state=state(skill=None))
    assert pressure(calm, None)[0] == "calm"
    assert pressure(tick(), None) == ("busy", "your hands are running follow_player")
    zombie = tick(state=state(nearby=[{"label": "zombie", "kind": "hostile", "distance": 5, "side": "left"}]))
    assert pressure(zombie, None) == ("urgent", "zombie 5 m from you")
    far = tick(state=state(nearby=[{"label": "zombie", "kind": "hostile", "distance": 20, "side": "left"}]))
    assert pressure(far, None)[0] == "busy"
    hurt = tick(at=101, state=state(health=14))
    assert pressure(hurt, tick())[0] == "urgent"
    drowning = tick(state=state(oxygen=6, in_water=True))
    assert pressure(drowning, None)[0] == "urgent"
    # Low air out of water is air coming back, or a bad reading (once another creature's air).
    assert pressure(tick(state=state(oxygen=6, in_water=False)), None)[0] == "busy"
    user_hit = tick(notices=[{"kind": "player_hurt", "urgency": "heads_up", "text": "Vincent got hit by a zombie"}])
    assert pressure(user_hit, None) == ("urgent", "Vincent got hit by a zombie")


def test_changes_lists_what_moved_since_the_last_tick():
    before = tick(at=100)
    after = tick(at=101, state=state(
        health=17, inventory=[{"name": "bread", "count": 2}, {"name": "iron_ore", "count": 1}],
        nearby=[{"label": "Vincent", "kind": "player", "distance": 4, "side": "ahead"},
                {"label": "zombie", "kind": "hostile", "distance": 14, "side": "behind"}],
        players=[{"name": "Vincent", "holding": "torch", "moving": "walking north", "pose": "standing",
                  "recent": [{"text": "placed crafting_table", "seconds_ago": 0}]}],
        time={"phase": "sunset", "day": 3}),
        talk=[{"role": "user", "content": "let's get iron", "at": 100.5}])
    got = changes(after, before)
    assert got == [
        "your health 20 -> 17/20",
        "you lost bread x1", "you got iron ore x1",
        "zombie appeared 14 m behind",
        "Vincent: standing still -> walking north",
        "Vincent: holding stone pickaxe -> holding torch",
        "Vincent placed crafting table",
        "it's now sunset",
        'the user said: "let\'s get iron"',
    ]
    assert changes(tick(at=102), tick(at=101)) == []  # a quiet second
    assert changes(tick(), None) == ["(first tick: everything below is new)"]


def test_news_leaves_out_background():
    before = tick(at=100, state=state(oxygen=20, nearby=[
        {"label": "Vincent", "kind": "player", "distance": 4, "side": "ahead"},
        {"label": "salmon", "kind": "passive", "distance": 6, "side": "left"},
        {"label": "skeleton", "kind": "hostile", "distance": 6, "side": "behind"}]))
    after = tick(at=101, state=state(
        oxygen=16, position={"x": 6, "y": 64, "z": 0},
        nearby=[{"label": "Vincent", "kind": "player", "distance": 4, "side": "ahead"},
                {"label": "tropical_fish", "kind": "passive", "distance": 28, "side": "right"}],
        players=[{"name": "Vincent", "holding": "stone_pickaxe", "moving": "running north", "pose": "standing",
                  "recent": []}]),
        talk=[{"role": "assistant", "content": "coming!", "at": 100.5}])
    assert changes(after, before) == [
        "your air 20 -> 16/20", "you moved 6 m", "tropical fish appeared 28 m right",
        "salmon is gone", "skeleton is gone", "Vincent: standing still -> running north", 'you said: "coming!"',
    ]
    assert news(after, before) == ["skeleton is gone"]  # a threat leaving is news; the rest is background
    assert news(tick(at=102, state=state(oxygen=8, in_water=True)), after)[0] == "your air 16 -> 8/20"  # now it matters
    assert news(tick(at=102, state=state(oxygen=8)), after) == []  # not in water: no danger
    assert news(tick(), None) == ["(first tick: everything below is new)"]


def test_worse_danger():
    def at(distance, **over):
        return danger(state(nearby=[{"label": "zombie", "kind": "hostile", "distance": distance}], **over))

    assert not worse(at(4), at(4)) and not worse(at(3), at(4))
    assert worse(at(2), at(4))  # 2 m closer
    assert worse(at(4, health=15), at(4))
    assert worse(danger(state(nearby=[{"label": "zombie", "kind": "hostile", "distance": 4},
                                      {"label": "spider", "kind": "hostile", "distance": 7}])), at(4))
    assert worse(at(4, oxygen=2, in_water=True), at(4, oxygen=6, in_water=True))
    assert not worse(at(4, oxygen=6, in_water=True), at(4, oxygen=9, in_water=True))
    assert not worse(at(4, oxygen=2), at(4, oxygen=6))  # out of water


def test_render_puts_changes_first_then_pressure_goal_talk_state():
    plan = {"goal": "get iron", "steps": [{"do": "follow Vincent", "state": "active"}]}
    talk = [{"role": "user", "content": "follow me", "at": 100}]
    text, level, changed, why = render(tick(at=110, plan=plan, talk=talk), tick(at=109, plan=plan, talk=talk))
    assert level == "busy" and changed == []
    assert text.splitlines()[:4] == [
        "Changed since last tick:", "- nothing", "Pressure: busy (your hands are running follow_player)",
        "Goal: get iron; current step: follow Vincent"]
    assert "  user (10 s ago): follow me" in text
    assert text.endswith("Game state:\nColumbina at (0, 64, 0)...")


def test_urgent_pressure_caps_hard_thinking():
    assert g.capped("hard", "urgent") == "react" and g.capped("hard", "combat") == "react"
    assert g.capped("hard", "busy") == "hard"
    assert g.capped("none", "urgent") == "none"


GAME_WORDS = ("minecraft", "mob", "zombie", "creeper", "skill", "mining", "night")


def test_the_mind_speaks_generally_and_the_source_adds_its_own_examples():
    from app.ai.mind.think import QuickThinker
    from app.ai.mind.tick import MINECRAFT

    thinker = QuickThinker.__new__(QuickThinker)
    general = [g.instructions(), g.question(), *g.level_guide().values(), thinker.instructions("light", "Vincent"),
               thinker.instructions("react", "Vincent")]
    for text in general:
        assert not [w for w in GAME_WORDS if w in text.lower()], text
    assert "playing Minecraft as her own player next to the user" in g.question(MINECRAFT)
    assert "creeper!" in g.level_guide(MINECRAFT)["react"]
    assert "playing Minecraft as her own player next to Vincent" in thinker.instructions("light", "Vincent", MINECRAFT)
    assert g.jev("key").body("tick", MINECRAFT)["questions"]["level"]["criteria"] == g.level_guide(MINECRAFT)


def test_decisions_gate_body_and_answer():
    gate = g.OpenAIDecisionsGate(api_key="k")
    body = gate.body("tick text")
    question = body["questions"][0]
    assert body["input"] == "tick text" and question["type"] == "choice" and question["name"] == "level"
    assert [c["value"] for c in question["choices"]] == list(g.LEVELS)
    result = gate.parse({"answers": [{"type": "choice", "name": "level", "choice": "light", "confidence": 0.6,
                                      "probabilities": [{"value": "light", "probability": 0.7},
                                                        {"value": "none", "probability": 0.3}]}],
                         "usage": {"input_tokens": 900, "input_tokens_details": {"cached_tokens": 800},
                                   "output_tokens": 3}})
    assert (result.level, result.probabilities, result.input_tokens, result.cached_tokens) == (
        "light", {"light": 0.7, "none": 0.3}, 900, 800)
    assert gate.parse({"answers": [{"name": "level", "type": "refusal"}]}).error == "refusal"


def test_system_one_gate_body_and_answer():
    gate = g.jev("key")
    body = gate.body("tick text")
    assert body["model"] == "typesafe/jev-1.13" and body["state"] == {"tick": "tick text"}
    assert body["questions"]["level"]["type"] == "choice"
    assert list(body["questions"]["level"]["criteria"]) == list(g.LEVELS)
    result = gate.parse({"answers": {"level": {"type": "choice", "choice": "react", "confidence": 0.8,
                                               "probabilities": {"react": 0.9, "light": 0.1}}},
                         "usage": {"input_tokens": 700, "output_tokens": 5, "cost": 0.00003}})
    assert (result.level, result.cost_usd, result.input_tokens) == ("react", 0.00003, 700)
    assert g.strands("http://127.0.0.1:8765/").url == "http://127.0.0.1:8765/v1/systemone"


def test_a_failing_gate_is_a_result_not_an_exception():
    class Broken(g.SystemOneGate):
        def _post(self, text, situation):
            raise RuntimeError("HTTP 401: no key")

    result = Broken("x", "http://nowhere", "m").decide("tick")
    assert result.level is None and result.error == "RuntimeError: HTTP 401: no key" and result.latency_ms >= 0


def test_recorder_writes_one_line_per_tick_with_its_own_notice_cursor(tmp_path, monkeypatch):
    import json
    import uuid
    from types import SimpleNamespace

    from app.services import mind_ticks

    class Pack:
        def __init__(self):
            self.asked = []

        def notices(self, since=0):
            self.asked.append(since)
            news = [{"seq": 6, "kind": "night", "urgency": "heads_up", "text": "it's night"}] if since == 5 else []
            return {"next": 6 if since >= 5 else 5, "notices": news}

    path = tmp_path / "ticks.jsonl"
    monkeypatch.setattr(mind_ticks, "tick_path", lambda: path)
    monkeypatch.setattr(mind_ticks, "recent_talk", lambda db, user_id, now: [])
    monkeypatch.setattr(mind_ticks.desktop_commands, "load_driver_state", lambda sid: {"notes": "near the ore"})
    for registry in (mind_ticks._previous, mind_ticks._notice_seq, mind_ticks._tick_no):
        registry.clear()
    session = SimpleNamespace(id=uuid.uuid4(), owner_user_id=uuid.uuid4(), state="running",
                              plan={"goal": "get iron", "steps": []})
    pack = Pack()
    mind_ticks._record(None, pack, session, state(), 100.0)
    mind_ticks._record(None, pack, session, state(health=18), 101.0)
    mind_ticks._record(None, pack, session, state(health=18), 200.0)  # long gap: a fresh start
    first, second, third = (json.loads(line) for line in path.read_text().splitlines())
    assert pack.asked == [0, 5, 6, 6]  # starts from "now", then follows its own cursor
    assert (first["tick"], first["changed"]) == (1, ["(first tick: everything below is new)", "noticed: it's night"])
    assert second["changed"] == ["your health 20 -> 18/20"]
    assert second["pressure"] == "urgent" and second["input"]["hands_note"] == "near the ore"
    assert third["changed"] == ["(first tick: everything below is new)"]


def test_going_underground_and_a_cave_mouth_are_news():
    def place(kind, openings=()):
        return {"overhead": {"kind": kind}, "ways": [], "openings": list(openings)}

    mouth = {"position": {"x": 0, "y": 64, "z": -6}, "distance": 6, "side": "ahead"}
    outside = tick(at=100, state=state(surroundings=place("sky")))
    sees_cave = tick(at=101, state=state(surroundings=place("sky", [mouth])))
    assert news(sees_cave, outside) == ["a cave or tunnel mouth came into view 6 m ahead"]
    moved = {**mouth, "position": {"x": 1, "y": 64, "z": -5}, "distance": 5}
    assert news(tick(at=102, state=state(surroundings=place("sky", [moved]))), sees_cave) == []  # the same mouth
    inside = tick(at=103, state=state(surroundings=place("underground")))
    assert news(inside, sees_cave) == ["you went underground"]
    assert news(tick(at=104, state=state(surroundings=place("trees"))), inside) == ["you're outdoors again"]
    assert news(tick(at=104, state=state(surroundings=place("trees"))), outside) == []  # under trees is still outdoors
    assert news(outside, tick(at=99)) == []  # an older pack without surroundings: nothing
