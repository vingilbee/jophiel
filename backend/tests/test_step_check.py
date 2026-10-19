"""Plan steps that end by what's true (app/ai/desktop/step_check.py): pure, no game."""
from app.ai.desktop import step_check as sc


def state(inventory=None, kills=None, land=None, nearby=None):
    return {"inventory": [{"name": n, "count": c} for n, c in (inventory or {}).items()], "kills": kills or {},
            "terrain": {"land": land} if land is not None else {}, "nearby": nearby or []}


def test_has_and_gained_count_ids_and_families():
    now = state({"birch_planks": 3, "oak_planks": 2, "oak_log": 1})
    assert sc.met({"kind": "has", "what": "planks", "count": 5}, now, None) == "you have 5 planks"
    assert sc.met({"kind": "has", "what": "birch_planks", "count": 4}, now, None) is None
    assert sc.met({"kind": "has", "what": "logs"}, now, None) == "you have 1 logs"
    assert sc.met({"kind": "has", "what": "glass"}, state({"glass": 1}), None) == "you have 1 glass"
    start = sc.baseline(state({"birch_planks": 1}))
    assert sc.met({"kind": "gained", "what": "planks", "count": 4}, now, start) == "you got 4 more planks"
    assert sc.met({"kind": "gained", "what": "planks", "count": 5}, now, start) is None


def test_killed_counts_from_the_step_start():
    start = sc.baseline(state(kills={"sheep": 2}))
    assert sc.met({"kind": "killed", "what": "sheep"}, state(kills={"sheep": 2}), start) is None
    assert sc.met({"kind": "killed", "what": "sheep"}, state(kills={"sheep": 3}), start) == "you killed 1 sheep"


def test_tops_and_being_near_someone():
    assert sc.met({"kind": "top_here"}, state(land={"close": {"rel": 3}}), None) is None
    assert sc.met({"kind": "top_here"}, state(land={"close": {"rel": 0}}), None)
    assert sc.met({"kind": "top_here"}, state(), None) is None  # no terrain read: not checkable
    assert sc.met({"kind": "peak"}, state(land={"peak": {"rel": 9}}), None) is None
    assert sc.met({"kind": "peak"}, state(land={"peak": None}), None) == "you're on the highest ground near"
    near = state(nearby=[{"label": "tearDrop12", "distance": 2.4}])
    assert sc.met({"kind": "near_player", "what": "tearDrop12"}, near, None) == "you're 2.4 m from tearDrop12"
    assert sc.met({"kind": "near_player", "what": "tearDrop12", "count": 2}, near, None) is None


def test_unknown_or_empty_checks_are_never_true():
    assert sc.met(None, state(), None) is None
    assert sc.met({"kind": "has"}, state({"dirt": 9}), None) is None
    assert sc.met({"kind": "fly"}, state(), None) is None


def test_what_changed_while_a_skill_ran():
    before = sc.baseline(state({"mutton": 1, "stick": 2}, {"sheep": 1}))
    after = state({"mutton": 3, "white_wool": 1}, {"sheep": 3})
    assert sc.change(before, after) == "mutton +2, stick -2, white_wool +1; killed sheep 2"
    assert sc.change(before, state({"mutton": 1, "stick": 2}, {"sheep": 1})) is None
