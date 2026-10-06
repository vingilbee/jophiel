"""Dialogue Working Memory V0 through capture v0.3, pipeline v0.3, and Realization (24.7, 24.8, 22).

Pure design cases need no database; the end-to-end shadow tests need
TEST_DATABASE_URL (a disposable PostgreSQL database; each test gets a schema).
"""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.ai.Cognitive.system.v0.cognitive_pipeline_v0 import (
    PIPELINE_SCHEMA_VERSION,
    PIPELINE_SCHEMA_VERSION_V03,
    CognitivePipelineRequest,
)
from app.ai.Cognitive.system.v0.contracts.actions import ActionTargetType
from app.ai.Cognitive.system.v0.contracts.dialogue_memory import (
    DialogueCueKind,
    DialogueEntity,
    DialogueGap,
    DialogueItemStatus,
    DialogueMemoryFailure,
    DialogueMemoryFailureCode,
    DialogueMemoryFailurePhase,
    DialogueOmissionReason,
    DialogueOptionSet,
    DialoguePlanReference,
    DialogueQuestion,
    DialogueRequest,
    DialogueTopic,
    DialogueUtteranceReference,
    turn_context_digest,
)
from app.ai.Cognitive.system.v0.contracts.realization import (
    NON_ASSERTABLE_STATUSES,
    AuthorizedQuote,
    ContentAttribution,
    ContentEpistemicStatus,
    ContentSourceKind,
    DialogueReference,
    DialogueReferenceKind,
    GroundedProposition,
    RealizationStatus,
)
from app.ai.Cognitive.system.v0.dialogue_memory_v0 import (
    DialogueMemoryLimits,
    DialogueRealizationContentProjectorV0,
    bootstrap,
    compile_turn_context,
    select_window,
    source_event,
)
from app.ai.Cognitive.system.v0.dialogue_memory_v0.projector import GROUNDED_PREFIX, grounded_ref
from app.ai.Cognitive.system.v0.live_shadow import (
    ObservationContext,
    ReplayStatus,
    SegmentResetReason,
    TerminalStatus,
    observe,
    replay,
)
from app.ai.Cognitive.system.v0.live_shadow.compiler import PredecessorRecord
from app.ai.Cognitive.system.v0.live_shadow.digests import payload_digest
from app.ai.Cognitive.system.v0.live_shadow.models import (
    ALWAYS_OMITTED_V03,
    CAPTURE_SCHEMA_VERSION,
    CAPTURE_SCHEMA_VERSION_V03,
    DIALOGUE_SUPPLIED_INPUTS,
    CapturedDialogueMemory,
    CognitiveShadowCapture,
    OmittedInput,
)
from app.ai.Cognitive.system.v0.live_shadow.realization import realization_inventory
from app.ai.Cognitive.system.v0.realization_v0 import RealizationCompileSkip, RealizationCompilerV0, project_payload
from app.ai.Cognitive.system.v0.contracts.dialogue_memory import DialogueResetReason
from cognitive_shadow_support import make_capture
from dialogue_memory_support import Script, uid

CASES_PATH = Path(__file__).resolve().parents[1] / "app/ai/Cognitive/system/v0/data/dialogue_memory_design_cases.json"
CASES = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
PURE = [case for case in CASES if case["kind"] == "pure"]


def _observe(capture: CognitiveShadowCapture, predecessor: PredecessorRecord | None = None):
    payload = capture.model_dump(mode="json")
    return observe(capture, predecessor, ObservationContext(
        job_id=capture.capture_id, attempt=1, capture_digest=payload_digest(payload),
        started_at="2026-09-27T00:00:00+00:00",
    ))


# ---------------------------------------------------------------------------
# Design cases (24.7)
# ---------------------------------------------------------------------------

def _script(case: dict, name: str | None = None) -> Script:
    script = Script(name or case["case_id"])
    generated = case.get("turns_generated")
    if generated:
        for turn in range(1, generated["count"] + 1):
            script.exchange(turn, generated["user"].format(n=turn), generated["assistant"])
        return script
    turn = 0
    for entry in case["turns"]:
        role, content, *options = entry
        options = dict(options[0]) if options else {}
        if role == "user":
            turn += 1
        deliveries = tuple(tuple(item) for item in options.pop("deliveries", ()))
        script.add(role, content, turn=max(turn, 1), deliveries=deliveries, **options)
    return script


