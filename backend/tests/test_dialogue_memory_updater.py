"""Pure deterministic updater, bootstrap, and ordering (sections 24.2 and 24.4)."""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path

import pytest

from app.ai.Cognitive.system.v0.contracts.dialogue_memory import (
    DialogueCommitmentReference,
    DialogueCorrection,
    DialogueCueKind,
    DialogueEntity,
    DialogueExposureState,
    DialogueGap,
    DialogueGapKind,
    DialogueHistoryOmission,
    DialogueItemStatus,
    DialogueMemoryFailureCode,
    DialogueOmissionReason,
    DialogueOptionSet,
    DialoguePlanReference,
    DialogueProposition,
    DialogueQuestion,
    DialogueRequest,
    DialogueResetReason,
    DialogueResolution,
    DialogueTopic,
    DialogueUtteranceReference,
)
from app.ai.Cognitive.system.v0.dialogue_memory_v0 import (
    DialogueMemoryError,
    DialogueMemoryLimits,
    DialogueMemoryUpdaterV0,
    bootstrap,
    dialogue_state_digest,
    select_window,
    source_event,
)
from app.ai.Cognitive.system.v0.dialogue_memory_v0.ordering import backfill_sort_key, validate_records
from dialogue_memory_support import Script, uid

PACKAGE = Path(__file__).resolve().parents[1] / "app/ai/Cognitive/system/v0/dialogue_memory_v0"


def _of(snapshot, cls, **match):
    return [item for item in snapshot.items if isinstance(item, cls)
            and all(getattr(item, key) == value for key, value in match.items())]


def _pets() -> Script:
    script = Script("pets")
    script.exchange(1, "I have a dog named Max and a cat named Luna.", "Lovely! How old are they?")
    script.exchange(2, "Max is three. Can you suggest dinner ideas?",
                    "Here are some options:\n1. Chicken and rice\n2. Salmon\n3. Pumpkin mash\nWhich one?")
    script.exchange(3, "The second one sounds good.", "Great choice. I'll note that.")
    return script


# ---------------------------------------------------------------------------
# Determinism, purity, and bootstrap equivalence
# ---------------------------------------------------------------------------

def test_the_package_imports_no_database_cache_clock_or_provider() -> None:
    forbidden = {"sqlalchemy", "redis", "openai", "anthropic", "langchain", "httpx", "time", "datetime",
                 "random", "app.db", "app.services"}
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # Imports under ``if TYPE_CHECKING:`` never execute (annotations only).
        typing_only = {id(inner) for node in ast.walk(tree) if isinstance(node, ast.If)
                       and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
                       for inner in ast.walk(node)}
        nodes = [node for node in ast.walk(tree) if id(node) not in typing_only]
        names = {alias.name for node in nodes if isinstance(node, ast.Import) for alias in node.names}
        names |= {node.module or "" for node in nodes if isinstance(node, ast.ImportFrom) and node.level == 0}
        assert not {name for name in names if name.split(".")[0] in forbidden or name in forbidden}, path.name


def test_predecessor_plus_events_is_pure_and_deterministic() -> None:
    first, second = _pets().snapshot(), _pets().snapshot()
    assert first == second and first.model_dump_json() == second.model_dump_json()
    updater = DialogueMemoryUpdaterV0()
    script = _pets()
    base = script.incremental([2])[0]
    assert updater.apply(base, script.events(2)) == updater.apply(base, script.events(2))


@pytest.mark.parametrize("batches", [[6], [2, 2, 2], [1, 1, 1, 1, 1, 1], [3, 3], [1, 5]])
def test_bootstrap_equals_incremental_replay_for_the_same_prefix(batches) -> None:
    script = _pets()
    produced = script.incremental(batches)
    final = produced[-1]
    assert dialogue_state_digest(final) == dialogue_state_digest(script.snapshot())
    assert final.revision == len(batches)
    for previous, current in zip(produced, produced[1:]):
        assert current.predecessor_snapshot_ref == previous.snapshot_id and current.segment_id == previous.segment_id


def test_consumed_events_and_sequence_gaps_fail_closed() -> None:
    script = _pets()
    snapshot = script.incremental([2])[0]
    updater = DialogueMemoryUpdaterV0()
    with pytest.raises(DialogueMemoryError) as duplicate:
        updater.apply(snapshot, script.events(1, 3))  # re-applying a consumed event
    assert duplicate.value.code == DialogueMemoryFailureCode.MESSAGE_ORDER_INVALID
    with pytest.raises(DialogueMemoryError) as gap:
        updater.apply(snapshot, script.events(3, 4))  # skips position 3
    assert gap.value.code == DialogueMemoryFailureCode.SOURCE_SEQUENCE_GAP
    assert updater.apply(snapshot, []) is snapshot  # nothing new: no new revision


