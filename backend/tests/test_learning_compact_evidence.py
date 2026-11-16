"""The reviewer's view of a game stretch stays readable (2026-11-06).

A raw record ran 1-3k characters, so a 20-minute stretch was ~300k characters and every review of a whole stretch
parked unread ("the evidence is more than can be read at once"). These check the compact lines say what happened
without inventing anything, and that the condensed view keeps every decision citable."""

from types import SimpleNamespace
from datetime import datetime, timezone

from app.ai.learning import assessment as A
from app.services import learning_assessment as LA


def run(before, after, **executed):
    return {"kind": "skill_run", "intended": {"skill": "go_to", "params": {"target": "peak", "time_limit": 60}},
            "result": executed.pop("result", "succeeded"),
            "executed": {"status": "done", "seconds": 20.7, "observed": {"before": before, "after": after}, **executed}}


def test_a_run_says_its_call_ending_and_what_changed():
    before = {"position": {"x": -225.4, "y": 84, "z": 166.6}, "health": 20, "food": 20,
              "inventory": {"dirt": 9, "oak_log": 4}, "held": {"name": "dirt"}}
    after = {"position": {"x": -241.6, "y": 100.3, "z": 222.5}, "health": 17, "food": 20,
             "inventory": {"dirt": 7, "oak_log": 4, "cobblestone": 1}, "held": {"name": "dirt"}}
    text = LA._execution_brief(run(before, after, data={"counted": "dirt -2", "x": -242}))
    assert text.startswith("go_to(target=peak) -> done; 20.7 s")
    assert "moved -225 84 167 -> -242 100 222" in text and "health 20 -> 17" in text
    assert "inventory cobblestone +1, dirt -2" in text and "inventory after:" in text
    assert "counted" not in text  # the pack's own count repeats the observed change
    assert "time_limit" not in text and "food" not in text  # unchanged food isn't news


def test_a_run_that_changed_nothing_says_so():
    state = {"position": {"x": 1, "y": 2, "z": 3}, "health": 20, "inventory": {"dirt": 1}}
    text = LA._execution_brief(run(state, dict(state), reason="no path", result="failed", status="failed"))
    assert "-> failed; 20.7 s; reason: no path" in text
    assert "observed: no change in place, health, food or inventory" in text and "inventory after" not in text
    assert LA._execution_line(run(state, dict(state), reason="no path")) == "-> done 20.7 s; no path"


def test_other_runs_read_as_what_they_did():
    route = {"kind": "thought_route", "result": "succeeded",
             "intended": {"kind": "decision", "conclusion": "mine it", "for_hands": "use the pickaxe"},
             "executed": {"routed": {"tip": ["accepted", ""]}}}
    text = LA._execution_brief(route)
    assert text.startswith("her decision thought routed (with a tip for her hands)") and "accepted" in text


def test_her_mind_and_planner_read_as_their_own_reasoning():
    thought = SimpleNamespace(learner="hard_thought", output={"raw": "{...}", "parsed": {
        "kind": "decision", "conclusion": "I'll mine it instead of roaming.", "thinking": "Roaming took long.",
        "for_hands": "Mine nearby.", "question": None}})
    text = LA._answer_brief(thought)
    assert text.startswith("her mind's hard thought (her own reasoning at the time: not evidence")
    assert "conclusion: I'll mine it instead of roaming." in text and "question" not in text and "{...}" not in text
    planner = SimpleNamespace(learner="planner", output={"parsed": {"decision": "revise", "why": "it wasn't working",
                                                                    "steps": [{"do": "mine"}]}})
    assert "her planner's decision" in LA._answer_brief(planner) and "why: it wasn't working" in LA._answer_brief(planner)


def test_a_targeted_outcome_does_not_repeat_its_call():
    e = SimpleNamespace(kind="skill_outcome", occurred_at=datetime(2026, 11, 6, 15, 1, tzinfo=timezone.utc),
                        payload={"skill": "go_to", "params": {"target": "block:stone"}, "status": "failed",
                                 "reason": "no stone in sight within 64 blocks", "attempt_id": "a1",
                                 "data": {"counted": "dirt +1"}})
    text = LA._experience_brief(e)
    assert "skill_outcome: go_to -> failed" in text and "block:stone" not in text
    assert "reason: no stone in sight" in text and "counted while it ran: dirt +1" in text