def _snapshot(case: dict, script: Script):
    limit = case.get("bootstrap_messages")
    if limit is None:
        return script.snapshot()
    boundary = len(script.records)
    window = select_window(script.records[-(limit + 1):], boundary=boundary, limit=limit)
    events = [source_event(record, user_ref=script.user) for record in window.records]
    return bootstrap(user_ref=script.user, conversation_ref=script.conversation, events=events,
                     window_start=window.window_start, revision=1, reset_reason=DialogueResetReason.INITIAL_BOOTSTRAP,
                     omissions=window.omissions, limits=DialogueMemoryLimits(bootstrap_messages=limit))


def _resolve(target: str, snapshot) -> str:
    kind, _, value = target.partition(":")
    items = snapshot.items
    if kind == "entity":
        return next(item.item_id for item in items if isinstance(item, DialogueEntity) and item.entity_key == value)
    if kind == "option":
        option_set = next(item for item in items if item.item_id in snapshot.active_option_set_refs)
        return option_set.options[int(value) - 1].option_ref
    if kind == "plan":
        return snapshot.active_plan_refs[0]
    if kind == "topic":
        return next(item.item_id for item in items if isinstance(item, DialogueTopic) and item.topic_key == value)
    if kind == "request":
        return next(item.item_id for item in items if isinstance(item, DialogueRequest)
                    and item.surface_anchors == (value,))
    if kind == "utterance":
        speaker = value.split(":")[0]
        utterances = [item for item in items if isinstance(item, DialogueUtteranceReference)
                      and item.speaker.value == speaker]
        return max(utterances, key=lambda item: item.message_sequence).item_id
    raise AssertionError(f"unknown target notation {target}")


def _case_context(case: dict, name: str | None = None):
    script = _script(case, name)
    snapshot = _snapshot(case, script)
    record, event = script.focus(case["focus"])
    context = compile_turn_context(snapshot, event, cognitive_turn_id=script.turn(999))
    return script, snapshot, record, context


def _v03_capture(script: Script, snapshot, record, context) -> CognitiveShadowCapture:
    omitted = set(ALWAYS_OMITTED_V03) | {OmittedInput.MEMORY_PROJECTION, OmittedInput.RELATIONSHIP_PROJECTION,
                                         OmittedInput.APPLICATION_SAFETY_POLICY}
    users = [item.message_ref for item in script.records if item.role == "user"]
    return make_capture(
        contents=(("user", record.content),), turn_id=script.turn(999), user_ref=script.user,
        conversation_ref=script.conversation, message_ids=(record.message_ref,),
        previous_user_message_id=users[-1] if users else None, messages=script.captured_messages(record),
        schema_version=CAPTURE_SCHEMA_VERSION_V03, omitted_inputs=tuple(sorted(omitted)),
        dialogue=CapturedDialogueMemory(status="available", turn_context=context,
                                        turn_context_digest=turn_context_digest(context)),
    )


def test_every_reviewed_case_is_covered_once() -> None:
    assert sorted(case["covers"] for case in CASES) == list(range(1, 21))
    assert len({case["case_id"] for case in CASES}) == 20
    tests = {path.name: path.read_text(encoding="utf-8") for path in Path(__file__).parent.glob("test_*.py")}
    for case in CASES:
        if case["kind"] == "database":
            file_name, _, function = case["test"].rpartition("::")
            assert f"def {function}(" in tests[Path(file_name).name]