def test_foreign_events_are_rejected() -> None:
    script, other = _pets(), Script("someone-else")
    other.exchange(1, "Hello there.", "Hi.")
    snapshot = script.incremental([6])[0]
    foreign = [source_event(record.__class__(**{**record.__dict__, "conversation_sequence": 7}),
                            user_ref=other.user) for record in other.records[:1]]
    with pytest.raises(DialogueMemoryError) as error:
        DialogueMemoryUpdaterV0().apply(snapshot, foreign)
    assert error.value.code == DialogueMemoryFailureCode.CONVERSATION_NOT_OWNED


# ---------------------------------------------------------------------------
# Attribution and epistemic policy
# ---------------------------------------------------------------------------

def test_user_claims_stay_attributed_even_when_repeated() -> None:
    script = Script("attributed")
    for turn in range(1, 4):
        script.exchange(turn, "My dog is named Max.", "Noted.")
    snapshot = script.snapshot()
    propositions = _of(snapshot, DialogueProposition)
    assert propositions and all(item.speaker.value == "user" and item.epistemic_status.value == "attributed"
                                for item in propositions)
    assert all(item.epistemic_status.value == "attributed" for item in _of(snapshot, DialogueEntity))


def test_assistant_claims_stay_attributed_references_never_propositions() -> None:
    script = Script("assistant-claims")
    script.exchange(1, "What's the capital of France?", "The capital of France is Paris. I'll double-check it.")
    snapshot = script.snapshot()
    assert not [item for item in _of(snapshot, DialogueProposition) if item.speaker.value != "user"]
    utterance = _of(snapshot, DialogueUtteranceReference, message_ref=script.records[1].message_ref)[0]
    assert (utterance.speaker.value, utterance.epistemic_status.value) == ("columbina", "attributed")
    commitment = _of(snapshot, DialogueCommitmentReference)[0]
    assert commitment.fulfillment == "unknown" and commitment.epistemic_status.value == "attributed"


def test_correction_supersedes_without_erasing_provenance() -> None:
    script = Script("correction")
    script.exchange(1, "My dog is named Max.", "Hi Max!")
    script.exchange(2, "No, I meant Charlie, not Max.", "Sorry, Charlie!")
    snapshot = script.snapshot()
    old = _of(snapshot, DialogueEntity, entity_key="max")[0]
    new = _of(snapshot, DialogueEntity, entity_key="charlie")[0]
    correction = _of(snapshot, DialogueCorrection)[0]
    proposition = _of(snapshot, DialogueProposition)[0]
    assert old.status == DialogueItemStatus.SUPERSEDED and old.superseded_by_ref == new.item_id
    assert proposition.status == DialogueItemStatus.SUPERSEDED
    assert proposition.superseded_by_refs == (correction.item_id,)
    assert set(correction.old_refs) == {old.item_id, proposition.item_id} and correction.new_refs == (new.item_id,)
    assert (correction.old_anchor, correction.new_anchor) == ("Max", "Charlie")
    assert script.records[0].message_ref in correction.source_message_refs  # old evidence kept
    assert snapshot.focus_entity_refs == (new.item_id,)


def test_unresolvable_correction_is_a_typed_gap() -> None:
    script = Script("bad-correction")
    script.exchange(1, "No, I meant Charlie, not Max.", "Okay.")
    gaps = _of(script.snapshot(), DialogueGap, gap_kind=DialogueGapKind.UNRESOLVED_CORRECTION)
    assert len(gaps) == 1 and gaps[0].resolution == DialogueResolution.NO_CANDIDATE


def test_partial_output_never_creates_completion_answer_plan_or_promise_evidence() -> None:
    script = Script("partial")
    script.add("user", "What should I eat? Suggest some options.", turn=1)
    script.add("assistant", "Options:\n1. Rice\n2. Beans\nI'll also", turn=1, status="partial")
    snapshot = script.snapshot()
    question = _of(snapshot, DialogueQuestion)[0]
    assert question.status == DialogueItemStatus.CURRENT  # not answered by a partial reply
    assert _of(snapshot, DialogueRequest)[0].response_message_refs == ()
    assert not _of(snapshot, DialogueOptionSet) and not _of(snapshot, DialogueCommitmentReference)
    utterance = _of(snapshot, DialogueUtteranceReference, message_ref=script.records[1].message_ref)[0]
    assert utterance.completeness == "partial" and utterance.quote is not None  # exact visible text, marked partial


