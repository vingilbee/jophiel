"""The shared-state fast path (docs/SHARED_STATE_REFACTOR_SPEC.md step 3): user message -> scoped view ->
cognition -> mouth. Routing and prompt are pure; the assembly runs with stub adapters; the cache's reread
against memory is in ``test_shared_state_cache.py`` and the initial memory check against memory in
``test_initial_recall.py`` (real PostgreSQL). No model calls."""
from __future__ import annotations

import json
from datetime import datetime, timezone
import uuid
from types import SimpleNamespace

import pytest

from app.ai.context import AgentRuntimeContext
from app.ai.prompts import build_system_prompt
from app.ai.state.contracts import (
    Assessed, CacheEntry, CognitionView, RecallDecision, ConversationView, EvidenceAssessment, Interpretation, MemoryContextView,
    MissingEvidence, OutputView, Projection, SharedView, ThinkingDecision, WorkView, Envelope, SCHEMA_VERSION,
)
from app.ai.recall.contracts import MemoryRef
from app.ai.state.routing import route
from app.ai.state import student_two as S
from app.services import cognitive_live, recall_references, shared_state, student_two

USER = uuid.uuid4()
CONV = uuid.uuid4()
MSG = uuid.uuid4()
SECRET = "my sister's name is Juniper"


def _interpretation(request="talk_only", acts=("small_talk",)):
    return Interpretation(acts=acts, tone="friendly", request=request, probabilities={}, model="s1:test")


def _cognition(status="available", interpretation=None, evidence=None):
    return CognitionView(
        Projection("cognition", status, reason=None if status == "available" else "test"),
        interpretation=interpretation if status == "available" else None,
        conversational_action=Assessed("respond", "policy"),
        missing_evidence=evidence or EvidenceAssessment("not_assessed", "not_assessed", reason="no head"))


def _view(cognition=None, thinking=None, memory=None, recall=None):
    from datetime import datetime, timezone
    return SharedView(
        recall=recall or RecallDecision(Projection.off("student_2", "off")),
        envelope=Envelope(SCHEMA_VERSION, USER, CONV, 3, datetime.now(timezone.utc), f"message:{MSG}"),
        personality=Projection.off("character", "off"),
        conversation=ConversationView(Projection("chat", "available"), CONV, 3, MSG),
        memory=memory or MemoryContextView(Projection("memory", "available")),
        cognition=cognition or _cognition(interpretation=_interpretation()),
        thinking=thinking or ThinkingDecision(Projection.off("student_2", "off")),
        work=WorkView(Projection("chat_worker", "available")), output=OutputView(Projection("delivery", "available")),
        inner_state=Projection.off("inner_state", "x"), activities=Projection.missing("activities", "x"),
        situation=Projection.missing("observation", "x"), mind=Projection.off("mind", "x"))


# --- routing (pure) ----------------------------------------------------------------------------------


def test_an_unassessed_turn_is_answered_but_never_reported_as_fast_path_eligible():
    decision = route(_view())
    # Student 1 can't say whether older memory or careful thinking is needed: a named limit, never a "no".
    assert decision.route == "unassessed" and decision.supported and decision.degraded == ()
    assert decision.outcome == "unassessed" and decision.eligibility == "unassessed"
    assert {"recall_need_not_assessed", "thinking_need_not_assessed"} <= set(decision.limits)
    # Only one of the two assessed is still unassessed.
    none_missing = EvidenceAssessment("none_missing", "student_1")
    half = route(_view(cognition=_cognition(interpretation=_interpretation(), evidence=none_missing)))
    assert half.route == "unassessed" and half.limits == ("thinking_need_not_assessed",)


def test_fast_path_is_confirmed_only_when_both_requirements_were_assessed_absent():
    none_missing = EvidenceAssessment("none_missing", "student_1")
    no_thinking = ThinkingDecision(Projection("student_2", "available"), required=False, decider="student_2", reason="trivial")
    decision = route(_view(cognition=_cognition(interpretation=_interpretation(), evidence=none_missing),
                           thinking=no_thinking))
    assert decision.route == "fast_path" and decision.eligibility == "confirmed"
    assert decision.outcome == "fast_path" and decision.limits == ()