@pytest.mark.parametrize("case", PURE, ids=[case["case_id"] for case in PURE])
def test_design_case(case) -> None:
    script, snapshot, record, context = _case_context(case)
    expect = case["expect"]
    analysis = context.current_interpretation_projection
    for expected, cue in zip(expect.get("cues", ()), analysis.cues, strict=True):
        assert (cue.cue_kind.value, cue.resolution.value) == (expected["kind"], expected["resolution"])
        if "targets" in expected:
            assert set(cue.resolved_refs) == {_resolve(target, snapshot) for target in expected["targets"]}
        if "candidates" in expected:
            assert set(cue.candidate_refs) == {_resolve(target, snapshot) for target in expected["candidates"]}
    for target in expect.get("quoted", ()):
        assert snapshot.item(_resolve(target, snapshot)).quote is not None
    for target in expect.get("partial_utterances", ()):
        assert snapshot.item(_resolve(target, snapshot)).completeness == "partial"
    gaps = {item.gap_kind.value for item in snapshot.items if isinstance(item, DialogueGap)}
    assert set(expect.get("gaps", ())) <= gaps
    for reason, start, end in expect.get("omissions", ()):
        assert any((item.reason.value, item.sequence_start, item.sequence_end) == (reason, start, end)
                   for item in snapshot.omitted_history)
    for anchor in expect.get("open_columbina_questions", ()):
        question = next(item for item in snapshot.items if isinstance(item, DialogueQuestion)
                        and item.surface_anchors == (anchor,))
        assert question.speaker.value == "columbina" and question.item_id in snapshot.open_question_refs
    if expect.get("remembered_unprompted"):
        assert any(isinstance(item, DialogueUtteranceReference) and item.introduced_turn_ref.startswith("message:")
                   for item in snapshot.items)

    # The real deterministic pipeline, schema v0.3, over the captured context.
    record_out = _observe(_v03_capture(script, snapshot, record, context))
    assert record_out.status == TerminalStatus.COMPLETED, record_out.failure
    trace, request = record_out.pipeline_trace, record_out.compiled_request
    assert trace.versions.pipeline_schema == PIPELINE_SCHEMA_VERSION_V03 and request.schema_version == 3
    dialogue_objects = {item.object_id: item for item in request.action_grounded_objects}
    types = {item.object_type.value for item in dialogue_objects.values()}
    assert set(expect.get("action_object_types", ())) <= types
    if "action_unknown_referents" in expect:
        assert len(request.action_target_descriptors) == expect["action_unknown_referents"]
        assert all(item.unknown_kind == "referent" for item in request.action_target_descriptors)
    if "proposition_objects_asserted_by" in expect:
        propositions = [item for item in dialogue_objects.values() if item.object_type == ActionTargetType.PROPOSITION]
        assert propositions and all(item.asserted_by == expect["proposition_objects_asserted_by"]
                                    and item.epistemic_status == "unknown" for item in propositions)
    for expected in expect.get("interpretation_propositions", ()):
        items = request.dialogue_context.interpretation_context.items
        match = next(item for item in items if item.item_kind == "proposition"
                     and item.surface_anchor == expected["anchor"])
        assert (match.speaker, match.epistemic_status) == (expected["speaker"], expected["epistemic_status"])
    if expect.get("a_move_targets_a_dialogue_object"):
        assert any(target.ref.startswith(GROUNDED_PREFIX)
                   for move in trace.action_selection.plan.moves for target in move.target_refs)
    if expect.get("an_eligible_candidate_targets_a_dialogue_object"):
        # The dialogue object reaches Action as a legal, scored candidate target.
        assert any(
            target.ref.startswith(GROUNDED_PREFIX)
            for step in trace.action_selection.steps for candidate in step.constructed_candidates
            if candidate.candidate_id in {item.candidate_ref for item in step.scored_candidates}
            for target in candidate.target_refs
        )
    if expect.get("primary_move_addresses_blocked_request"):
        # SH-EVAL-006: a blocked question is addressed by the primary move,
        # which targets one of the typed planning condition's blockers.
        selection = trace.action_selection
        blockers = {ref for condition in selection.planning_conditions for ref in condition.blocker_refs}
        primary = next(move for move in selection.plan.moves if move.move_id == selection.plan.primary_move_ref)
        assert blockers and {target.ref for target in primary.target_refs} & blockers
    committed = expect.get("committed_after_focus")
    if committed:
        script.add("user", case["focus"], turn=999)
        after = script.snapshot()
        entities = {item.entity_key: item.status for item in after.items if isinstance(item, DialogueEntity)}
        for key in committed.get("superseded_entities", ()):
            assert entities[key] == DialogueItemStatus.SUPERSEDED
        for key in committed.get("current_entities", ()):
            assert entities[key] == DialogueItemStatus.CURRENT
        for anchor in committed.get("withdrawn_requests", ()):
            assert next(item for item in after.items if isinstance(item, DialogueRequest)
                        and item.surface_anchors == (anchor,)).status == DialogueItemStatus.WITHDRAWN
    if expect.get("disjoint_identities"):
        other = _case_context(case, name=case["case_id"] + "-second-user")
        assert other[0].user != script.user and other[1].snapshot_id != snapshot.snapshot_id
        assert not {item.item_id for item in other[1].items} & {item.item_id for item in snapshot.items}
        assert [cue.resolution for cue in other[3].current_interpretation_projection.cues] == [
            cue.resolution for cue in analysis.cues]