def test_question_and_request_resolution_require_evidence() -> None:
    script = Script("resolution")
    script.exchange(1, "How much protein is in eggs? Please list sources.", "About 6 g each.")
    script.add("user", "Why is that?", turn=2)
    script.add("assistant", "I couldn't complete that response.", turn=2, status="failed")
    script.add("assistant", "By the way, did you eat today?", unprompted=True)
    snapshot = script.snapshot()
    answered = _of(snapshot, DialogueQuestion, speaker=_of(snapshot, DialogueQuestion)[0].speaker)
    first = next(item for item in answered if item.surface_anchors[0].startswith("How much"))
    assert first.status == DialogueItemStatus.RESOLVED and first.resolution_basis == "completed_response_in_turn"
    why = next(item for item in _of(snapshot, DialogueQuestion) if item.surface_anchors[0] == "Why is that?")
    assert why.status == DialogueItemStatus.CURRENT  # a failed reply is not an answer
    request = _of(snapshot, DialogueRequest)[0]
    assert request.status == DialogueItemStatus.CURRENT  # responded to, never presumed fulfilled
    assert request.response_message_refs == (script.records[1].message_ref,)
    nudge = next(item for item in _of(snapshot, DialogueQuestion) if item.speaker.value == "columbina")
    assert nudge.status == DialogueItemStatus.CURRENT  # no user reply yet


def test_topic_change_does_not_resolve_an_open_request() -> None:
    script = Script("topics")
    script.exchange(1, "Please draft a meal plan.", "Working on it.")
    script.exchange(2, "Let's talk about sleep.", "Sure.")
    snapshot = script.snapshot()
    assert _of(snapshot, DialogueRequest, surface_anchors=("Please draft a meal plan.",))[0].is_current
    assert _of(snapshot, DialogueTopic)[0].topic_key == "sleep"


# ---------------------------------------------------------------------------
# Reference resolution
# ---------------------------------------------------------------------------

def _gaps(snapshot, kind=DialogueGapKind.UNRESOLVED_REFERENCE):
    return _of(snapshot, DialogueGap, gap_kind=kind)


def test_pronoun_with_one_antecedent_resolves() -> None:
    script = Script("one-antecedent")
    script.exchange(1, "I adopted a puppy named Rex.", "Congratulations!")
    script.add("user", "What should I feed him?", turn=2)
    snapshot = script.snapshot()
    rex = _of(snapshot, DialogueEntity, entity_key="rex")[0]
    utterance = _of(snapshot, DialogueUtteranceReference, message_ref=script.records[2].message_ref)[0]
    assert utterance.refers_to_refs == (rex.item_id,) and not _gaps(snapshot)


def test_ambiguous_pronoun_stays_unresolved_and_never_picks_the_newest() -> None:
    script = Script("two-antecedents")
    script.exchange(1, "I have a dog named Max and a cat named Luna.", "Lovely.")
    script.add("user", "What should I feed it?", turn=2)
    snapshot = script.snapshot()
    gap = _gaps(snapshot)[0]
    names = {item.item_id: item.entity_key for item in _of(snapshot, DialogueEntity)}
    assert gap.resolution == DialogueResolution.AMBIGUOUS
    assert {names[ref] for ref in gap.candidate_refs} == {"max", "luna"}
    assert gap.cue_kind == DialogueCueKind.PRONOUN_SINGULAR and gap.item_id in snapshot.unresolved_reference_refs


def test_plural_pronoun_resolves_to_the_group_within_the_utterance() -> None:
    script = Script("plural")
    script.add("user", "I have a dog named Max and a cat named Luna. What should I feed them?", turn=1)
    snapshot = script.snapshot()
    utterance = _of(snapshot, DialogueUtteranceReference)[0]
    assert len(utterance.refers_to_refs) == 2 and not _gaps(snapshot)