def test_no_cognition_is_a_degraded_turn_not_a_normal_one():
    for status in ("unavailable", "disabled"):
        decision = route(_view(cognition=_cognition(status)))
        assert "cognition_unavailable" in decision.degraded and decision.outcome == "degraded"


def test_known_needs_route_to_recall_and_background_thinking():
    missing = EvidenceAssessment("missing", "student_1", (MissingEvidence("what did we build?", "personal history"),))
    needs_memory = _cognition(interpretation=_interpretation(), evidence=missing)
    think = ThinkingDecision(Projection("student_2", "available"), required=True, decider="student_2", reason="hard")
    assert route(_view(cognition=needs_memory)).route == "recall"
    assert route(_view(cognition=needs_memory)).eligibility == "not_eligible"
    assert route(_view(thinking=think)).route == "mind"
    both = route(_view(cognition=needs_memory, thinking=think))
    # Thinking with recall: both go to background work; the mouth searches nothing and waits for nothing.
    assert both.route == "recall_then_mind" and both.supported and both.degraded == ()
    assert both.schedules_mind and not both.runs_recall


def test_a_request_beyond_talking_and_a_changed_cache_are_limits_the_mouth_hears():
    decision = route(_view(cognition=_cognition(interpretation=_interpretation("outside_ability", ("requesting",))),
                           memory=MemoryContextView(Projection("memory", "available"), gone=("event:4",))))
    assert {"request_outside_talking", "memory_cache_changed"} <= set(decision.limits)
    text = shared_state.boundary_text(_view(), route(_view()))
    assert "Memory was not searched" in text
    assert OLDER in text and THINKING in text


OLDER = "needs anything older than what you have"
THINKING = "needs careful thinking"


def test_each_unknown_is_said_on_its_own_and_never_contradicts_a_decision():
    # Memory need unknown, thinking explicitly decided (not needed): only the memory unknown is said.
    no_thinking = ThinkingDecision(Projection("student_2", "available"), required=False, decider="student_2", reason="trivial")
    view = _view(thinking=no_thinking)
    decision = route(view)
    text = shared_state.boundary_text(view, decision)
    assert decision.limits == ("recall_need_not_assessed",) and decision.route == "unassessed"
    assert OLDER in text and THINKING not in text

    # Memory need assessed (nothing missing), thinking unknown: only the thinking unknown is said.
    none_missing = EvidenceAssessment("none_missing", "student_1")
    view = _view(cognition=_cognition(interpretation=_interpretation(), evidence=none_missing))
    decision = route(view)
    text = shared_state.boundary_text(view, decision)
    assert decision.limits == ("thinking_need_not_assessed",) and decision.route == "unassessed"
    assert THINKING in text and OLDER not in text

    # Both decided absent: neither is said, and the route is a confirmed fast path.
    view = _view(cognition=_cognition(interpretation=_interpretation(), evidence=none_missing), thinking=no_thinking)
    text = shared_state.boundary_text(view, route(view))
    assert "Nobody checked" not in text and route(view).eligibility == "confirmed"


# --- prompt --------------------------------------------------------------------------------------------


def test_the_cached_memory_prompt_says_nothing_else_was_looked_up_and_replaces_the_recall_block():
    ctx = AgentRuntimeContext(user_id=USER, recall="SHOULD NOT SHOW", cached_memory="- [event:4] died",
                              turn_boundary="- Memory search FAILED.")
    prompt = build_system_prompt(ctx)
    assert "nothing else was looked up for this message" in prompt and "- [event:4] died" in prompt
    assert "What you had for this message" in prompt and "SHOULD NOT SHOW" not in prompt
    plain = build_system_prompt(AgentRuntimeContext(user_id=USER, recall="RECALLED"))
    assert "RECALLED" in plain and "What you had for this message" not in plain


