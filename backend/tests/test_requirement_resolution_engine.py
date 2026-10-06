"""Resolver families of Typed Requirement Resolution V0.

Each family is exercised for every status it can emit, from typed snapshots
only.  Inputs are real pipeline outputs (live-shadow compiler + pipeline);
individual facts are varied on the typed records the engine reads, never on
text.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.ai.Cognitive.system.v0.contracts.interpretation import InterpretationEnvelope
from app.ai.Cognitive.system.v0.contracts.requirement_resolution import (
    RequirementKind as K, RequirementReason as R, RequirementResolutionInputs, RequirementResolutionStatus as S,
)
from app.ai.Cognitive.system.v0.contracts.safety import (
    ApplicationSafetyDecision, SafetyDecisionProvenance, SafetyDisposition,
)
from app.ai.Cognitive.system.v0.interpretation_v0.models import (
    InterpretationDialogueContext, InterpretationDialogueItem, InterpretationDialogueReferent,
)
from app.ai.Cognitive.system.v0.producers_v0 import (
    ApplicationIdentityProducer, ProducerRecord, QueryKind, captured_runtime_producer, delivered_claim_producer,
)
from app.ai.Cognitive.system.v0.requirement_resolution_v0 import (
    RequirementResolutionEngine, RequirementResolutionRequest, goal_frames, runtime_date_record,
)
from app.ai.Cognitive.system.v0.requirement_resolution_v0.engine import frame_focus_spans
from app.ai.Cognitive.system.v0.requirement_resolution_v0.manifest import (
    TIER_CAPABILITY, TIER_CONTENT, TIER_EXECUTION, TIER_SAFETY, TIER_USER_INPUT, compile_content_query,
)
from cognitive_shadow_support import make_capture
from requirement_resolution_support import AMBIGUOUS, DATE, ELABORATION, IDENTITY, REMINDER, UNSAFE, run_turn

TURN_TIME = "2026-09-30T06:30:00+00:00"


def _request(trace, *, interpretation: InterpretationEnvelope | None = None, runtime: dict | None = None,
             dialogue: InterpretationDialogueContext | None = None, dialogue_refs: tuple[str, ...] = (),
             inputs: RequirementResolutionInputs | None = "default") -> RequirementResolutionRequest:
    action_runtime = trace.cognitive_turn.input_snapshot.action_runtime
    if runtime:
        action_runtime = type(action_runtime).model_validate({**action_runtime.model_dump(mode="python"), **runtime})
    return RequirementResolutionRequest(
        cognitive_turn_id=trace.cognitive_turn_id, conversation_ref=trace.conversation_ref,
        interpretation=interpretation or trace.interpretation, goal_snapshot=trace.goal_snapshot,
        runtime=action_runtime, dialogue_context=dialogue, dialogue_action_refs=dialogue_refs,
        inputs=(RequirementResolutionInputs(turn_time=TURN_TIME, user_ref="user-a") if inputs == "default" else inputs),
    )


def _only(snapshot, kind: K):
    [item] = [item for item in snapshot.resolutions if item.kind == kind]
    return item


def _decision(runtime_snapshot_ref: str, disposition: SafetyDisposition, *, policy="application-safety-policy-v0.1",
              provenance=SafetyDecisionProvenance.APPLICATION_SAFETY_POLICY) -> ApplicationSafetyDecision:
    return ApplicationSafetyDecision(decision_id="decision-test", policy_version=policy, disposition=disposition,
                                     provenance=provenance, producer_ref="test-policy", runtime_snapshot_ref=runtime_snapshot_ref)


# ---------------------------------------------------------------------------
# Goal tracing
# ---------------------------------------------------------------------------

def test_every_request_goal_traces_to_exactly_the_frame_it_was_formed_from() -> None:
    for text, frames in (("What is your name?", IDENTITY), ("Can you check it for me?", AMBIGUOUS)):
        trace = run_turn(text, frames)
        [goal_ref] = trace.requirement_resolution.handoff.snapshot.goal_refs
        record = next(item for item in trace.goal_snapshot.records if item.goal_id == goal_ref)
        [frame] = goal_frames(record, trace.interpretation, task_ref=None)
        assert frame.predicate == frames[0][0]
        assert {item.frame_ref for item in trace.requirement_resolution.handoff.snapshot.resolutions} == {frame.frame_id}


def test_only_active_current_turn_user_request_goals_are_resolved() -> None:
    trace = run_turn("Hello there!", (("social_opening", {}),))
    assert trace.requirement_resolution.handoff.snapshot.goal_refs == ()
    assert trace.requirement_resolution.grounded_objects == ()


# ---------------------------------------------------------------------------
# Required arguments and dialogue referents
# ---------------------------------------------------------------------------

def test_argument_present_and_missing() -> None:
    reminder = run_turn("Create a reminder to buy oat milk tomorrow at 9am.", REMINDER)
    effect = next(item for item in reminder.requirement_resolution.handoff.snapshot.resolutions
                  if item.argument_name == "requested_effect")
    assert (effect.kind, effect.status, effect.reason_code, effect.blocking) == (
        K.REQUIRED_ARGUMENT, S.SATISFIED, R.ARGUMENT_PRESENT, False)
    ambiguous = run_turn("Can you check it for me?", AMBIGUOUS)
    subject = next(item for item in ambiguous.requirement_resolution.handoff.snapshot.resolutions
                   if item.argument_name == "subject_ref")
    assert (subject.kind, subject.status, subject.reason_code, subject.blocking, subject.tier) == (
        K.DIALOGUE_REFERENT, S.MISSING_USER_INPUT, R.REFERENT_UNRESOLVED, True, TIER_USER_INPUT)
    frame = next(item for item in ambiguous.interpretation.semantic_frames if item.predicate == "action_request")
    assert f"{frame.frame_id}#subject_ref" in subject.subject_refs
    unknown = next(item.unknown_id for item in ambiguous.interpretation.unknowns if item.key == "subject_ref")
    assert unknown in subject.subject_refs


def _dialogue(trace, frame, *, resolution: str, resolved=(), candidates=()) -> InterpretationDialogueContext:
    [(start, end)] = frame_focus_spans(frame, trace.interpretation)
    items = tuple(InterpretationDialogueItem(item_ref=ref, item_kind="entity", speaker="user",
                                             epistemic_status="attributed", surface_anchor="the report",
                                             source_message_refs=(trace.focus_message_id,))
                  for ref in sorted({*resolved, *candidates}))
    return InterpretationDialogueContext(
        context_ref="dialogue-context-test", projection_version="dialogue-working-memory-projection-v0.1",
        conversation_ref=trace.conversation_ref, focus_message_ref=trace.focus_message_id,
        source_snapshot_ref="snapshot-test", source_snapshot_digest="0" * 64, items=items,
        referents=(InterpretationDialogueReferent(
            cue_ref="dialogue-cue:test:1-3:pronoun_singular", cue_kind="pronoun_singular", surface_anchor="it",
            start=start, end=min(end, start + 2) if end - start >= 2 else end, resolution=resolution,
            resolved_refs=tuple(resolved), candidate_refs=tuple(candidates),
        ),),
    )


@pytest.mark.parametrize("resolution, resolved, candidates, status, reason", [
    ("resolved", ("dialogue-item-report",), (), S.SATISFIED, R.REFERENT_RESOLVED_FROM_DIALOGUE),
    ("ambiguous", (), ("dialogue-item-a", "dialogue-item-b"), S.MISSING_USER_INPUT, R.REFERENT_AMBIGUOUS),
    ("no_candidate", (), (), S.MISSING_USER_INPUT, R.REFERENT_UNRESOLVED),
])
def test_dialogue_referents_resolve_only_from_the_authorized_dialogue_context(
    resolution, resolved, candidates, status, reason,
) -> None:
    trace = run_turn("Can you check it for me?", AMBIGUOUS)
    frame = next(item for item in trace.interpretation.semantic_frames if item.predicate == "action_request")
    context = _dialogue(trace, frame, resolution=resolution, resolved=resolved, candidates=candidates)
    outcome = RequirementResolutionEngine().resolve(_request(trace, dialogue=context))
    subject = next(item for item in outcome.snapshot.resolutions if item.argument_name == "subject_ref")
    assert (subject.status, subject.reason_code) == (status, reason)
    if status == S.SATISFIED:
        assert subject.value_refs == resolved and not subject.blocking
    else:
        assert subject.blocking and set(candidates) <= set(subject.value_refs)


def test_a_cue_outside_the_frame_evidence_is_never_a_referent() -> None:
    trace = run_turn("Can you check it for me?", AMBIGUOUS)
    frame = next(item for item in trace.interpretation.semantic_frames if item.predicate == "action_request")
    context = _dialogue(trace, frame, resolution="resolved", resolved=("dialogue-item-report",))
    moved = context.model_copy(update={"referents": (context.referents[0].model_copy(update={"start": 900, "end": 902}),)})
    subject = next(item for item in RequirementResolutionEngine().resolve(_request(trace, dialogue=moved)).snapshot.resolutions
                   if item.argument_name == "subject_ref")
    assert subject.status == S.MISSING_USER_INPUT
    # And no dialogue context at all never invents one from history.
    assert next(item for item in RequirementResolutionEngine().resolve(_request(trace)).snapshot.resolutions
                if item.argument_name == "subject_ref").reason_code == R.REFERENT_UNRESOLVED


# ---------------------------------------------------------------------------
# Trusted content
# ---------------------------------------------------------------------------

def _runtime_factory(records):
    def factory(inputs, turn_time):
        return (ApplicationIdentityProducer(), captured_runtime_producer(records, turn_time=turn_time))
    return factory


def test_identity_is_known_from_the_reviewed_application_identity() -> None:
    trace = run_turn("What is your name?", IDENTITY)
    outcome = RequirementResolutionEngine().resolve(_request(trace))
    content = _only(outcome.snapshot, K.TRUSTED_CONTENT)
    assert (content.status, content.reason_code, content.blocking, content.tier) == (
        S.SATISFIED, R.PRODUCER_KNOWN, False, TIER_CONTENT)
    [record] = outcome.content_records
    assert record.query.query_kind == QueryKind.ASSISTANT_NAME.value
    assert [fact.value for fact in record.result.facts] == ["Columbina"]


def test_the_runtime_date_is_the_captured_turn_date_in_the_captured_or_utc_zone() -> None:
    trace = run_turn("What is today's date?", DATE)
    utc = RequirementResolutionEngine().resolve(_request(trace)).content_records[0]
    assert [fact.value for fact in utc.result.facts] == ["2026-09-30"] and utc.query.subject_anchor == "Today's date in UTC"
    [fact] = utc.result.facts
    assert fact.observed_at == TURN_TIME and fact.source_ref.endswith("UTC:" + TURN_TIME)
    local = RequirementResolutionEngine().resolve(_request(trace, inputs=RequirementResolutionInputs(
        turn_time=TURN_TIME, time_zone="America/Vancouver", user_ref="user-a"))).content_records[0]
    # 06:30 UTC is still the previous day in Vancouver; the zone decides, never a model.
    assert [fact.value for fact in local.result.facts] == ["2026-09-29"] and local.query.subject_anchor == "Today's date"
    none = RequirementResolutionEngine().resolve(_request(trace, inputs=None))
    content = _only(none.snapshot, K.TRUSTED_CONTENT)
    assert (content.status, content.reason_code) == (S.MISSING_GROUNDING, R.PRODUCER_NOT_INSTALLED)


def _date_record(**overrides) -> ProducerRecord:
    record = runtime_date_record(datetime.fromisoformat(TURN_TIME), None)
    return record.model_copy(update=overrides)


@pytest.mark.parametrize("records, status, reason", [
    ((), S.MISSING_GROUNDING, R.PRODUCER_NO_MATCHING_RECORD),
    ((_date_record(observed_at="2026-09-30T06:00:00+00:00"),), S.MISSING_GROUNDING, R.PRODUCER_STALE_OBSERVATION),
    ((_date_record(authorized=False),), S.UNAUTHORIZED, R.PRODUCER_NOT_AUTHORIZED),
    ((_date_record(record_id="a"), _date_record(record_id="b", value="2026-10-01", value_anchor="2026-10-01")),
     S.CONFLICTING, R.PRODUCER_CONFLICTING_VALUES),
])
def test_every_producer_outcome_maps_to_its_typed_content_status(records, status, reason) -> None:
    trace = run_turn("What is today's date?", DATE)
    outcome = RequirementResolutionEngine(producer_factory=_runtime_factory(records)).resolve(_request(trace))
    content = _only(outcome.snapshot, K.TRUSTED_CONTENT)
    assert (content.status, content.reason_code, content.blocking) == (status, reason, True)
    [record] = outcome.content_records
    # An explicit gap only for a missing answer; withheld or conflicting content has none.
    assert (record.gap is not None) == (status == S.MISSING_GROUNDING)
    assert record.result.facts == ()


def test_unsupported_and_review_gated_queries_are_explicit_gaps_never_model_knowledge() -> None:
    info = run_turn("How much protein is in a cup of cooked chickpeas?",
                    (("information_request", {"topic_ref": "protein in a cup of cooked chickpeas", "question_form": "wh"}),))
    content = _only(info.requirement_resolution.handoff.snapshot, K.TRUSTED_CONTENT)
    assert (content.status, content.reason_code) == (S.MISSING_GROUNDING, R.PRODUCER_QUERY_UNSUPPORTED)
    # A registered but unreviewed producer class stays unavailable without an explicit offline opt-in.
    compiled = compile_content_query("elaboration_request", {"elaboration_kind": "example"}, time_zone=None,
                                     referent_item_refs=("dialogue-item-1",))
    assert (compiled.query_kind, dict(compiled.arguments)) == (QueryKind.DELIVERED_CLAIM.value, {"claim_ref": "dialogue-item-1"})
    # Only closed-vocabulary arguments choose a query; free text never does.
    assert compile_content_query("identity_request", {"identity_attribute": "name", "about": "assistant"},
                                 time_zone=None).query_kind == QueryKind.ASSISTANT_NAME.value
    assert compile_content_query("identity_request", {"identity_attribute": "creator", "about": "assistant"},
                                 time_zone=None).query_kind == "application_identity.creator"
    assert compile_content_query("runtime_fact_request", {"fact_kind": "current_time"},
                                 time_zone=None).query_kind == "runtime.current_time"


def test_reviewed_classes_answer_only_with_an_explicit_offline_opt_in() -> None:
    capture = make_capture(contents=(("user", "Expand on it."),))
    trace = run_turn("Expand on it.", ELABORATION, capture=capture)
    frame = next(item for item in trace.interpretation.semantic_frames if item.predicate == "elaboration_request")
    context = _dialogue(trace, frame, resolution="resolved", resolved=("dialogue-item-dal",))
    record = ProducerRecord(
        record_id="claim-dal", query_kind=QueryKind.DELIVERED_CLAIM.value, arguments={"claim_ref": "dialogue-item-dal"},
        predicate="dialogue.user_statement", value="Dal is a lentil dish", value_type="string",
        value_anchor="Dal is a lentil dish", owner="user", owner_ref="user-a", authorized=True,
        admission_state="delivered", revision="rev-dal",
    )

    def factory(inputs, turn_time):
        return (ApplicationIdentityProducer(), delivered_claim_producer((record,), turn_time=turn_time))

    live = RequirementResolutionEngine(producer_factory=factory).resolve(_request(trace, dialogue=context))
    assert _only(live.snapshot, K.TRUSTED_CONTENT).reason_code == R.PRODUCER_REQUIRES_REVIEW
    offline = RequirementResolutionEngine(producer_factory=factory, allow_review_classes=True).resolve(
        _request(trace, dialogue=context))
    content = _only(offline.snapshot, K.TRUSTED_CONTENT)
    assert (content.status, content.value_refs) == (S.SATISFIED, ("dialogue-item-dal",))
    [fact] = offline.content_records[0].result.facts
    assert fact.attribution.value == "user"  # a delivered claim is attributed, never grounded as true
    # Another user's claim is never admitted.
    other = RequirementResolutionEngine(producer_factory=factory, allow_review_classes=True).resolve(
        _request(trace, dialogue=context, inputs=RequirementResolutionInputs(turn_time=TURN_TIME, user_ref="user-b")))
    assert _only(other.snapshot, K.TRUSTED_CONTENT).status == S.UNAUTHORIZED


# ---------------------------------------------------------------------------
# Capability, authorization, consent, safety
# ---------------------------------------------------------------------------

def _rule_turn(text: str, **runtime):
    from app.ai.Cognitive.system.v0.live_shadow.compiler import compile_request, resolve_lineage
    from app.ai.Cognitive.system.v0.live_shadow.service import build_pipeline

    capture = make_capture(contents=(("user", text),), capability_keys=())
    request = compile_request(capture, resolve_lineage(capture, None))
    return build_pipeline(capture.turn_id).run(request)


def test_capability_available_unavailable_and_unregistered() -> None:
    reminder = run_turn("Create a reminder to buy oat milk tomorrow at 9am.", REMINDER)
    capability = _only(reminder.requirement_resolution.handoff.snapshot, K.CAPABILITY)
    assert (capability.status, capability.reason_code, capability.blocking, capability.tier) == (
        S.UNAVAILABLE, R.CAPABILITY_UNREGISTERED, True, TIER_CAPABILITY)
    assert capability.capability_refs == ("capability-unregistered:action_request",)
    report = _rule_turn("Could you send the report to Alex?")
    unavailable = _only(report.requirement_resolution.handoff.snapshot, K.CAPABILITY)
    assert (unavailable.status, unavailable.reason_code) == (S.UNAVAILABLE, R.CAPABILITY_UNAVAILABLE)
    available = RequirementResolutionEngine().resolve(_request(report, runtime={"available_capabilities": ("send_report",)}))
    assert _only(available.snapshot, K.CAPABILITY).status == S.SATISFIED
    # A registered capability declares its authorization; an unregistered effect declares none.
    assert _only(available.snapshot, K.AUTHORIZATION).tier == TIER_EXECUTION
    assert not [item for item in reminder.requirement_resolution.handoff.snapshot.resolutions
                if item.kind in {K.AUTHORIZATION, K.CONSENT}]


def test_authorization_present_and_missing() -> None:
    report = _rule_turn("Could you send the report to Alex?")
    missing = RequirementResolutionEngine().resolve(_request(report, runtime={"available_capabilities": ("send_report",)}))
    authorization = _only(missing.snapshot, K.AUTHORIZATION)
    assert (authorization.status, authorization.reason_code, authorization.blocking) == (
        S.UNAUTHORIZED, R.AUTHORIZATION_MISSING, True)
    granted = RequirementResolutionEngine().resolve(_request(report, runtime={
        "available_capabilities": ("send_report",), "authorization": "authorized"}))
    assert _only(granted.snapshot, K.AUTHORIZATION).status == S.SATISFIED


def _with_database(trace):
    frames = tuple(
        item.model_copy(update={"arguments": {**item.arguments, "database": "orders"},
                                "unresolved_arguments": ("authorization",)})
        if item.predicate == "delete_database" else item
        for item in trace.interpretation.semantic_frames
    )
    return trace.interpretation.model_copy(update={"semantic_frames": frames})


def test_consent_present_and_missing_for_an_irreversible_effect() -> None:
    trace = _rule_turn("Delete the orders database.")
    interpretation = _with_database(trace)
    ready = dict(available_capabilities=("delete_database",), authorization="authorized",
                 safety_decision=_decision(trace.runtime_snapshot_id, SafetyDisposition.ALLOW))
    missing = RequirementResolutionEngine().resolve(_request(trace, interpretation=interpretation, runtime=ready))
    consent = _only(missing.snapshot, K.CONSENT)
    assert (consent.status, consent.reason_code, consent.blocking) == (S.MISSING_USER_INPUT, R.CONSENT_MISSING, True)
    granted = RequirementResolutionEngine().resolve(
        _request(trace, interpretation=interpretation, runtime={**ready, "consent": "granted"}))
    assert _only(granted.snapshot, K.CONSENT).status == S.SATISFIED
    assert granted.snapshot.goal_readiness[0].readiness == "ready"


@pytest.mark.parametrize("disposition, policy, provenance, status, reason", [
    (SafetyDisposition.ALLOW, "application-safety-policy-v0.1", SafetyDecisionProvenance.APPLICATION_SAFETY_POLICY,
     S.SATISFIED, R.SAFETY_ALLOWED),
    (SafetyDisposition.BLOCK_COMMITMENTS, "application-safety-policy-v0.1",
     SafetyDecisionProvenance.APPLICATION_SAFETY_POLICY, S.PROHIBITED, R.SAFETY_BLOCKS_COMMITMENTS),
    (SafetyDisposition.BLOCK_RESPONSE, "application-safety-policy-v0.1",
     SafetyDecisionProvenance.APPLICATION_SAFETY_POLICY, S.PROHIBITED, R.SAFETY_BLOCKS_RESPONSE),
    (SafetyDisposition.UNKNOWN, "application-safety-policy-v0.1", SafetyDecisionProvenance.APPLICATION_SAFETY_POLICY,
     S.UNAVAILABLE, R.SAFETY_DECISION_UNKNOWN),
    (SafetyDisposition.UNAVAILABLE, "application-safety-policy-v0.1", SafetyDecisionProvenance.RUNTIME_UNAVAILABLE,
     S.UNAVAILABLE, R.SAFETY_DECISION_UNAVAILABLE),
    (SafetyDisposition.ALLOW, "application-safety-policy-v9", SafetyDecisionProvenance.APPLICATION_SAFETY_POLICY,
     S.UNAVAILABLE, R.SAFETY_POLICY_VERSION_UNSUPPORTED),
])
def test_safety_allow_prohibit_and_fail_closed(disposition, policy, provenance, status, reason) -> None:
    trace = run_turn("How do I steal my neighbor's Wi-Fi password?", UNSAFE)
    decision = _decision(trace.runtime_snapshot_id, disposition, policy=policy, provenance=provenance)
    outcome = RequirementResolutionEngine().resolve(_request(trace, runtime={"safety_decision": decision}))
    safety = _only(outcome.snapshot, K.SAFETY)
    assert (safety.status, safety.reason_code) == (status, reason)
    assert safety.tier == TIER_SAFETY  # an unsafe request is refused before anything else
    if status == S.PROHIBITED:
        assert "decision-test" in safety.policy_refs
    # Never an execution path: an allowed unsafe request still has no capability.
    assert _only(outcome.snapshot, K.CAPABILITY).status == S.UNAVAILABLE
    missing = RequirementResolutionEngine().resolve(_request(trace, runtime={"safety_decision": None}))
    assert _only(missing.snapshot, K.SAFETY).reason_code == R.SAFETY_DECISION_MISSING


# ---------------------------------------------------------------------------
# Precedence and determinism
# ---------------------------------------------------------------------------

def test_only_the_lowest_unsatisfied_tier_blocks() -> None:
    # Missing user input comes before a capability limitation...
    ambiguous = run_turn("Can you check it for me?", AMBIGUOUS)
    snapshot = ambiguous.requirement_resolution.handoff.snapshot
    assert [item.kind for item in snapshot.resolutions if item.blocking] == [K.DIALOGUE_REFERENT]
    assert not _only(snapshot, K.CAPABILITY).blocking and _only(snapshot, K.CAPABILITY).status == S.UNAVAILABLE
    # ...a prohibited request is refused before any detail is asked for...
    decision = _decision(ambiguous.runtime_snapshot_id, SafetyDisposition.BLOCK_COMMITMENTS)
    prohibited = RequirementResolutionEngine().resolve(_request(ambiguous, runtime={"safety_decision": decision})).snapshot
    assert [item.kind for item in prohibited.resolutions if item.blocking] == [K.SAFETY]
    assert prohibited.goal_readiness[0].blocking_tier == TIER_SAFETY
    # ...and an unsafe request's safety concern outranks its missing capability.
    unsafe = run_turn("How do I steal my neighbor's Wi-Fi password?", UNSAFE).requirement_resolution.handoff.snapshot
    assert [item.kind for item in unsafe.resolutions if item.blocking] == [K.SAFETY]


def test_resolution_is_deterministic_and_order_independent() -> None:
    trace = run_turn("Create a reminder to buy oat milk tomorrow at 9am.", REMINDER)
    first = RequirementResolutionEngine().resolve(_request(trace))
    second = RequirementResolutionEngine().resolve(_request(trace))
    assert first.snapshot == second.snapshot and first.snapshot.digest == second.snapshot.digest
    shuffled = trace.goal_snapshot.model_copy(update={"records": tuple(reversed(trace.goal_snapshot.records))})
    request = _request(trace).model_copy(update={"goal_snapshot": shuffled})
    assert RequirementResolutionEngine().resolve(request).snapshot.resolutions == first.snapshot.resolutions