def test_ordinal_option_resolves_against_the_active_option_set() -> None:
    snapshot = _pets().snapshot()
    option_set = _of(snapshot, DialogueOptionSet)[0]
    utterance = _of(snapshot, DialogueUtteranceReference, message_ref=_pets().records[4].message_ref)[0]
    assert utterance.refers_to_refs == (option_set.options[1].option_ref,)
    assert snapshot.active_option_set_refs == (option_set.item_id,)


def test_ordinal_without_option_set_and_continue_without_plan_are_gaps() -> None:
    script = Script("no-set")
    script.exchange(1, "Use the second option.", "Which options?")
    script.exchange(2, "Yes, continue.", "Continue what?")
    kinds = {(gap.cue_kind, gap.resolution) for gap in _gaps(script.snapshot())}
    assert kinds == {(DialogueCueKind.ORDINAL_OPTION, DialogueResolution.NO_CANDIDATE),
                     (DialogueCueKind.CONTINUATION, DialogueResolution.NO_CANDIDATE)}


def test_continue_with_an_active_plan() -> None:
    script = Script("plan")
    script.exchange(1, "Help me plan my week.", "Here is a plan:\n1. Shop on Monday\n2. Cook on Tuesday")
    script.exchange(2, "Yes, continue.", "Next, freeze portions.")
    snapshot = script.snapshot()
    plan = _of(snapshot, DialoguePlanReference)[0]
    assert plan.continuation_message_refs == (script.records[2].message_ref,) and plan.progress == "unknown"
    assert not _gaps(snapshot)


def test_withdrawal_and_return_to_a_prior_topic() -> None:
    script = Script("withdraw")
    script.exchange(1, "Let's talk about protein.", "Sure.")
    script.exchange(2, "Please plan a grocery list.", "Okay.")
    script.exchange(3, "Speaking of sleep, I sleep badly.", "Sorry to hear.")
    script.exchange(4, "Never mind the list. Back to protein.", "Protein it is.")
    snapshot = script.snapshot()
    request = _of(snapshot, DialogueRequest, surface_anchors=("Please plan a grocery list.",))[0]
    assert request.status == DialogueItemStatus.WITHDRAWN
    assert request.withdrawn_by_message_ref == script.records[6].message_ref
    protein = _of(snapshot, DialogueTopic, topic_key="protein")[0]
    assert protein.return_message_refs == (script.records[6].message_ref,)
    assert snapshot.active_topic_refs[0] == protein.item_id  # most recently (re)activated first


def test_prior_statement_queries_resolve_to_exact_prior_utterances() -> None:
    script = Script("queries")
    script.exchange(1, "I ran 5 km today.", "Nice run!")
    script.add("user", "What did I just tell you? And what did you say before?", turn=2)
    snapshot = script.snapshot()
    utterance = _of(snapshot, DialogueUtteranceReference, message_ref=script.records[2].message_ref)[0]
    refs = {_of(snapshot, DialogueUtteranceReference, item_id=ref)[0].message_ref for ref in utterance.refers_to_refs}
    assert refs == {script.records[0].message_ref, script.records[1].message_ref}


# ---------------------------------------------------------------------------
# Late delivery and bounds
# ---------------------------------------------------------------------------

def test_pending_delivery_settles_into_a_later_snapshot_with_original_provenance() -> None:
    script = Script("late")
    script.exchange(1, "Hi from WhatsApp.", "Hello!", channel="whatsapp", deliveries=((0, "sent"),))
    pending = script.snapshot()
    gap = _gaps(pending, DialogueGapKind.DELIVERY_PENDING)[0]
    record = script.records[1]
    delivered = source_event(record.__class__(**{**record.__dict__, "deliveries": (
        record.deliveries[0].__class__(**{**record.deliveries[0].__dict__, "status": "delivered"}),)}),
        user_ref=script.user)
    assert delivered.exposure_state == DialogueExposureState.DURABLY_VISIBLE
    settled = DialogueMemoryUpdaterV0().apply(pending, [], exposure_updates=[delivered])
    assert settled.through_conversation_sequence == pending.through_conversation_sequence
    assert settled.source_prefix_digest == pending.source_prefix_digest
    assert _of(settled, DialogueGap, item_id=gap.item_id)[0].status == DialogueItemStatus.RESOLVED
    utterance = _of(settled, DialogueUtteranceReference, message_ref=record.message_ref)[0]
    assert utterance.exposure_evidence_refs == delivered.exposure_evidence_refs
    with pytest.raises(DialogueMemoryError):  # nothing pending any more
        DialogueMemoryUpdaterV0().apply(settled, [], exposure_updates=[delivered])