def test_typed_memory_never_says_nothing_relevant_when_nothing_was_looked_up():
    assert "nothing relevant" in cognitive_live.typed_memory_text(None)
    unsearched = cognitive_live.typed_memory_text(None, searched=False)
    assert "nothing relevant" not in unsearched and "not looked up for this message" in unsearched


# --- assembly with stub adapters ----------------------------------------------------------------------


def _settings(tmp_path, **over):
    base = dict(shared_state_log_path=str(tmp_path / "shared.jsonl"),
                cognitive_live_student_dir=None, recall_tool_calls_per_reply=3, student_fails=False)
    return SimpleNamespace(**(base | over))


def _turn_recall(status, records=(), rendered="", legacy=None):
    return recall_references.TurnRecall(USER, CONV, "UTC", datetime.now(timezone.utc), None,
                                        SimpleNamespace(status=status, records=list(records), coverage={}),
                                        rendered, legacy)


@pytest.fixture
def stubs(monkeypatch, tmp_path):
    calls = {"prepare": [], "fresh": 0, "cache": 0}
    settings = _settings(tmp_path)
    monkeypatch.setattr(shared_state, "get_settings", lambda: settings)
    shared_state._personality.cache_clear()
    monkeypatch.setattr(shared_state, "_conversation", lambda u, c, t: ConversationView(
        Projection("chat", "available", version="3"), c, 3, t))
    record = SimpleNamespace(ref=MemoryRef("event", "4"), fingerprint="f" * 40, applicability="current",
                             corrected=False, content="died", matched=(), lifecycle=None,
                             recorded_at=datetime(2026, 10, 20, tzinfo=timezone.utc))
    # The initial memory check: what memory's reply search returns (one per call; the last repeats).
    found = _turn_recall("matched", [record], "Memory search ran (looked in: events):\n- [event:4] died")
    calls["searches"] = [found]

    def fresh(*a, **k):
        calls["fresh"] += 1
        searches = calls["searches"]
        return searches.pop(0) if len(searches) > 1 else searches[0]

    monkeypatch.setattr(recall_references, "for_reply", fresh)
    cached = _turn_recall("matched", [record], "Memories from your last answer, re-read now:\n- [event:4] died")

    def reread(*a, **k):
        calls["cache"] += 1
        return cached

    monkeypatch.setattr(recall_references, "cached_for_reply", reread)

    def prepare(**kwargs):
        calls["prepare"].append(kwargs)
        if settings.student_fails:
            return None
        return cognitive_live.LivePrep(interpretation=dict(
            acts={"small_talk": 0.9}, tone={"friendly": 0.8}, request={"no_request": 0.9},
            understand={"acts": {"small_talk": True, "asking": False}, "tone": "friendly", "request": "no_request"}),
            state={"notes": [{"kind": "their_open_question", "opened_turn": 2}]})

    monkeypatch.setattr(cognitive_live, "prepare", prepare)
    # Student 2, scripted: the real snapshot, render, parse and check run; only the model's answer is given.
    calls["student"] = {"answer": json.dumps(SAY_HI), "down": False, "texts": []}
    monkeypatch.setattr(student_two, "gather", lambda db, **k: S.Live(
        "user_message", "chat", datetime.now(timezone.utc), (), k["message"]))
    monkeypatch.setattr(student_two, "manifest", lambda: {"name": "test-student", "system": "sys", "rendering": None})

    def ask(model, text):
        calls["student"]["texts"].append(text)
        if calls["student"]["down"]:
            raise TimeoutError("read timeout")
        return calls["student"]["answer"]

    real_decide = student_two.decide
    monkeypatch.setattr(student_two, "decide", lambda live, **k: real_decide(live, **k, ask=ask))
    monkeypatch.setattr(student_two, "apply", lambda db, outcome, **k: setattr(outcome, "effect", "nothing changed")
                        or "nothing changed")
    monkeypatch.setattr(student_two, "log", lambda *a, **k: None)
    # No database here: the coordinator's supersession and scheduling are covered in test_coordinator.py.
    from app.services import coordinator
    monkeypatch.setattr(coordinator, "supersede_older", lambda *a, **k: 0)
    monkeypatch.setattr(coordinator, "schedule", lambda **k: None)
    return settings, calls, found