# ---------------------------------------------------------------------------
# Capture and pipeline versions, replay, lineage (22)
# ---------------------------------------------------------------------------

def _pets_context():
    return _case_context(next(case for case in CASES if case["case_id"] == "second-option"))


def test_v02_payloads_are_unchanged_by_the_v03_fields() -> None:
    capture = make_capture()
    payload = capture.model_dump(mode="json")
    assert capture.schema_version == CAPTURE_SCHEMA_VERSION and "dialogue" not in payload
    assert all("conversation_sequence" not in message for message in payload["messages"])
    record = _observe(capture)
    request_payload = record.compiled_request.model_dump(mode="json")
    assert record.pipeline_trace.versions.pipeline_schema == PIPELINE_SCHEMA_VERSION
    assert "dialogue_context" not in request_payload and request_payload["schema_version"] == 2
    assert "dialogue_memory" not in record.versions.model_dump(mode="json")


def test_v03_replay_matches_and_is_self_contained() -> None:
    script, snapshot, record, context = _pets_context()
    capture = _v03_capture(script, snapshot, record, context)
    observed = _observe(capture)
    assert observed.versions.dialogue_memory == context.versions
    report = replay(
        compiled_request_payload=observed.compiled_request.model_dump(mode="json"),
        pipeline_trace_payload=observed.pipeline_trace.model_dump(mode="json"),
        compiled_request_digest=observed.compiled_request_digest, stored_semantic_digest=observed.semantic_digest,
        versions_payload=observed.versions.model_dump(mode="json"),
    )
    assert (report.status, report.code) == (ReplayStatus.MATCH, "semantic_digest_match")
    # The stored capture recompiles to exactly the stored request, from the capture alone.
    again = _observe(CognitiveShadowCapture.model_validate(capture.model_dump(mode="json")))
    assert again.compiled_request == observed.compiled_request


def test_tampered_or_unavailable_dialogue_context_fails_the_observation() -> None:
    script, snapshot, record, context = _pets_context()
    capture = _v03_capture(script, snapshot, record, context)
    payload = capture.model_dump(mode="json")
    payload["dialogue"]["turn_context"]["current_interpretation_projection"]["cues"] = []
    payload["dialogue"]["turn_context_digest"] = turn_context_digest(payload["dialogue"]["turn_context"])
    forged = CognitiveShadowCapture.model_validate(payload)
    failed = _observe(forged)
    assert failed.status == TerminalStatus.FAILED and failed.failure.code.startswith("dialogue_memory.")

    omitted = set(ALWAYS_OMITTED_V03) | DIALOGUE_SUPPLIED_INPUTS | {
        OmittedInput.DIALOGUE_WORKING_MEMORY, OmittedInput.MEMORY_PROJECTION,
        OmittedInput.RELATIONSHIP_PROJECTION, OmittedInput.APPLICATION_SAFETY_POLICY,
    }
    unavailable = capture.model_copy(update={
        "dialogue": CapturedDialogueMemory(status="unavailable", failure=DialogueMemoryFailure(
            phase=DialogueMemoryFailurePhase.SOURCE_ORDERING, code=DialogueMemoryFailureCode.SOURCE_SEQUENCE_GAP,
            safe_message="gap")),
        "omitted_inputs": tuple(sorted(omitted)),
    })
    unavailable = CognitiveShadowCapture.model_validate(unavailable.model_dump(mode="json"))
    failed = _observe(unavailable)
    assert (failed.status, failed.failure.code) == (TerminalStatus.FAILED, "dialogue_memory.source_sequence_gap")


def test_capture_rejects_future_evidence_and_mismatched_focus() -> None:
    script, snapshot, record, context = _pets_context()
    payload = _v03_capture(script, snapshot, record, context).model_dump(mode="json")
    reordered = json.loads(json.dumps(payload))
    reordered["messages"][0]["conversation_sequence"] = 99  # a message positioned after the focus
    with pytest.raises(ValueError):
        CognitiveShadowCapture.model_validate(reordered)
    retexted = json.loads(json.dumps(payload))
    retexted["messages"][-1]["content"] = "Use the third option."
    with pytest.raises(ValueError):
        CognitiveShadowCapture.model_validate(retexted)


