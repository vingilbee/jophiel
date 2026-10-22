"""The shared-state fast path (docs/SHARED_STATE_REFACTOR_SPEC.md step 3): user message -> scoped view ->
cognition -> mouth. Routing and prompt are pure; the assembly runs with stub adapters; the cache's reread
against memory is in ``test_shared_state_cache.py`` (real PostgreSQL). No model calls."""
from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest

from app.ai.context import AgentRuntimeContext
from app.ai.prompts import build_system_prompt
from app.ai.state.contracts import (
    Assessed, CacheEntry, CognitionView, RecallDecision, ConversationView, EvidenceAssessment, Interpretation, MemoryContextView,
    MissingEvidence, OutputView, Projection, SharedView, ThinkingDecision, WorkView, Envelope, SCHEMA_VERSION,
)
from app.ai.state.routing import route
from app.services import cognitive_live, recall_references, shared_state

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
        recall=recall or RecallDecision(Projection.off("jev", "off")),
        envelope=Envelope(SCHEMA_VERSION, USER, CONV, 3, datetime.now(timezone.utc), f"message:{MSG}"),
        personality=Projection.off("character", "off"),
        conversation=ConversationView(Projection("chat", "available"), CONV, 3, MSG),
        memory=memory or MemoryContextView(Projection("memory", "available")),
        cognition=cognition or _cognition(interpretation=_interpretation()),
        thinking=thinking or ThinkingDecision(Projection.off("jev", "step 4")),
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
    no_thinking = ThinkingDecision(Projection("jev", "available"), required=False, decider="jev", reason="trivial")
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
    think = ThinkingDecision(Projection("jev", "available"), required=True, decider="jev", reason="hard")
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
    no_thinking = ThinkingDecision(Projection("jev", "available"), required=False, decider="jev", reason="trivial")
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


def test_the_fast_path_prompt_says_no_search_ran_and_replaces_the_recall_block():
    ctx = AgentRuntimeContext(user_id=USER, recall="SHOULD NOT SHOW", cached_memory="- [event:4] died",
                              turn_boundary="- Memory was not searched for this message.")
    prompt = build_system_prompt(ctx)
    assert "no memory search ran for this message" in prompt and "- [event:4] died" in prompt
    assert "What you had for this message" in prompt and "SHOULD NOT SHOW" not in prompt
    plain = build_system_prompt(AgentRuntimeContext(user_id=USER, recall="RECALLED"))
    assert "RECALLED" in plain and "What you had for this message" not in plain


def test_typed_memory_never_says_nothing_relevant_when_nothing_was_looked_up():
    assert "nothing relevant" in cognitive_live.typed_memory_text(None)
    unsearched = cognitive_live.typed_memory_text(None, searched=False)
    assert "nothing relevant" not in unsearched and "not looked up for this message" in unsearched


# --- assembly with stub adapters ----------------------------------------------------------------------


def _settings(tmp_path, **over):
    base = dict(shared_state_pipeline_enabled=True, shared_state_log_path=str(tmp_path / "shared.jsonl"),
                cognitive_live_student="ab", cognitive_live_character=False, cognitive_live_student_dir=None,
                recall_enabled=True, mind_enabled=False, mind_inner_state_enabled=True,
                memory_manager_enabled=False, recall_tool_calls_per_reply=3)
    return SimpleNamespace(**(base | over))


