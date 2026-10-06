"""Dialogue Working Memory V0 contracts (section 24.1)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.ai.Cognitive.system.v0.contracts.dialogue_memory import (
    DIALOGUE_MEMORY_CACHE_VERSION,
    MAX_EXACT_QUOTES,
    DialogueEntity,
    DialogueGap,
    DialogueGapKind,
    DialogueItemStatus,
    DialogueMemorySnapshot,
    DialogueMemoryVersions,
    DialogueProposition,
    DialogueQuestion,
    DialogueRequest,
    DialogueSourceEvent,
    DialogueTurnContext,
    DialogueUtteranceReference,
    canonical_json,
    sha256_text,
    snapshot_semantic_digest,
    source_event_id_for,
)
from app.ai.Cognitive.system.v0.dialogue_memory_v0 import source_event
from dialogue_memory_support import Script, uid

BACKEND = Path(__file__).resolve().parents[1]


def _script() -> Script:
    script = Script("contract")
    script.exchange(1, "I have a dog named Max. Can you suggest a name for my cat?",
                    "Here are some ideas:\n1. Luna\n2. Milo\nWhich do you like?")
    script.exchange(2, "No, I meant Charlie, not Max.", "Got it, Charlie. I'll keep that in mind.")
    return script


def test_versions_are_exact_and_the_cache_is_not_semantic() -> None:
    assert DialogueMemoryVersions().model_dump() == {
        "contract": "dialogue-working-memory-contract-v0.1", "item": "dialogue-working-memory-item-v0.1",
        "message_order": "dialogue-message-order-v0.1", "message_eligibility": "dialogue-message-eligibility-v0.1",
        "updater": "dialogue-working-memory-updater-v0.1", "projection": "dialogue-working-memory-projection-v0.1",
        "semantic_digest": "dialogue-working-memory-digest-v0.1",
    }
    assert DIALOGUE_MEMORY_CACHE_VERSION == "dialogue-working-memory-cache-v0.1"
    with pytest.raises(ValidationError):
        DialogueMemoryVersions(updater="dialogue-working-memory-updater-v0.2")


def test_snapshot_is_immutable_forbids_extras_and_round_trips_exactly() -> None:
    snapshot = _script().snapshot()
    with pytest.raises(ValidationError):
        snapshot.revision = 7  # type: ignore[misc]
    payload = snapshot.model_dump(mode="json")
    assert DialogueMemorySnapshot.model_validate_json(canonical_json(payload)) == snapshot
    with pytest.raises(ValidationError):
        DialogueMemorySnapshot.model_validate({**payload, "raw_transcript": "anything"})
    item = payload["items"][0]
    with pytest.raises(ValidationError):
        DialogueMemorySnapshot.model_validate({**payload, "items": [{**item, "orm_object": {}}, *payload["items"][1:]]})


def test_snapshot_identity_and_digest_are_derived_and_checked() -> None:
    snapshot = _script().snapshot()
    payload = snapshot.model_dump(mode="json")
    assert snapshot.semantic_digest == snapshot_semantic_digest(snapshot)
    for mutation in (
        {"semantic_digest": "0" * 64},
        {"snapshot_id": uid("forged")},
        {"source_prefix_digest": "not-a-digest"},
        {"through_conversation_sequence": 1},  # items cite later evidence
        {"user_ref": "USER"},
        {"conversation_ref": ""},
    ):
        with pytest.raises(ValidationError):
            DialogueMemorySnapshot.model_validate({**payload, **mutation})


def test_indexes_duplicate_refs_and_lifecycle_are_validated() -> None:
    snapshot = _script().snapshot()
    payload = snapshot.model_dump(mode="json")
    entity = next(item for item in payload["items"] if item["item_type"] == "entity")
    for mutation in (
        {"focus_entity_refs": [entity["item_id"], entity["item_id"]]},
        {"open_question_refs": [entity["item_id"]]},  # wrong item type
        {"current_item_refs": []},
        {"items": [*payload["items"], payload["items"][0]]},
    ):
        with pytest.raises(ValidationError):
            DialogueMemorySnapshot.model_validate({**payload, **mutation})


def _item_payload(snapshot: DialogueMemorySnapshot, cls):
    return next(item for item in snapshot.items if isinstance(item, cls)).model_dump(mode="python")


def test_epistemic_and_lifecycle_rules_cannot_be_forged() -> None:
    snapshot = _script().snapshot()
    proposition = _item_payload(snapshot, DialogueProposition)
    with pytest.raises(ValidationError):
        DialogueProposition(**{**proposition, "speaker": "columbina"})  # legacy agent text is never a proposition
    with pytest.raises(ValidationError):
        DialogueProposition(**{**proposition, "epistemic_status": "observed"})  # never promoted to fact
    with pytest.raises(ValidationError):
        DialogueProposition(**{**proposition, "status": "superseded", "superseded_by_refs": ()})
    question = _item_payload(snapshot, DialogueQuestion)
    assert question["status"] == DialogueItemStatus.RESOLVED  # the user's reply closed Columbina's question
    with pytest.raises(ValidationError):
        DialogueQuestion(**{**question, "resolution_basis": None, "resolved_by_message_ref": None})
    with pytest.raises(ValidationError):
        DialogueQuestion(**{**question, "resolution_basis": "completed_response_in_turn"})  # wrong evidence kind
    request = _item_payload(snapshot, DialogueRequest)
    with pytest.raises(ValidationError):
        DialogueRequest(**{**request, "status": "resolved"})  # fulfilment is never inferred
    with pytest.raises(ValidationError):
        DialogueRequest(**{**request, "status": "withdrawn"})  # withdrawal cites its message
    entity = _item_payload(snapshot, DialogueEntity)
    with pytest.raises(ValidationError):
        DialogueEntity(**{**entity, "surface_anchors": ("a longer anchor than its span",)})


def test_partial_output_cannot_be_a_complete_utterance() -> None:
    script = Script("partial-contract")
    script.add("user", "Tell me a story.", turn=1)
    script.add("assistant", "Once upon a", turn=1, status="partial")
    utterance = [item for item in script.snapshot().items if isinstance(item, DialogueUtteranceReference)
                 and item.speaker.value == "columbina"][0]
    assert (utterance.completeness, utterance.terminal_status.value) == ("partial", "partial")
    with pytest.raises(ValidationError):
        DialogueUtteranceReference(**{**utterance.model_dump(mode="python"), "completeness": "complete"})
    with pytest.raises(ValidationError):
        DialogueUtteranceReference(**{**utterance.model_dump(mode="python"), "exposure_state": "not_visible"})


def test_source_events_bind_content_digest_position_and_exposure() -> None:
    record = _script().records[0]
    event = source_event(record, user_ref=uid("contract", "user"))
    payload = event.model_dump(mode="python")
    assert event.content_digest == sha256_text(record.content)
    assert event.event_id == source_event_id_for(event.conversation_ref, event.message_ref, 1, event.exposure_state)
    for mutation in ({"content": "tampered"}, {"conversation_sequence": 2}, {"user_ref": "nobody"},
                     {"status": "partial"}, {"exposure_state": "delivery_pending"}, {"event_id": uid("x")}):
        with pytest.raises(ValidationError):
            DialogueSourceEvent(**{**payload, **mutation})


def test_gaps_keep_competing_candidates_and_never_claim_resolution() -> None:
    script = Script("gap-contract")
    script.add("user", "I met Anna and Maria yesterday.", turn=1)
    script.add("user", "Should I call her?", turn=2)
    gap = next(item for item in script.snapshot().items if isinstance(item, DialogueGap))
    assert gap.gap_kind == DialogueGapKind.UNRESOLVED_REFERENCE and len(gap.candidate_refs) == 2
    payload = gap.model_dump(mode="python")
    with pytest.raises(ValidationError):
        DialogueGap(**{**payload, "resolution": "resolved"})
    with pytest.raises(ValidationError):
        DialogueGap(**{**payload, "candidate_refs": payload["candidate_refs"][:1]})
    with pytest.raises(ValidationError):
        DialogueGap(**{**payload, "speaker": "runtime"})


def test_turn_context_binds_snapshot_focus_and_future_boundary() -> None:
    script = _script()
    context, _ = script.context("What about the second one?")
    payload = context.model_dump(mode="python")
    assert DialogueTurnContext.model_validate_json(canonical_json(context.model_dump(mode="json"))) == context
    for mutation in ({"future_boundary_sequence": 99}, {"committed_snapshot_digest": "0" * 64},
                     {"focus_conversation_sequence": 2}, {"cognitive_turn_id": uid("other-turn")},
                     {"context_id": uid("forged-context")}):
        with pytest.raises(ValidationError):
            DialogueTurnContext(**{**payload, **mutation})


def test_quote_bound_is_a_contract_invariant() -> None:
    script = Script("quotes")
    for index in range(MAX_EXACT_QUOTES + 4):
        script.add("user", f"Short message number {index}.", turn=index)
    snapshot = script.snapshot()
    quoted = [item for item in snapshot.items if isinstance(item, DialogueUtteranceReference) and item.quote]
    assert len(quoted) == MAX_EXACT_QUOTES
    assert min(item.message_sequence for item in quoted) == len(script.records) - MAX_EXACT_QUOTES + 1


def test_identities_are_hash_seed_and_process_invariant() -> None:
    probe = (
        "import sys; sys.path.insert(0, 'tests');"
        "from test_dialogue_memory_contract import _script; s = _script().snapshot();"
        "print(s.snapshot_id, s.semantic_digest, s.source_prefix_digest)"
    )
    outputs = {
        subprocess.run([sys.executable, "-c", probe], cwd=BACKEND, capture_output=True, text=True, check=True,
                       env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(BACKEND)}).stdout.strip()
        for seed in ("0", "1", "31337")
    }
    snapshot = _script().snapshot()
    assert outputs == {f"{snapshot.snapshot_id} {snapshot.semantic_digest} {snapshot.source_prefix_digest}"}


def test_snapshot_json_carries_no_generic_transcript_field() -> None:
    payload = _script().snapshot().model_dump(mode="json")
    assert not {"messages", "transcript", "content", "history"} & set(payload)
    for item in payload["items"]:
        assert "content" not in item
    assert json.dumps(payload)  # canonical JSON-safe values only
    assert DialogueItemStatus.CURRENT.value in {item["status"] for item in payload["items"]}