def test_switching_between_v02_and_v03_is_a_visible_lineage_reset() -> None:
    script, snapshot, record, context = _pets_context()
    v03 = _v03_capture(script, snapshot, record, context)
    previous = _observe(make_capture(user_ref=script.user, conversation_ref=script.conversation,
                                     message_ids=(v03.previous_user_message_id,), contents=(("user", "hi"),)))
    predecessor = PredecessorRecord(
        terminal_id=previous.terminal_id, turn_id=previous.turn_id, user_ref=previous.user_ref,
        conversation_ref=previous.conversation_ref, focus_message_id=previous.focus_message_id,
        segment_id=previous.segment_id, status=previous.status, pipeline_trace=previous.pipeline_trace,
    )
    observed = _observe(v03, predecessor)
    assert observed.segment_reset_reason == SegmentResetReason.LINEAGE_VERSION_CHANGE


# ---------------------------------------------------------------------------
# Realization content (24.8)
# ---------------------------------------------------------------------------

def _fake_action(turn: str, *refs: str):
    """A duck-typed Action result whose single move targets the given dialogue refs."""

    targets = tuple(SimpleNamespace(role="primary", ref=ref, target_type=ActionTargetType.PROPOSITION) for ref in refs)
    move = SimpleNamespace(target_refs=targets, supporting_evidence_refs=(), parameters={})
    return SimpleNamespace(cognitive_turn_id=turn, result_id=uid("result", turn), plan=SimpleNamespace(moves=(move,)))


def _mapping_context():
    script = Script("realization-mapping")
    script.exchange(1, "I ran 5 km today. What should I eat after running?",
                    "Try these options:\n1. Banana\n2. Yogurt\nI'll check in tomorrow.")
    script.exchange(2, "My dog is named Max.", "Hi Max!")
    script.exchange(3, "No, I meant Charlie, not Max.", "Got it.")
    context, _ = script.context("What did you say before?")
    return script, context


def test_dialogue_items_project_to_the_right_content_kind_source_and_attribution() -> None:
    script, context = _mapping_context()
    snapshot = context.prior_context_projection
    from app.ai.Cognitive.system.v0.contracts.dialogue_memory import DialogueCorrection, DialogueProposition

    proposition = next(item for item in snapshot.items if isinstance(item, DialogueProposition) and item.is_current)
    option_set = next(item for item in snapshot.items if isinstance(item, DialogueOptionSet))
    reply = next(item for item in snapshot.items if isinstance(item, DialogueUtteranceReference)
                 and item.speaker.value == "columbina" and item.message_sequence == 2)
    correction = next(item for item in snapshot.items if isinstance(item, DialogueCorrection))
    refs = [grounded_ref(ref) for ref in (proposition.item_id, option_set.options[0].option_ref, reply.item_id,
                                          correction.item_id)]
    inventory = DialogueRealizationContentProjectorV0().project(_fake_action(context.cognitive_turn_id, *refs), context)
    by_ref = {}
    for item in inventory.items:
        for ref in item.represents_refs:
            by_ref.setdefault(ref, []).append(item)
    claim = by_ref[refs[0]][0]
    assert isinstance(claim, GroundedProposition)
    assert (claim.source_kind, claim.attribution, claim.epistemic_status) == (
        ContentSourceKind.DELIVERED_USER_UTTERANCE, ContentAttribution.USER, ContentEpistemicStatus.ATTRIBUTED)
    option = by_ref[refs[1]][0]
    assert isinstance(option, DialogueReference) and option.reference_kind == DialogueReferenceKind.OPTION
    assert (option.source_kind, option.attribution) == (ContentSourceKind.DELIVERED_ASSISTANT_UTTERANCE,
                                                         ContentAttribution.COLUMBINA)
    quote = by_ref[refs[2]][0]
    assert isinstance(quote, AuthorizedQuote) and quote.attribution == ContentAttribution.COLUMBINA
    assert quote.quote_text == script.records[1].content  # exact delivered wording
    sides = {item.epistemic_status: item for item in by_ref[refs[3]]}
    assert set(sides) == {ContentEpistemicStatus.CORRECTED, ContentEpistemicStatus.ATTRIBUTED}
    assert sides[ContentEpistemicStatus.CORRECTED].epistemic_status in NON_ASSERTABLE_STATUSES
    assert sides[ContentEpistemicStatus.CORRECTED].subject.surface_anchor == "Max"
    assert sides[ContentEpistemicStatus.ATTRIBUTED].subject.surface_anchor == "Charlie"
    for item in inventory.items:  # provenance: snapshot, digest, item, and source messages
        assert f"dialogue-snapshot:{snapshot.snapshot_id}" in item.source_refs
        assert f"dialogue-snapshot-digest:{snapshot.semantic_digest}" in item.source_refs
        assert item.authorized_for_realization is True