@pytest.fixture
def stubs(monkeypatch, tmp_path):
    calls = {"prepare": [], "fresh": 0}
    settings = _settings(tmp_path)
    monkeypatch.setattr(shared_state, "get_settings", lambda: settings)
    shared_state._personality.cache_clear()
    monkeypatch.setattr(shared_state, "_conversation", lambda u, c, t: ConversationView(
        Projection("chat", "available", version="3"), c, 3, t))
    record = SimpleNamespace(ref="event:4", fingerprint="f" * 40, applicability="current", corrected=False,
                             content="died")
    cached = SimpleNamespace(result=SimpleNamespace(status="matched", records=[record], coverage={}),
                             now=None, rendered="Memories from your last answer, re-read now:\n- [event:4] died",
                             legacy=None, tool_calls=0)
    monkeypatch.setattr(recall_references, "cached_for_reply", lambda *a, **k: cached)
    monkeypatch.setattr(recall_references, "enabled", lambda: settings.recall_enabled)

    def fresh(*a, **k):
        calls["fresh"] += 1
        raise AssertionError("the fast path never searches before the reply")

    monkeypatch.setattr(recall_references, "for_reply", fresh)
    monkeypatch.setattr(cognitive_live, "enabled", lambda: settings.cognitive_live_student != "off")

    def prepare(**kwargs):
        calls["prepare"].append(kwargs)
        if settings.cognitive_live_student == "off":
            return None
        return cognitive_live.LivePrep(arm="page_only", interpretation=dict(
            acts={"small_talk": 0.9}, tone={"friendly": 0.8}, request={"no_request": 0.9},
            understand={"acts": {"small_talk": True, "asking": False}, "tone": "friendly", "request": "no_request"}),
            state={"notes": [{"kind": "their_open_question", "opened_turn": 2}]})

    monkeypatch.setattr(cognitive_live, "prepare", prepare)
    # No database here: the coordinator's supersession and scheduling are covered in test_coordinator.py.
    from app.services import coordinator
    monkeypatch.setattr(coordinator, "supersede_older", lambda *a, **k: 0)
    monkeypatch.setattr(coordinator, "schedule", lambda **k: None)
    return settings, calls, cached


def _prepare(**over):
    return shared_state.prepare(**({"user_id": USER, "conversation_id": CONV, "user_message_id": MSG,
                                    "history": [{"role": "user", "content": SECRET}], "message": SECRET,
                                    "zone": "UTC", "turn_id": uuid.uuid4(), "reply_message_id": uuid.uuid4()}
                                   | over))


def test_the_view_is_built_from_conversation_cache_and_cognition_without_a_search(stubs):
    settings, calls, _ = stubs
    turn = _prepare()
    assert calls["fresh"] == 0
    assert calls["prepare"][0]["memory_searched"] is False
    view = turn.view
    # Cognition runs in every arm: the page_only arm hides the reading's words, not the routing.
    assert view.cognition.meta.status == "available" and view.cognition.interpretation.acts == ("small_talk",)
    assert view.cognition.conversational_action.origin == "policy"
    assert view.cognition.missing_evidence.assessment == "not_assessed"
    assert view.thinking.required is None and view.mind.status == "disabled"
    assert view.memory.entries == (CacheEntry("event:4", "f" * 40, "current", content="died"),)
    assert view.conversation.note_refs[0].kind == "their_open_question"
    assert turn.decision.route == "unassessed" and turn.decision.outcome == "unassessed"
    assert turn.cached_memory.endswith("died") and "1 memories from your last answer" in turn.boundary
    assert turn.event()["type"] == "route" and turn.event()["eligibility"] == "unassessed"


def test_student_off_makes_every_turn_visibly_degraded(stubs):
    settings, _, _ = stubs
    settings.cognitive_live_student = "off"
    turn = _prepare()
    assert turn.view.cognition.meta.status == "disabled"
    assert turn.decision.outcome == "degraded" and "didn't work this turn" in turn.boundary
    assert turn.event()["degraded"] == ["cognition_unavailable"]


def test_recall_off_means_no_cache_and_the_mouth_is_told(stubs):
    settings, _, _ = stubs
    settings.recall_enabled = False
    turn = _prepare()
    assert turn.view.memory.meta.status == "disabled" and turn.recall_turn is None
    assert "recent conversation only" in turn.boundary


def test_the_trace_links_event_route_and_delivery_and_holds_no_message_text(stubs, tmp_path):
    settings, _, cached = stubs
    turn = _prepare()
    cached.tool_calls = 1  # she searched during the reply: the fast path's memory wasn't enough
    reply_id = uuid.uuid4()
    shared_state.finish(turn, outcome="partial", reply="Juniper! I remember", assistant_message_id=reply_id,
                        heard_chars=4)
    raw = (tmp_path / "shared.jsonl").read_text()
    assert "Juniper" not in raw and "died" not in raw
    line = json.loads(raw)
    assert line["event"] == f"message:{MSG}" and line["route"]["route"] == "unassessed"
    assert line["route"]["eligibility"] == "unassessed" and line["route"]["outcome"] == "unassessed"
    assert line["dependencies"]["conversation_revision"] == "3" and "event:4#" in line["dependencies"]["memory_cache"]
    assert line["calls"] == {"fresh_recall_before_reply": 0, "mind_scheduled": 0, "jev": 0,
                             "memory_tool_calls_by_mouth": 1}
    assert line["mouth_escalated"] == ["searched_memory"]
    assert line["delivery"] == {"outcome": "partial", "assistant_message_id": str(reply_id), "reply_chars": 19,
                                "heard_chars": 4}
    assert line["sections"]["inner_state"] == "disabled" and line["sections"]["activities"] == "unavailable"


