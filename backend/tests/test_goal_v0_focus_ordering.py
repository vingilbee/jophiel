"""Goal V0 focus keeps semantic ranking separate from canonical record order.

``GoalEngine._focus`` ranks active goals semantically to choose the primary and
supporting goals, while ``GoalSnapshot`` requires ``non_focal_active_goal_refs``
in record order.  These tests construct the conflicting arrangement explicitly
(goal identities that sort opposite to creation order) rather than relying on
hash-derived identities happening to disagree.
"""

from __future__ import annotations

import copy
import hashlib
import uuid

import pytest

from app.ai.Cognitive.system.v0.contracts.goals import (
    GoalSnapshot,
    GoalStatus,
    GoalTurnRollover,
    stale_turn_scoped_goals,
)
from app.ai.Cognitive.system.v0.goal_v0 import GoalEngine
from app.ai.Cognitive.system.v0.goal_v0.fixture_adapter import build_request, load_design_fixture
from app.ai.Cognitive.system.v0.live_shadow.models import TerminalStatus
from test_goal_design_cases import FIXTURE
from test_cognitive_shadow_compiler import _as_predecessor, _observe
from cognitive_shadow_support import make_capture

RESPONSE = "interaction.discharge_response_obligation"
ACKNOWLEDGE = "interaction.acknowledge_relevant_information"


def _inverted_identity(kind: str, parts: tuple[str, ...]) -> str:
    """Later turns receive lexically smaller goal ids than earlier turns.

    Goal ids are the semantic tie-breaker in focus ranking, so this makes the
    semantic order run opposite to record (creation) order.
    """

    if kind == "goal":
        turn = int(parts[0].rsplit("-", 1)[1])
        return f"goal-{9 - turn}-{parts[1]}"
    return f"{kind}-{hashlib.sha256('|'.join(parts).encode()).hexdigest()[:20]}"


def _goal(turn: int, definition_key: str) -> str:
    return f"goal-{9 - turn}-{definition_key}"


def _turn_case(base: dict, turn: int) -> dict:
    case = copy.deepcopy(base)
    case["turn_context"].update(
        cognitive_turn_id=f"order-turn-{turn}",
        runtime_snapshot_ref=f"order-runtime-{turn}",
        response_obligation_ref=f"order-response-obligation-{turn}",
    )
    case["interpretation_input"]["interpretation_trace_ref"] = f"order-interpretation-{turn}"
    case["conversation"]["conversation_ref"] = "order-conversation"
    # Each turn acknowledges a distinct proposition, so its conversation-scoped
    # goal is a new commitment rather than a duplicate of an earlier one.
    for frame in case["interpretation_input"]["semantic_frames"]:
        frame["arguments"]["proposition_ref"] = f"order-proposition-{turn}"
    case["new_goal_evidence"] = []
    return case


def _rollover(previous: GoalSnapshot, case: dict) -> GoalTurnRollover | None:
    turn = case["turn_context"]
    if not stale_turn_scoped_goals(previous, turn["cognitive_turn_id"]):
        return None
    return GoalTurnRollover(
        rollover_id=f"order-rollover-{turn['cognitive_turn_id']}", proof_kind="successor_response_turn",
        producer="test-trusted-runtime", conversation_ref=previous.conversation_ref,
        closed_turn_ref=previous.cognitive_turn_id, closed_goal_snapshot_ref=previous.goal_snapshot_id,
        closing_turn_ref=turn["cognitive_turn_id"],
        closing_response_obligation_ref=turn["response_obligation_ref"],
    )


def _record_order_non_focal(snapshot: GoalSnapshot) -> tuple[str, ...]:
    return tuple(
        record.goal_id
        for record in snapshot.records
        if record.status == GoalStatus.ACTIVE and record.goal_id not in snapshot.focal_goal_refs
    )


def _chain(turns: int):
    base = next(
        case for case in load_design_fixture(FIXTURE)["cases"]
        if case["case_id"] == "acknowledgement"
    )
    engine = GoalEngine(identity_factory=_inverted_identity)
    previous = None
    for turn in range(1, turns + 1):
        case = _turn_case(base, turn)
        rollover = _rollover(previous, case) if previous is not None else None
        request = build_request(case, previous_snapshot=previous, turn_rollover=rollover)
        result = engine.update(request)
        previous = result.snapshot
    return engine, request, result