def test_only_selected_refs_enter_the_inventory_and_legacy_speech_is_never_a_fact() -> None:
    _, context = _mapping_context()
    snapshot = context.prior_context_projection
    plan_less = DialogueRealizationContentProjectorV0().project(_fake_action(context.cognitive_turn_id), context)
    assert plan_less.items == ()  # nothing referenced, nothing exported
    with pytest.raises(ValueError):
        GroundedProposition(
            content_id="x", cognitive_turn_id=context.cognitive_turn_id,
            source_kind=ContentSourceKind.DELIVERED_ASSISTANT_UTTERANCE, source_refs=("chat-message:x",),
            authorized_for_realization=True, epistemic_status=ContentEpistemicStatus.ATTRIBUTED,
            attribution=ContentAttribution.COLUMBINA, subject={"ref": "s", "surface_anchor": "Paris"},
            predicate="dialogue.user_statement",
        )
    partial_script = Script("partial-realization")
    partial_script.exchange(1, "Tell me a story.", "Once upon a", status="partial")
    partial_context, _ = partial_script.context("Go on.")
    partial = next(item for item in partial_context.prior_context_projection.items
                   if isinstance(item, DialogueUtteranceReference) and item.speaker.value == "columbina")
    content = DialogueRealizationContentProjectorV0().project(
        _fake_action(partial_context.cognitive_turn_id, grounded_ref(partial.item_id)), partial_context).items
    assert len(content) == 1 and isinstance(content[0], DialogueReference)  # partial output is never quoted
    assert "dialogue-completeness:partial" in content[0].source_refs
    assert snapshot.snapshot_id  # the snapshot itself never leaves the projector


def _topic_return():
    """The reviewed topic-return context with a focus that only returns to the topic.

    The reviewed focus also asks a question with no trusted answer producer, so
    its plan now addresses that blocked question first (SH-EVAL-006).  These
    realization-privacy checks need a plan that binds the returned topic.
    """

    case = copy.deepcopy(next(case for case in CASES if case["case_id"] == "return-to-prior-topic"))
    case["focus"] = "Back to protein."
    script, snapshot, record, context = _case_context(case)
    observed = _observe(_v03_capture(script, snapshot, record, context))
    assert [move.definition_ref.action_key for move in observed.pipeline_trace.action_selection.plan.moves] == [
        "manage_dialogue.return_to_topic",
    ]
    return script, context, observed


def _compile_realization(observed, context):
    trace = observed.pipeline_trace
    action = trace.action_selection
    return RealizationCompilerV0().compile(
        action_result=action, inventory=realization_inventory(action, context),
        personality=trace.cognitive_turn.input_snapshot.action_personality,
        state=trace.cognitive_turn.dynamic_state_update.snapshot, language_tag="en",
        current_user_utterance=next(
            message.content for message in observed.compiled_request.messages
            if message.message_id == observed.compiled_request.focus_message_id
        ),
        safety_basis=action.safety, configured_maximum_characters=1200,
    )


def test_realization_payload_carries_selected_content_never_the_transcript_or_snapshot() -> None:
    script, context, observed = _topic_return()
    request = _compile_realization(observed, context)
    assert not isinstance(request, RealizationCompileSkip)
    topic = next(item for item in request.content_items if isinstance(item, DialogueReference)
                 and item.reference_kind == DialogueReferenceKind.TOPIC)
    assert topic.surface_anchors == ("protein",) and topic.attribution == ContentAttribution.USER
    rendered = json.dumps(project_payload(request).model_dump(mode="json"))
    for text in ("Sorry to hear.", "I sleep badly", "Speaking of sleep", context.prior_context_projection.snapshot_id):
        assert text not in rendered