def _prepare(**over):
    return shared_state.prepare(**({"user_id": USER, "conversation_id": CONV, "user_message_id": MSG,
                                    "history": [{"role": "user", "content": SECRET}], "message": SECRET,
                                    "zone": "UTC", "turn_id": uuid.uuid4(), "reply_message_id": uuid.uuid4()}
                                   | over))


def test_every_message_gets_an_initial_memory_check_before_cognition(stubs):
    settings, calls, _ = stubs
    turn = _prepare()
    assert calls["fresh"] == 1 and calls["cache"] == 0  # one search, before Student 1 read the message
    assert calls["prepare"][0]["memory_searched"] is True
    view = turn.view
    assert view.cognition.meta.status == "available" and view.cognition.interpretation.acts == ("small_talk",)
    assert view.cognition.conversational_action == Assessed("respond", "student_2", "say hello back")
    assert view.thinking.required is False and view.thinking.decider == "student_2" and view.mind.status == "disabled"
    assert view.memory.entries == (CacheEntry("event:4", "f" * 40, "current", content="died"),)
    assert view.memory.recall_status == "matched" and turn.searched
    assert view.conversation.note_refs[0].kind == "their_open_question"
    assert turn.decision.route == "fast_path" and turn.decision.outcome == "fast_path"
    assert turn.recall_rendered.endswith("died") and turn.cached_memory is None
    assert "Memory was searched for this message" in turn.boundary
    # What each consumer of the evidence was given: Student 1 the facts and episodes (none here), Student 2
    # nothing (its snapshot has no memory input), the mouth the whole bundle.
    consumers = turn.recall_turn.consumers
    assert consumers["student_1"]["refs"] == [] and consumers["student_2"]["refs"] == []
    assert "no memory input" in consumers["student_2"]["note"]
    assert turn.event()["type"] == "route" and turn.event()["eligibility"] == "confirmed"
    assert turn.immediate_action == "respond" and turn.immediate_origin == "student_2"
    assert "What to get across: hi there" in turn.decision_brief


def test_a_failed_student_makes_the_turn_visibly_degraded(stubs):
    settings, _, _ = stubs
    settings.student_fails = True
    turn = _prepare()
    assert turn.view.cognition.meta.status == "unavailable"
    assert turn.decision.outcome == "degraded" and "didn't work this turn" in turn.boundary
    assert turn.event()["degraded"] == ["cognition_unavailable"]


def test_the_trace_links_event_route_and_delivery_and_holds_no_message_text(stubs, tmp_path):
    settings, _, found = stubs
    turn = _prepare()
    found.tool_calls = 1  # she searched during the reply: the initial check wasn't enough
    reply_id = uuid.uuid4()
    shared_state.finish(turn, outcome="partial", reply="Juniper! I remember", assistant_message_id=reply_id,
                        heard_chars=4)
    raw = (tmp_path / "shared.jsonl").read_text()
    assert "Juniper" not in raw and "died" not in raw
    line = json.loads(raw)
    assert line["event"] == f"message:{MSG}" and line["route"]["route"] == "fast_path"
    assert line["route"]["eligibility"] == "confirmed" and line["route"]["outcome"] == "fast_path"
    assert line["dependencies"]["conversation_revision"] == "3" and "event:4#" in line["dependencies"]["memory_cache"]
    assert line["calls"] == {"fresh_recall_before_reply": 1, "mind_scheduled": 0, "student_2": 1,
                             "memory_tool_calls_by_mouth": 1}
    assert line["student_2"]["communication"] == "respond" and line["student_2"]["model"] == "test-student"
    assert line["mouth_escalated"] == ["searched_memory"]
    assert line["delivery"] == {"outcome": "partial", "assistant_message_id": str(reply_id), "reply_chars": 19,
                                "heard_chars": 4}
    assert line["sections"]["inner_state"] == "disabled" and line["sections"]["activities"] == "unavailable"