def test_non_focal_refs_follow_record_order_while_focus_keeps_semantic_ranking() -> None:
    _, _, result = _chain(3)
    snapshot = result.snapshot

    # Records are in creation order; ids sort in the opposite direction.
    assert tuple(record.goal_id for record in snapshot.records) == (
        _goal(1, RESPONSE), _goal(1, ACKNOWLEDGE),
        _goal(2, RESPONSE), _goal(2, ACKNOWLEDGE),
        _goal(3, RESPONSE), _goal(3, ACKNOWLEDGE),
    )
    # Earlier turns' response obligations expired at rollover; the
    # conversation-scoped acknowledgements legitimately persist.
    status = {record.goal_id: record.status for record in snapshot.records}
    assert status[_goal(1, RESPONSE)] == status[_goal(2, RESPONSE)] == GoalStatus.EXPIRED
    assert all(status[_goal(turn, ACKNOWLEDGE)] == GoalStatus.ACTIVE for turn in (1, 2, 3))
    assert status[_goal(3, RESPONSE)] == GoalStatus.ACTIVE

    # Semantic focus: the best-ranked acknowledgement, supported by this turn's
    # response obligation.  Record order would instead have chosen turn 1's.
    assert snapshot.focus_decision.primary_goal_ref == _goal(3, ACKNOWLEDGE)
    assert snapshot.focus_decision.supporting_goal_refs == (_goal(3, RESPONSE),)
    assert snapshot.focal_goal_refs == (_goal(3, ACKNOWLEDGE), _goal(3, RESPONSE))

    # Two non-focal goals of one definition, whose semantic order (ascending
    # id) differs from record order.
    non_focal = snapshot.focus_decision.non_focal_active_goal_refs
    assert non_focal == (_goal(1, ACKNOWLEDGE), _goal(2, ACKNOWLEDGE))
    assert non_focal == _record_order_non_focal(snapshot)
    assert non_focal != tuple(sorted(non_focal))
    active = tuple(record.goal_id for record in snapshot.records if record.status == GoalStatus.ACTIVE)
    assert snapshot.active_goal_refs == active
    assert (*snapshot.focal_goal_refs, *non_focal) != snapshot.active_goal_refs
    assert sorted((*snapshot.focal_goal_refs, *non_focal)) == sorted(snapshot.active_goal_refs)


@pytest.mark.parametrize("arrangement", ("engine", "reversed", "interleaved"))
def test_each_record_arrangement_emits_its_own_record_order(arrangement: str) -> None:
    engine, request, result = _chain(3)
    snapshot = result.snapshot
    records = list(snapshot.records)
    arranged = {
        "engine": records,
        "reversed": records[::-1],
        "interleaved": records[1::2] + records[0::2],
    }[arrangement]

    focus = engine._focus(request, arranged, snapshot.conflicts, snapshot.focus_decision.route)

    # The semantic choice and its provenance are independent of record arrangement ...
    assert focus.primary_goal_ref == _goal(3, ACKNOWLEDGE)
    assert focus.supporting_goal_refs == (_goal(3, RESPONSE),)
    assert focus.coordination_floor_goal_ref == _goal(3, RESPONSE)
    assert focus.evidence_refs == ("order-response-obligation-3",)
    # ... while the non-focal projection follows each arrangement exactly.
    focal = {focus.primary_goal_ref, *focus.supporting_goal_refs}
    expected = tuple(
        record.goal_id for record in arranged
        if record.status == GoalStatus.ACTIVE and record.goal_id not in focal
    )
    assert focus.non_focal_active_goal_refs == expected
    rearranged = GoalSnapshot.model_validate({
        **snapshot.model_dump(mode="python"),
        "records": [record.model_dump(mode="python") for record in arranged],
        "active_goal_refs": tuple(record.goal_id for record in arranged if record.status == GoalStatus.ACTIVE),
        "focus_decision": focus.model_dump(mode="python"),
    })
    assert rearranged.focus_decision.non_focal_active_goal_refs == _record_order_non_focal(rearranged)


# ---------------------------------------------------------------------------
# Continuous multi-turn pipeline (live-shadow observe, no database)
# ---------------------------------------------------------------------------

# With these fixed identities, turn 3 of this conversation was rejected at
# ``pipeline.goal_update.focus_selection`` before the fix, and turn 4 then
# began a ``predecessor_gap`` segment.
ORDERING_NAMESPACE = uuid.UUID("00000000-0000-0000-0000-00000000c0de")
ORDERING_MESSAGES = (
    "How much protein is in two eggs?",
    "How many calories are in a banana?",
    "How much fat is in avocado?",
    "How many carbs are in rice?",
    "How much fiber is in oats?",
)