def test_missing_required_dialogue_content_skips_instead_of_inventing() -> None:
    script, context, observed = _topic_return()
    snapshot = context.prior_context_projection
    stripped = snapshot.model_copy(update={"items": tuple(item for item in snapshot.items
                                                         if not isinstance(item, DialogueTopic))})
    hollow = context.model_copy(update={"prior_context_projection": stripped})
    result = _compile_realization(observed, hollow)
    assert isinstance(result, RealizationCompileSkip) and result.status == RealizationStatus.SKIPPED_MISSING_CONTENT


# ---------------------------------------------------------------------------
# End to end on real PostgreSQL (16, 22, 24.7 case 19, 24.8)
# ---------------------------------------------------------------------------

from cognitive_shadow_support import create_conversation, create_user, database, shadow_env  # noqa: E402,F401
from dialogue_memory_support import dialogue_env  # noqa: E402,F401
from test_dialogue_memory_persistence import _drain, _head, _turn  # noqa: E402


def _live_turn(env, conversation_id, text: str):
    from app.db.models import ChatJob, Conversation
    from app.services import chat as chat_service

    with env.db() as db:
        conversation = db.get(Conversation, conversation_id)
        turn_id = uuid.uuid4()
        message = chat_service.add_message(db, conversation=conversation, role="user", content=text,
                                           turn_id=turn_id, commit=False)
        job = ChatJob(turn_id=turn_id, user_id=conversation.user_id, conversation_id=conversation.id,
                      user_message_id=message.id, payload={"content": text}, status="processing")
        db.add(job)
        db.commit()
        return message.id, turn_id, job.id


def _capture(env, conversation_id, user_id, message_id, turn_id, job_id):
    from app.ai.Cognitive.system.v0.live_shadow.models import GenerationPath, ShadowSourceChannel
    from app.services import cognitive_shadow as shadow_service

    return shadow_service.capture_turn(
        user_id=user_id, conversation_id=conversation_id, turn_id=turn_id, user_message_id=message_id,
        chat_job_id=job_id, generation_path=GenerationPath.CHAT_WORKER, source_channel=ShadowSourceChannel.WEB,
    )


def _finish_reply(env, conversation_id, turn_id, reply: str):
    from app.db.models import Conversation
    from app.services import chat as chat_service
    from app.services import dialogue_memory as service

    with env.db() as db:
        conversation = db.get(Conversation, conversation_id)
        assistant = chat_service.add_message(db, conversation=conversation, role="assistant", content=reply,
                                             turn_id=turn_id, commit=False)
        service.schedule_update(db, user_id=conversation.user_id, conversation_id=conversation_id,
                                trigger_message_id=assistant.id)
        db.commit()
        return assistant.id


def _shadow_rows(env, turn_id):
    from app.db.models import CognitiveShadowJob, CognitiveShadowTrace

    with env.db() as db:
        return (db.scalar(select(CognitiveShadowJob).where(CognitiveShadowJob.turn_id == turn_id)),
                db.scalar(select(CognitiveShadowTrace).where(CognitiveShadowTrace.turn_id == turn_id)))


def _seed_conversation(env):
    with env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        _turn(db, conversation, "Suggest a breakfast.", "Here are some options:\n1. Oatmeal\n2. Eggs")
        return user.id, conversation.id