# --- Student 2: the decision ------------------------------------------------------------------------

DIALS = {"length": "a_little", "warmth": "somewhat", "playfulness": "none", "directness": "somewhat"}
SAY_HI = dict(intention="say hello back", work="no_change", communication="respond", what_to_communicate="hi there",
              move="react_briefly", dials=DIALS, about=["M"])


def _decided(recall, thinking, status="available"):
    meta = Projection("student_2", status, reason=None if status == "available" else "down")
    return (RecallDecision(meta, recall, "student_2" if recall else "not_assessed"),
            ThinkingDecision(meta, required={"required": True, "not_required": False}.get(thinking),
                             decider="student_2" if thinking else "none", choice=thinking))


@pytest.mark.parametrize("recall,thinking,expected,eligibility,searches", [
    ("not_required", "not_required", "fast_path", "confirmed", False),
    ("required", "not_required", "recall", "not_eligible", True),
    ("not_required", "required", "mind", "not_eligible", False),
    ("required", "required", "recall_then_mind", "not_eligible", False),  # its recall is background work
])
def test_each_decision_has_its_route(recall, thinking, expected, eligibility, searches):
    r, t = _decided(recall, thinking)
    decision = route(_view(recall=r, thinking=t))
    assert (decision.route, decision.eligibility, decision.runs_recall) == (expected, eligibility, searches)
    assert decision.degraded == () and decision.schedules_mind == ("mind" in expected)


def test_a_failed_decision_or_reading_is_never_a_confirmed_fast_path():
    r, t = _decided(None, None, status="unavailable")
    failed = route(_view(recall=r, thinking=t))
    assert failed.route == "unassessed" and "decision_unavailable" in failed.degraded
    assert {"recall_need_not_assessed", "thinking_need_not_assessed"} <= set(failed.limits)
    r, t = _decided("not_required", "not_required")
    no_reading = route(_view(recall=r, thinking=t, cognition=_cognition("unavailable")))
    assert no_reading.route == "unassessed" and no_reading.eligibility == "unassessed"


def test_the_student_reads_the_live_situation_in_its_training_shape(stubs):
    _, calls, _ = stubs
    _prepare()
    [text] = calls["student"]["texts"]
    assert text.startswith("TRIGGER: user message\nTIME: ")
    assert f"[M] NEW MESSAGE FROM THE USER (just now; nothing done about it yet): {SECRET}" in text
    assert "SETTING: just talking" in text and "write: write text for the user" in text
    assert not any(line in text for line in S.E.EXCHANGE)  # qwen-4 was trained before the exchange line


def test_silence_is_honoured_no_reply_and_no_second_search(stubs):
    _, calls, _ = stubs
    calls["student"]["answer"] = json.dumps(dict(intention="let it sit", work="request_recall", target="M",
                                                 communication="silent"))
    turn = _prepare()
    assert turn.silent and turn.immediate_origin == "student_2" and turn.decision_brief is None
    assert calls["fresh"] == 1  # the initial check ran before anyone decided; silence adds nothing


def test_recall_decided_after_the_initial_check_means_searching_further_not_the_same_search(stubs):
    _, calls, _ = stubs
    calls["student"]["answer"] = json.dumps(dict(SAY_HI, work="request_recall", target="M"))
    for status, words in (("matched", "What you recall"), ("no_match", "isn't proof it didn't happen")):
        calls["fresh"] = 0
        calls["searches"] = [_turn_recall(status, rendered=f"RENDERED {status}")]
        turn = _prepare()
        assert calls["fresh"] == 1 and turn.decision.route == "recall" and turn.searched
        assert turn.recall_rendered == f"RENDERED {status}" and turn.cached_memory is None
        assert words in turn.boundary and turn.view.memory.recall_status == status
        assert "additional_recall_requested" in turn.decision.limits and "memory tools" in turn.boundary
        assert "look up your memory about their new message" in turn.decision_brief