def _fixed(*parts: str) -> str:
    return str(uuid.uuid5(ORDERING_NAMESPACE, "|".join(("1", *parts))))


def test_continuous_pipeline_crosses_the_non_focal_ordering_boundary() -> None:
    user, conversation = _fixed("user"), _fixed("conv")
    contents: list[tuple[str, str]] = []
    message_ids: list[str] = []
    previous_record = previous_capture = None
    records = []
    for index, content in enumerate(ORDERING_MESSAGES):
        if index:
            contents.append(("assistant", f"reply {index}"))
            message_ids.append(_fixed("a", str(index)))
        contents.append(("user", content))
        message_ids.append(_fixed("m", str(index)))
        capture = make_capture(
            user_ref=user, conversation_ref=conversation, turn_id=_fixed("t", str(index)),
            contents=tuple(contents), message_ids=tuple(message_ids),
            previous_user_message_id=previous_capture.focus_message_id if previous_capture else None,
            chat_job_ref=_fixed("j", str(index)), captured_at="2026-09-26T00:00:00+00:00",
        )
        predecessor = _as_predecessor(previous_record, previous_capture) if previous_record else None
        record = _observe(capture, predecessor)

        assert record.status == TerminalStatus.COMPLETED, record.failure
        trace = record.pipeline_trace
        goals = trace.goal_snapshot
        active = tuple(item.goal_id for item in goals.records if item.status == GoalStatus.ACTIVE)
        assert goals.active_goal_refs == active
        assert goals.focal_goal_refs == (
            goals.focus_decision.primary_goal_ref, *goals.focus_decision.supporting_goal_refs,
        )
        assert goals.focus_decision.non_focal_active_goal_refs == tuple(
            ref for ref in active if ref not in goals.focal_goal_refs
        )
        assert trace.action_selection.plan.moves
        if previous_record is None:
            assert record.segment_reset_reason == "initial_conversation"
            assert goals.revision == 1 and goals.supersedes_goal_snapshot_id is None
        else:
            prior = previous_record.pipeline_trace
            assert record.segment_reset_reason is None
            assert record.predecessor_trace_id == previous_record.terminal_id
            assert record.segment_id == previous_record.segment_id
            assert record.compiled_request.previous_goal_snapshot == prior.goal_snapshot
            assert record.compiled_request.previous_state == prior.cognitive_turn.dynamic_state_update.snapshot
            assert goals.supersedes_goal_snapshot_id == prior.goal_snapshot.goal_snapshot_id
            assert goals.revision == prior.goal_snapshot.revision + 1
            state = trace.cognitive_turn.dynamic_state_update.snapshot
            assert state.revision == prior.cognitive_turn.dynamic_state_update.snapshot.revision + 1
        records.append(record)
        previous_record, previous_capture = record, capture

    # SH-EVAL-001: the chain no longer accumulates earlier turns' obligations.
    # Each turn ends with exactly its own response obligation active, and every
    # earlier one expired (never satisfied) through the trusted rollover.
    for index, record in enumerate(records):
        goals = record.pipeline_trace.goal_snapshot
        turn_scoped_active = [
            item for item in goals.records
            if item.scope == "turn" and item.status in {GoalStatus.ACTIVE, GoalStatus.SUSPENDED}
        ]
        assert [item.scope_ref for item in turn_scoped_active if item.definition_ref.definition_key == RESPONSE] == [
            record.turn_id
        ]
        assert all(item.scope_ref == record.turn_id for item in turn_scoped_active)
        earlier = [
            item for item in goals.records
            if item.definition_ref.definition_key == RESPONSE and item.scope_ref != record.turn_id
        ]
        assert len(earlier) == index
        assert all(item.status == GoalStatus.EXPIRED for item in earlier)
        rollover = record.compiled_request.goal_turn_rollover
        if index == 0:
            assert rollover is None
        else:
            assert rollover.closed_turn_ref == records[index - 1].turn_id
            assert rollover.closing_turn_ref == record.turn_id
        # SH-EVAL-002: focus evidence is this turn's obligation, not an older one.
        focus = goals.focus_decision
        floor = next(item for item in goals.records if item.goal_id == focus.coordination_floor_goal_ref)
        assert floor.scope_ref == record.turn_id and floor.status == GoalStatus.ACTIVE
        assert focus.evidence_refs == floor.source_refs