def test_v03_capture_end_to_end_and_the_turn_never_sees_its_own_reply(dialogue_env) -> None:
    from app.services import cognitive_shadow as shadow_service
    from app.workers import cognitive_shadow as shadow_worker

    user_id, conversation_id = _seed_conversation(dialogue_env)
    _drain()
    message_id, turn_id, job_id = _live_turn(dialogue_env, conversation_id, "Use the second option.")
    assert _capture(dialogue_env, conversation_id, user_id, message_id, turn_id, job_id)
    reply_id = _finish_reply(dialogue_env, conversation_id, turn_id, "Eggs it is: SECRET-REPLY-MARKER.")
    _drain()  # the head now holds the reply; the late shadow worker must not see it
    assert _head(dialogue_env, conversation_id).through_conversation_sequence == 4
    assert shadow_worker.run_once()
    job, trace = _shadow_rows(dialogue_env, turn_id)
    assert job.capture_schema_version == CAPTURE_SCHEMA_VERSION_V03
    assert trace.terminal_status == "completed", trace.failure_payload
    capture = CognitiveShadowCapture.model_validate(job.capture_payload)
    context = capture.dialogue.turn_context
    assert context.prior_context_projection.through_conversation_sequence == 2 == context.future_boundary_sequence - 1
    request = CognitivePipelineRequest.model_validate(trace.compiled_request_payload)
    assert request.schema_version == 3 and str(reply_id) not in {m.message_id for m in request.messages}
    rendered = json.dumps(job.capture_payload) + json.dumps(trace.compiled_request_payload)
    assert "SECRET-REPLY-MARKER" not in rendered
    cue = context.current_interpretation_projection.cues[0]
    assert cue.cue_kind == DialogueCueKind.ORDINAL_OPTION and cue.resolution.value == "resolved"
    with dialogue_env.db() as db:
        report = shadow_service.replay_terminal(db, user_id=user_id, terminal_id=trace.id)
    assert report.status == ReplayStatus.MATCH


def test_capture_after_the_head_moved_on_uses_the_committed_state_before_the_focus(dialogue_env) -> None:
    user_id, conversation_id = _seed_conversation(dialogue_env)
    message_id, turn_id, job_id = _live_turn(dialogue_env, conversation_id, "The first one.")
    _finish_reply(dialogue_env, conversation_id, turn_id, "Oatmeal it is.")
    _drain()  # eager updates already consumed the focus and its reply
    assert _head(dialogue_env, conversation_id).through_conversation_sequence == 4
    assert _capture(dialogue_env, conversation_id, user_id, message_id, turn_id, job_id)
    job, _ = _shadow_rows(dialogue_env, turn_id)
    context = CognitiveShadowCapture.model_validate(job.capture_payload).dialogue.turn_context
    snapshot = context.prior_context_projection
    assert snapshot.through_conversation_sequence == 2
    assert all(item.last_supported_sequence <= 2 for item in snapshot.items)


def test_shadow_candidate_never_enters_dialogue_memory(dialogue_env, monkeypatch) -> None:
    from app.services import cognitive_shadow as shadow_service
    from app.workers import cognitive_shadow as shadow_worker
    from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0
    from realization_v0_support import ScriptedModel

    shadow_env_candidate = "Hello there."
    dialogue_env.configure(cognitive_shadow_realization_enabled=True,
                           cognitive_shadow_realization_model="fake-realizer-1")
    model = ScriptedModel(shadow_env_candidate)
    monkeypatch.setattr(shadow_service, "realization_engine", lambda settings=None: RealizationEngineV0(model))
    with dialogue_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        user_id, conversation_id = user.id, conversation.id
    message_id, turn_id, job_id = _live_turn(dialogue_env, conversation_id, "hi")
    assert _capture(dialogue_env, conversation_id, user_id, message_id, turn_id, job_id)
    assert shadow_worker.run_once()
    _, trace = _shadow_rows(dialogue_env, turn_id)
    assert trace.realization_status in {"accepted_shadow", "rejected"}
    candidate = trace.realization_trace_payload["final_candidate"] or trace.realization_trace_payload["attempts"][-1][
        "candidate"]
    assert candidate["text"] == shadow_env_candidate
    delivered = "Hi! What can I help you with today?"
    reply_id = _finish_reply(dialogue_env, conversation_id, turn_id, delivered)
    _drain()
    head = _head(dialogue_env, conversation_id)
    rendered = json.dumps(head.model_dump(mode="json"))
    assert shadow_env_candidate not in rendered  # the shadow wording is never remembered as speech
    replies = [item for item in head.items if isinstance(item, DialogueUtteranceReference)
               and item.speaker.value == "columbina"]
    assert [item.message_ref for item in replies] == [str(reply_id)]
    assert replies[0].quote.text == delivered  # the actually delivered reply, with Columbina attribution
    with dialogue_env.db() as db:
        report = shadow_service.replay_realization_terminal(db, user_id=user_id, terminal_id=trace.id)
    assert report.status == ReplayStatus.MATCH  # rebuilt from the capture, without a provider call
    assert len(model.payloads) == 1