def _stretch() -> A.Evidence:
    ev = A.Evidence(trigger="a stretch of play", trigger_refs=())
    for k in (1, 2):
        ev.targets[f"h{k}"] = A.Target(f"h{k}", "hands", "her hands' decision", f"run:{k}", (f"attempt:{k}",),
                                       brief=f"[15:0{k}:00] go_to(target=-1{k} 70 5) -> failed 60 s; no path")
        ev.add(A.Item(ref=f"h{k}.d", kind="record", event=f"run:{k}", roots=(f"attempt:{k}",), target=f"h{k}",
                      learner="hands", text=f"her hands' decision (ok): chose go_to {k} " + "x" * 400))
        ev.add(A.Item(ref=f"g{k}", kind="game", event=f"run:{k}", roots=(f"experience:{k}",), target=f"h{k}",
                      text=f"skill_outcome {k} " + "y" * 300))
    ev.targets["s1"] = A.Target("s1", "stretch", "the recorded stretch of play", "assessment:1", ())
    for k in range(3):
        ev.add(A.Item(ref=f"x{k}", kind="fact", event=f"execution:{k}", roots=(f"execution:{k}",), target="s1",
                      text="effect of her decision (work no_change, target none): nothing changed"))
    ev.add(A.Item(ref="a1", kind="record", event="attempt:t", roots=("attempt:t",), target="s1",
                  learner="hard_thought", text="her mind's hard thought: conclusion: dig down instead"))
    ev.add(A.Item(ref="n1", kind="record", event="attempt:s", roots=("attempt:s",), target="s1",
                  learner="mind", text="her mind's evaluation: " + "z" * 500))
    return ev


def test_the_condensed_view_keeps_every_decision_citable_and_her_thoughts_whole():
    ev = _stretch()
    full, short = A.render(ev), A.render(ev, condensed=True)
    assert len(short) < len(full)
    assert "h1 [15:01:00] go_to(target=-11 70 5) -> failed 60 s; no path" in short
    assert "Cite hN: it stands for all of that decision's records" in short
    assert "h1.d" not in short and "skill_outcome 1" not in short  # stood for by the h1 line
    assert "conclusion: dig down instead" in short  # her mind's thought is kept whole
    assert short.count("nothing changed") == 1 and "(and 2 more identical records in this stretch)" in short
    assert "z" * 200 not in short and "z" * 50 in short  # conversation turns during play are cut short
    # The same evidence: the digest is of the full view, whichever is read.
    assert A.digest(ev) == A.digest(_stretch())
    # A cited hN stands for its records.
    checked = A.check({"targets": [{"ref": "h1", "status": "supported", "cites": ["h1"], "why": "it failed"}]}, ev)
    assert checked.result["targets"][0]["cites"]


def test_the_full_view_is_unchanged_by_the_condensed_option():
    ev = _stretch()
    full = A.render(ev)
    assert "h1.d her hands' decision" in full and "skill_outcome 1" in full and full.count("nothing changed") == 3
    assert "Condensed" not in full


def test_a_review_sees_which_lessons_her_parts_applied_and_where(monkeypatch):
    """Live 2026-11-06 her hands said they applied "choose a nearby passage target" 87 times while no iron was
    found, and no review ever saw it. The lessons given during a stretch are shown with what each part said, and
    each decision says which it applied, so a lesson can be judged by what followed its use."""
    import uuid
    from app.services import memory_lessons

    lesson, other = uuid.uuid4(), uuid.uuid4()
    h1, h2, thought = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    use = lambda lid, aid, reported, consumer="hands": SimpleNamespace(  # noqa: E731
        lesson_id=lid, attempt_id=aid, reported=reported, consumer=consumer)
    uses = [use(lesson, h1, "applied"), use(lesson, h2, "applied"), use(other, h2, "not_relevant"),
            use(lesson, thought, None, "mind")]
    db = SimpleNamespace(scalars=lambda q: uses)
    monkeypatch.setattr(memory_lessons, "for_learning_ids", lambda db, uid, ids: [
        A.PriorLesson("", str(i), 1, f"(hypothesis) lesson {i}") for i in ids])
    ev = A.Evidence(trigger="a stretch of play", trigger_refs=())
    row = SimpleNamespace(user_id=uuid.uuid4())
    records = [SimpleNamespace(id=thought, learner="hard_thought"), SimpleNamespace(id=uuid.uuid4(), learner="hands")]
    notes = LA._lessons_used(db, row, ev, {str(h1): "h1", str(h2): "h2"}, records)
    assert notes == {"h1": "applied L1", "h2": "applied L1, L2 not relevant"}
    text = ev.lessons["L1"].text
    assert ev.lessons["L1"].id == str(lesson) and "Given in this stretch:" in text
    assert "her hands said applied on 2 decision(s) (h1, h2)" in text
    assert "her mind was given it (no report) on 1 thought(s)" in text
    assert "her hands said not relevant on 1 decision(s) (h2)" in ev.lessons["L2"].text
    # Nothing given: nothing shown.
    assert LA._lessons_used(SimpleNamespace(scalars=lambda q: []), row, A.Evidence("x", ()), {str(h1): "h1"}, []) == {}