def test_compaction_preserves_active_dependencies_and_records_omission() -> None:
    script = Script("compaction")
    script.exchange(1, "Please draft a meal plan.", "Here is a plan:\n1. Breakfast\n2. Lunch")
    for turn in range(2, 30):
        script.exchange(turn, f"Note number {turn} about my day.", "Thanks.")
    limits = DialogueMemoryLimits(max_items=40)
    snapshot = script.snapshot(limits=limits)
    assert len(snapshot.items) <= 40
    assert _of(snapshot, DialogueRequest)[0].is_current and _of(snapshot, DialoguePlanReference)
    compaction = [item for item in snapshot.omitted_history if item.reason == DialogueOmissionReason.COMPACTED]
    assert len(compaction) == 1 and compaction[0].recoverable
    # Eager (one transition per turn) and one-shot catch-up agree under compaction too.
    assert dialogue_state_digest(script.incremental([2] * 29, limits=limits)[-1]) == dialogue_state_digest(snapshot)


def test_compaction_fails_closed_when_required_referents_exceed_the_bound() -> None:
    script = Script("too-small")
    script.add("user", "Please plan dinner. Please list snacks. Please draft lunch. Can you help? Why? How?",
               turn=1)
    with pytest.raises(DialogueMemoryError) as error:
        script.snapshot(limits=DialogueMemoryLimits(max_items=3))
    assert error.value.code == DialogueMemoryFailureCode.REQUIRED_DIALOGUE_CONTENT_MISSING


def test_bootstrap_window_records_explicit_omissions() -> None:
    script = Script("window")
    for turn in range(1, 21):
        script.exchange(turn, f"Message {turn}.", "Okay.")
    window = select_window(script.records[-31:], boundary=40, limit=30)
    assert window.window_start == 11 and len(window.records) == 30
    assert window.omissions == (DialogueHistoryOmission(
        reason=DialogueOmissionReason.OUTSIDE_BOOTSTRAP_WINDOW, sequence_start=1, sequence_end=10, recoverable=True),)
    deleted = [record for record in script.records if record.conversation_sequence != 35]
    window = select_window(deleted[-31:], boundary=40, limit=30)
    assert window.window_start == 36
    assert {(item.reason, item.sequence_start, item.sequence_end) for item in window.omissions} == {
        (DialogueOmissionReason.SOURCE_DELETED, 35, 35), (DialogueOmissionReason.OUTSIDE_BOOTSTRAP_WINDOW, 1, 34)}
    events = [source_event(record, user_ref=script.user) for record in window.records]
    snapshot = bootstrap(user_ref=script.user, conversation_ref=script.conversation, events=events,
                         window_start=window.window_start, revision=1,
                         reset_reason=DialogueResetReason.INITIAL_BOOTSTRAP, omissions=window.omissions)
    assert snapshot.segment_start_sequence == 36 and snapshot.omitted_history == window.omissions


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

def test_backfill_order_groups_turns_user_first_with_stable_tie_breaks() -> None:
    same = datetime(2026, 1, 1, 10, 0, 0)
    turn = uid("turn-a")
    rows = [
        ("assistant-a", backfill_sort_key(job_created_at=same, turn_id=turn, role="assistant", created_at=same,
                                          message_id=uid("m1"))),
        ("user-a", backfill_sort_key(job_created_at=same, turn_id=turn, role="user", created_at=same,
                                     message_id=uid("m2"))),
        ("nudge", backfill_sort_key(job_created_at=None, turn_id=None, role="assistant",
                                    created_at=datetime(2026, 1, 1, 9, 0), message_id=uid("m3"))),
    ]
    assert [name for name, key in sorted(rows, key=lambda item: item[1])] == ["nudge", "user-a", "assistant-a"]


def test_record_validation_requires_contiguous_owned_positions() -> None:
    script = _pets()
    validate_records(script.records[2:], after_sequence=2, conversation_ref=script.conversation)
    with pytest.raises(DialogueMemoryError) as gap:
        validate_records(script.records[3:], after_sequence=1, conversation_ref=script.conversation)
    assert gap.value.code == DialogueMemoryFailureCode.SOURCE_SEQUENCE_GAP
    with pytest.raises(DialogueMemoryError) as foreign:
        validate_records(script.records, after_sequence=0, conversation_ref=uid("other"))
    assert foreign.value.code == DialogueMemoryFailureCode.CONVERSATION_NOT_OWNED