def test_the_worker_starts_no_memory_search_on_the_fast_path(monkeypatch):
    from app.workers import chat as worker
    monkeypatch.setattr(shared_state, "enabled", lambda: True)
    monkeypatch.setattr(recall_references, "for_reply", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    monkeypatch.setattr(worker.memory_service, "retrieve_for_prompt",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    monkeypatch.setattr(worker.cognitive_shadow, "capture_enabled", lambda: False)
    job = SimpleNamespace(attempts=1, user_message_id=MSG, user_id=USER, conversation_id=CONV, turn_id=uuid.uuid4(),
                          id=uuid.uuid4())

    async def run():
        return await asyncio.gather(*worker._start_setup_tasks(job, {"content": "hi"}))

    assert asyncio.run(run()) == [None, None]


# --- step 4: Jev's two answers ------------------------------------------------------------------------

from app.ai.state import assessment as jev_contract  # noqa: E402
from app.services import jev_assessment  # noqa: E402


def _jev(recall, thinking, status="available"):
    meta = Projection("jev", status, reason=None if status == "available" else "HTTP 503")
    return (RecallDecision(meta, recall, "jev" if recall else "not_assessed"),
            ThinkingDecision(meta, required={"required": True, "not_required": False}.get(thinking),
                             decider="jev" if thinking else "none", choice=thinking))


@pytest.mark.parametrize("recall,thinking,expected,eligibility,searches", [
    ("not_required", "not_required", "fast_path", "confirmed", False),
    ("required", "not_required", "recall", "not_eligible", True),
    ("uncertain", "not_required", "unassessed", "unassessed", False),
    ("not_required", "uncertain", "unassessed", "unassessed", False),
    ("not_required", "required", "mind", "not_eligible", False),
    ("required", "required", "recall_then_mind", "not_eligible", False),  # its recall is background work
])
def test_each_jev_answer_has_its_route(recall, thinking, expected, eligibility, searches):
    r, t = _jev(recall, thinking)
    decision = route(_view(recall=r, thinking=t))
    assert (decision.route, decision.eligibility, decision.runs_recall) == (expected, eligibility, searches)
    assert decision.recall == recall and decision.thinking == thinking
    assert decision.degraded == () and decision.schedules_mind == ("mind" in expected)
    if recall == "uncertain":
        assert "recall_need_uncertain" in decision.limits  # uncertain is not "not required", and starts no search


def test_jev_decides_processing_needs_only_never_clarification_or_tools():
    from app.ai.state.routing import Route
    assert set(Route.__args__) == {"fast_path", "unassessed", "recall", "mind", "recall_then_mind"}
    assert jev_contract.RECALL == jev_contract.THINKING == ("not_required", "required", "uncertain")


def test_a_failed_assessment_or_cognition_is_never_a_confirmed_fast_path():
    r, t = _jev(None, None, status="unavailable")
    failed = route(_view(recall=r, thinking=t))
    assert failed.route == "unassessed" and "assessment_unavailable" in failed.degraded
    assert {"recall_need_not_assessed", "thinking_need_not_assessed"} <= set(failed.limits)
    r, t = _jev("not_required", "not_required")
    no_reading = route(_view(recall=r, thinking=t, cognition=_cognition("unavailable")))
    assert no_reading.route == "unassessed" and no_reading.eligibility == "unassessed"


def test_the_request_carries_both_questions_and_a_bad_answer_stays_unassessed():
    body = jev_contract.body("jev-latest", jev_contract.state(
        message="hi", recent=[{"role": "user", "content": "x" * 5000}], interpretation=None,
        evidence_lines=[], capabilities=["conversation"]))
    assert set(body["questions"]) == {"recall", "thinking"}
    assert set(body["questions"]["recall"]["criteria"]) == set(jev_contract.RECALL)
    assert len(body["state"]["recent_conversation"][0]["text"]) <= jev_contract.MESSAGE_CHARS
    good = jev_contract.parse({"model": "jev-1.14.0", "answers": {
        "recall": {"choice": "required", "probabilities": {"required": 0.9}},
        "thinking": {"choice": "not_required"}}}, trigger_ref="m", input_versions={}, provider="t", latency_ms=60)
    assert (good.status, good.recall, good.thinking, good.model) == ("ok", "required", "not_required", "jev-1.14.0")
    # A choice Jev no longer has (v0's clarification) is an unusable answer, never mapped to a route.
    bad = jev_contract.parse({"answers": {"recall": {"choice": "clarification_needed"},
                                          "thinking": {"choice": "required"}}},
                             trigger_ref="m", input_versions={}, provider="t", latency_ms=60)
    assert bad.status == "invalid" and bad.recall is None and bad.thinking == "required"


def _jev_settings(monkeypatch, settings):
    jev = SimpleNamespace(shared_state_assessment_enabled=True, typesafe_api_key="k",
                          typesafe_base_url="https://jev.test/v1", shared_state_assessment_model="jev-latest",
                          shared_state_assessment_timeout_seconds=1.5)
    monkeypatch.setattr(jev_assessment, "get_settings", lambda: jev)


def _answer(recall, thinking):
    def post(url, body):
        assert url == "https://jev.test/v1/systemone" and body["state"]["latest_user_message"] == SECRET
        return {"model": "jev-1.14.0", "answers": {"recall": {"choice": recall}, "thinking": {"choice": thinking}}}
    return post


def test_a_greeting_needing_neither_is_a_confirmed_fast_path_without_a_search(stubs, monkeypatch):
    settings, calls, _ = stubs
    _jev_settings(monkeypatch, settings)
    real = jev_assessment.assess
    monkeypatch.setattr(jev_assessment, "assess", lambda **k: real(**k, post=_answer("not_required", "not_required")))
    turn = _prepare()
    assert turn.decision.route == "fast_path" and turn.decision.eligibility == "confirmed"
    assert calls["fresh"] == 0 and not turn.searched and turn.recall_rendered is None
    assert "Nobody checked" not in turn.boundary and "Memory was not searched" in turn.boundary


@pytest.mark.parametrize("kind", ["fact", "episode", "event", "item"])
def test_cached_content_reaches_the_actual_jev_request_without_a_recall(stubs, monkeypatch, kind):
    from datetime import datetime, timezone

    from app.ai.recall.contracts import MemoryRecord, MemoryRef
    from app.services import recall

    settings, calls, cached = stubs
    _jev_settings(monkeypatch, settings)
    fact = ("The Cobalt reading circle meets every Thursday at 7 pm in the Glass Pavilion; "
            "bring the blue atlas to its next meeting.")
    now = datetime.now(timezone.utc)
    record = MemoryRecord(
        ref=MemoryRef(kind, "4" if kind == "event" else str(uuid.uuid4())), subkind=kind,
        content=fact, context_id=None, context_label=None, occurred_at=None, occurred_until=None,
        scheduled_at=None, recorded_at=now, updated_at=None, status="active", epistemic="reported",
        evidence=None, fingerprint="f" * 40, matched=("direct_reference",), applicability="current")
    cached.result.records = [record]
    cached.rendered = "Memories from your last answer, re-read now:\n" + "\n".join(
        recall.render_records([record], now=now, zone="America/Vancouver"))
    # Production rendering keeps the answer off the bullet header. It is also absent from
    # Jev's bounded recent conversation, reproducing the frontend cache-probe failure.
    assert fact not in next(line for line in cached.rendered.splitlines() if line.startswith("- "))
    captured = []

    def post(url, body):
        captured.append(body["state"])
        assert body["state"]["memories_supplied"] == [f"[{record.ref}] (current) {fact}"]
        assert all("Thursday" not in m["text"] for m in body["state"]["recent_conversation"])
        return {"answers": {"recall": {"choice": "not_required"},
                            "thinking": {"choice": "not_required"}}}

    real = jev_assessment.assess
    monkeypatch.setattr(jev_assessment, "assess", lambda **k: real(**k, post=post))
    turn = _prepare(message="What day, time and place is my Cobalt reading circle?", history=[
        {"role": "assistant", "content": "rain " * 200 + fact}])
    assert len(captured) == 1 and turn.decision.route == "fast_path"
    assert calls["fresh"] == 0 and not turn.searched


def test_jev_memory_content_is_bounded_and_retains_applicability_and_corrections(stubs, monkeypatch):
    settings, _, cached = stubs
    _jev_settings(monkeypatch, settings)
    cached.result.records[0].content = "changed fact " * 1000
    cached.result.records[0].applicability = "no_longer_current"
    cached.result.records[0].corrected = True
    captured = []
    real = jev_assessment.assess

    def post(url, body):
        captured.append(body["state"]["memories_supplied"])
        return {"answers": {"recall": {"choice": "uncertain"}, "thinking": {"choice": "not_required"}}}

    monkeypatch.setattr(jev_assessment, "assess", lambda **k: real(**k, post=post))
    _prepare()
    [evidence] = captured[0]
    assert evidence.startswith("[event:4] (no_longer_current; corrected by the user) changed fact")
    assert len(evidence) == jev_contract.EVIDENCE_CHARS and evidence.endswith("…")
    bounded = jev_contract.state(message="hi", recent=[], interpretation=None,
                                 evidence_lines=[evidence] * 100, capabilities=[])
    assert len(bounded["memories_supplied"]) == jev_contract.EVIDENCE_LINES
    cached.result.records = []
    _prepare()
    assert captured[1] == "none"  # stale rendered text cannot resurrect a removed fact


def test_recall_needed_searches_memory_before_the_mouth_and_says_what_it_found(stubs, monkeypatch):
    settings, calls, _ = stubs
    _jev_settings(monkeypatch, settings)
    real = jev_assessment.assess
    monkeypatch.setattr(jev_assessment, "assess", lambda **k: real(**k, post=_answer("required", "not_required")))
    for status, words in (("matched", "What you recall"), ("no_match", "isn't proof it didn't happen"),
                          ("failed", "search FAILED")):
        found = SimpleNamespace(result=SimpleNamespace(status=status, records=[], coverage={}),
                                rendered=f"RENDERED {status}", tool_calls=0)
        monkeypatch.setattr(recall_references, "for_reply", lambda *a, **k: found)
        turn = _prepare()
        assert turn.decision.route == "recall" and turn.searched
        assert turn.recall_rendered == f"RENDERED {status}" and turn.cached_memory is None
        assert words in turn.boundary and turn.view.memory.recall_status == status


def test_recall_needed_without_shared_recall_is_degraded_not_answered_as_known(stubs, monkeypatch):
    settings, _, _ = stubs
    settings.recall_enabled = False
    _jev_settings(monkeypatch, settings)
    real = jev_assessment.assess
    monkeypatch.setattr(jev_assessment, "assess", lambda **k: real(**k, post=_answer("required", "not_required")))
    turn = _prepare()
    assert turn.decision.route == "recall" and "recall_unavailable" in turn.decision.degraded
    assert "can't check that right now" in turn.boundary


def test_an_unreachable_assessor_keeps_the_turn_unassessed_and_degraded(stubs, monkeypatch, tmp_path):
    settings, calls, _ = stubs
    _jev_settings(monkeypatch, settings)

    def down(url, body):
        raise TimeoutError("read timeout")

    real = jev_assessment.assess
    monkeypatch.setattr(jev_assessment, "assess", lambda **k: real(**k, post=down))
    turn = _prepare()
    assert turn.assessment.status == "error" and turn.decision.route == "unassessed"
    assert "assessment_unavailable" in turn.decision.degraded and calls["fresh"] == 0
    shared_state.finish(turn, outcome="completed", reply="ok", assistant_message_id=uuid.uuid4())
    line = json.loads((tmp_path / "shared.jsonl").read_text().splitlines()[-1])
    assert line["assessment"]["status"] == "error" and line["calls"]["jev"] == 1
    assert SECRET not in (tmp_path / "shared.jsonl").read_text()