def test_a_failed_initial_check_rereads_the_cache_and_the_recall_route_tries_again(stubs):
    _, calls, _ = stubs
    # Nobody asked for recall: the cache stands in, and the turn says the search failed.
    calls["searches"] = [_turn_recall("failed")]
    turn = _prepare()
    assert calls["fresh"] == 1 and calls["cache"] == 1 and calls["prepare"][-1]["memory_searched"] is False
    assert turn.view.memory.recall_status == "failed" and not turn.searched
    assert turn.cached_memory.endswith("died") and "search FAILED" in turn.boundary
    assert "1 memories from your last answer" in turn.boundary
    # Student 2 asked for recall: one more try, and what it found replaces the cache.
    calls["student"]["answer"] = json.dumps(dict(SAY_HI, work="request_recall", target="M"))
    calls["fresh"] = 0
    calls["searches"] = [_turn_recall("failed"), _turn_recall("matched", rendered="RENDERED again")]
    retried = _prepare()
    assert calls["fresh"] == 2 and retried.searched and retried.recall_rendered == "RENDERED again"
    assert retried.view.memory.recall_status == "matched" and "What you recall" in retried.boundary
    # It fails again: degraded, and still never "nothing found".
    calls["fresh"] = 0
    calls["searches"] = [_turn_recall("failed")]
    failed = _prepare()
    assert calls["fresh"] == 2 and "recall_unavailable" in failed.decision.degraded
    assert "search FAILED" in failed.boundary and "nothing that matches" not in failed.boundary


def test_thinking_decided_schedules_the_mind_with_what_to_think_about(stubs, monkeypatch):
    _, calls, _ = stubs
    from app.services import coordinator
    scheduled, immediate = [], []
    monkeypatch.setattr(coordinator, "schedule", lambda **k: scheduled.append(k) or (
        shared_state.PendingWork("w1", "mind", "queued", "s", "the considered answer"),))
    monkeypatch.setattr(coordinator, "decide_immediate", lambda **k: immediate.append(k["decided"]))
    calls["student"]["answer"] = json.dumps(dict(
        SAY_HI, communication="acknowledge", what_to_communicate="let me think", work="request_thinking",
        target="M", think_about="which train", think_form="whether"))
    turn = _prepare()
    assert turn.decision.route == "mind" and scheduled[0]["work_input"]["think_about"] == "which train"
    assert immediate[0].action == "acknowledge" and immediate[0].origin == "student_2"
    assert turn.immediate_action == "acknowledge" and turn.pending


def test_an_invalid_or_unreachable_student_is_a_degraded_turn_that_still_answers(stubs, tmp_path):
    _, calls, _ = stubs
    calls["student"]["answer"] = json.dumps(dict(SAY_HI, work="pause", target="G7"))  # no G7 here
    invalid = _prepare()
    assert invalid.student.status == "invalid" and invalid.student.problems
    assert "decision_unavailable" in invalid.decision.degraded and invalid.immediate_action == "respond"
    assert invalid.immediate_origin == "operational_fallback" and invalid.decision_brief is None
    calls["student"]["down"] = True
    down = _prepare()
    assert down.student.status == "unavailable" and "TimeoutError" in down.student.error
    assert "decision_unavailable" in down.decision.degraded and calls["fresh"] == 2  # one initial check per turn
    shared_state.finish(down, outcome="completed", reply="ok", assistant_message_id=uuid.uuid4())
    line = json.loads((tmp_path / "shared.jsonl").read_text().splitlines()[-1])
    assert line["student_2"]["status"] == "unavailable" and line["calls"]["student_2"] == 0
    assert SECRET not in (tmp_path / "shared.jsonl").read_text()


def test_the_mouth_hears_her_decision():
    ctx = AgentRuntimeContext(user_id=USER, decision="- What you want here: say hello back")
    prompt = build_system_prompt(ctx)
    assert "## What you decided" in prompt and "say hello back" in prompt
    assert "## What you decided" not in build_system_prompt(AgentRuntimeContext(user_id=USER))
