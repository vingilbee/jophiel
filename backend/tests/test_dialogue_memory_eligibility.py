"""Message eligibility and exposure policy (section 24.3): one test per policy row."""

from __future__ import annotations

import pytest

from app.ai.Cognitive.system.v0.contracts.dialogue_memory import (
    DialogueExposureState as E,
    DialogueGap,
    DialogueGapKind,
    DialogueUtteranceReference,
)
from app.ai.Cognitive.system.v0.dialogue_memory_v0 import resolve_exposure
from app.ai.Cognitive.system.v0.dialogue_memory_v0.models import DeliveryFact, DialogueMessageRecord
from dialogue_memory_support import CREATED_AT, Script, uid


def _record(role: str, content: str, *, status: str = "completed", channel: str = "web",
            deliveries: tuple[tuple[int, str], ...] = ()) -> DialogueMessageRecord:
    return DialogueMessageRecord(
        message_ref=uid("eligibility", role, content, status, channel), conversation_ref=uid("eligibility"),
        conversation_sequence=1, turn_ref=uid("turn"), role=role, status=status, source_channel=channel,
        content=content, created_at=CREATED_AT,
        deliveries=tuple(DeliveryFact(delivery_ref=uid("delivery", str(index)), channel="whatsapp",
                                      part_index=index, status=state) for index, state in deliveries),
    )


@pytest.mark.parametrize("channel", ["web", "voice", "whatsapp", "system"])
def test_user_messages_on_every_supported_channel_are_durably_visible(channel) -> None:
    assert resolve_exposure(_record("user", "hello", channel=channel)) == (E.DURABLY_VISIBLE, ())


@pytest.mark.parametrize(("status", "content", "expected"), [
    ("completed", "Here you go.", E.DURABLY_VISIBLE),     # web completed, committed to history
    ("partial", "Here you", E.PARTIALLY_VISIBLE),         # non-empty partial: exact text + incomplete marker
    ("cancelled", "", E.NOT_VISIBLE),                     # empty cancellation: metadata only
    ("failed", "I couldn't complete that.", E.PARTIALLY_VISIBLE),  # failed but shown in durable history
    ("failed", "", E.NOT_VISIBLE),                        # invisible failure
    ("completed", "", E.NOT_VISIBLE),
])
@pytest.mark.parametrize("channel", ["web", "voice"])
def test_streamed_assistant_outcomes(channel, status, content, expected) -> None:
    assert resolve_exposure(_record("assistant", content, status=status, channel=channel))[0] == expected


@pytest.mark.parametrize(("parts", "expected"), [
    (((0, "pending"),), E.DELIVERY_PENDING),
    (((0, "sending"),), E.DELIVERY_PENDING),
    (((0, "accepted"),), E.DELIVERY_PENDING),   # the provider may still report a failure
    (((0, "sent"),), E.DELIVERY_PENDING),
    (((0, "delivered"),), E.DURABLY_VISIBLE),
    (((0, "read"), (1, "delivered")), E.DURABLY_VISIBLE),
    (((0, "delivered"), (1, "pending")), E.DELIVERY_PENDING),
    (((0, "delivered"), (1, "failed")), E.DELIVERY_FAILED),  # exclude utterance, keep the failure event
    (((0, "failed"),), E.DELIVERY_FAILED),
    (((1, "delivered"),), E.VISIBILITY_UNKNOWN),  # missing part 0 cannot prove the text was sent
])
def test_whatsapp_delivery_mapping(parts, expected) -> None:
    exposure, evidence = resolve_exposure(_record("assistant", "Reply text.", channel="whatsapp", deliveries=parts))
    assert exposure == expected
    assert len(evidence) == len(parts) and all(item.startswith("message-delivery:") for item in evidence)


def test_a_delivered_whatsapp_failure_text_is_partial_not_complete() -> None:
    record = _record("assistant", "I couldn't complete that response.", status="failed", channel="whatsapp",
                     deliveries=((0, "delivered"),))
    assert resolve_exposure(record)[0] == E.PARTIALLY_VISIBLE


@pytest.mark.parametrize("channel", ["whatsapp", "system"])
def test_delivery_channel_output_without_a_delivery_record_is_unknown(channel) -> None:
    assert resolve_exposure(_record("assistant", "text", channel=channel))[0] == E.VISIBILITY_UNKNOWN


def test_unknown_channels_fail_closed() -> None:
    assert resolve_exposure(_record("user", "hi", channel="carrier-pigeon"))[0] == E.VISIBILITY_UNKNOWN
    assert resolve_exposure(_record("assistant", "hi", channel="carrier-pigeon"))[0] == E.VISIBILITY_UNKNOWN


def _utterances(snapshot, speaker="columbina"):
    return [item for item in snapshot.items if isinstance(item, DialogueUtteranceReference)
            and item.speaker.value == speaker]


def _gaps(snapshot, kind):
    return [item for item in snapshot.items if isinstance(item, DialogueGap) and item.gap_kind == kind]


def test_memory_behaviour_for_each_exposure_state() -> None:
    script = Script("exposure-behaviour")
    script.exchange(1, "First?", "Complete answer.")
    script.exchange(2, "Second?", "Partial ans", status="partial")
    script.exchange(3, "Third?", "", status="cancelled")
    script.exchange(4, "Fourth?", "", status="failed")
    script.exchange(5, "Fifth?", "Queued reply.", channel="whatsapp", deliveries=((0, "sent"),))
    script.exchange(6, "Sixth?", "Lost reply.", channel="whatsapp", deliveries=((0, "failed"),))
    script.exchange(7, "Seventh?", "Mystery reply.", channel="pigeon")
    snapshot = script.snapshot()
    utterances = {item.message_sequence: item for item in _utterances(snapshot)}
    assert set(utterances) == {2, 4}  # only exposed assistant output is remembered as speech
    assert utterances[2].completeness == "complete" and utterances[4].completeness == "partial"
    assert len(_utterances(snapshot, "user")) == 7  # the user's statements are never lost
    assert [gap.subject_message_ref for gap in _gaps(snapshot, DialogueGapKind.RESPONSE_FAILED)] == [
        script.records[7].message_ref]
    assert [gap.subject_message_ref for gap in _gaps(snapshot, DialogueGapKind.DELIVERY_PENDING)] == [
        script.records[9].message_ref]
    assert [gap.subject_message_ref for gap in _gaps(snapshot, DialogueGapKind.DELIVERY_FAILED)] == [
        script.records[11].message_ref]
    assert [gap.subject_message_ref for gap in _gaps(snapshot, DialogueGapKind.VISIBILITY_UNKNOWN)] == [
        script.records[13].message_ref]
    # The cancelled empty outcome was consumed (counted) but produced no item.
    assert snapshot.source_event_count == 14
    assert not any(script.records[5].message_ref in item.source_message_refs for item in snapshot.items)


def test_shadow_candidates_are_not_chat_messages_and_cannot_be_loaded() -> None:
    """The policy reads only persisted chat rows; there is no path for a shadow candidate."""

    import inspect

    from app.ai.Cognitive.system.v0.dialogue_memory_v0 import eligibility
    from app.services import dialogue_memory

    for module in (eligibility, dialogue_memory):
        source = inspect.getsource(module)
        for forbidden in ("CognitiveShadowTrace", "realization_trace", "final_candidate", "accepted_shadow"):
            assert forbidden not in source
