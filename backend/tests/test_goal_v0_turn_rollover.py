"""Goal V0 turn rollover (SH-EVAL-001) and exact focus provenance (SH-EVAL-002).

A completed response episode must not leave its turn-scoped goals active in
the next one: the trusted rollover expires them (scope closure is never
success) before the new turn's goals form, while conversation- and
task-scoped goals persist.  Focus evidence must come from the exact focal or
coordination-floor record, never from a record that merely shares a
definition key.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.ai.Cognitive.system.v0.contracts.goals import (
    RESPONSE_OBLIGATION_DEFINITION_KEY,
    GoalScope,
    GoalSnapshot,
    GoalStatus,
    GoalTransitionKind,
    GoalTurnRollover,
    GoalUpdateTrigger,
    stale_turn_scoped_goals,
)
from app.ai.Cognitive.system.v0.goal_v0 import GoalEngine
from app.ai.Cognitive.system.v0.goal_v0.engine import _id
from app.ai.Cognitive.system.v0.goal_v0.fixture_adapter import build_request, load_design_fixture
from test_goal_design_cases import FIXTURE

BACKEND = Path(__file__).resolve().parents[1]
RESPONSE = RESPONSE_OBLIGATION_DEFINITION_KEY
ROLLOVER_RULE = "goal-lifecycle.expire-turn-scope-on-successor-turn.v1"
# Five consecutive response turns with different act mixes: an answerable
# request, a conversation-scoped acknowledgement, a quoted (rejected) request,
# social contact, and another request.  No outcome producer runs.
SEQUENCE = (
    "direct-information-request", "acknowledgement", "quoted-request", "social-contact",
    "direct-information-request",
)


def _salted(kind: str, parts: tuple[str, ...]) -> str:
    return f"{kind}-{hashlib.sha256(('salt|' + '|'.join(parts)).encode()).hexdigest()[:20]}"


def _descending(kind: str, parts: tuple[str, ...]) -> str:
    # Later turns sort before earlier ones, so id order opposes record order.
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]
    turn = next((int(part.rsplit("-", 1)[1]) for part in parts if part.startswith("rollover-turn-")), 0)
    return f"{kind}-{9 - turn}-{digest}"


IDENTITIES = {"default": lambda kind, parts: _id(kind, *parts), "salted": _salted, "descending": _descending}


def _case(case_id: str) -> dict:
    return next(case for case in load_design_fixture(FIXTURE)["cases"] if case["case_id"] == case_id)


def _turn_case(case_id: str, turn: int) -> dict:
    case = copy.deepcopy(_case(case_id))
    case["turn_context"].update(
        cognitive_turn_id=f"rollover-turn-{turn}",
        runtime_snapshot_ref=f"rollover-runtime-{turn}",
        response_obligation_ref=f"rollover-response-obligation-{turn}",
    )
    case["interpretation_input"]["interpretation_trace_ref"] = f"rollover-interpretation-{turn}"
    case["conversation"]["conversation_ref"] = "rollover-conversation"
    for frame in case["interpretation_input"].get("semantic_frames", ()):
        if "proposition_ref" in frame["arguments"] or case_id == "acknowledgement":
            frame["arguments"]["proposition_ref"] = f"rollover-proposition-{turn}"
    case["new_goal_evidence"] = []
    case.pop("update_steps", None)
    return case


def _rollover(previous: GoalSnapshot, case: dict, **overrides) -> GoalTurnRollover:
    turn = case["turn_context"]
    values = dict(
        rollover_id=f"rollover:{previous.cognitive_turn_id}:{turn['cognitive_turn_id']}",
        proof_kind="successor_response_turn", producer="test-trusted-runtime",
        conversation_ref=previous.conversation_ref, closed_turn_ref=previous.cognitive_turn_id,
        closed_goal_snapshot_ref=previous.goal_snapshot_id, closing_turn_ref=turn["cognitive_turn_id"],
        closing_response_obligation_ref=turn["response_obligation_ref"],
    )
    values.update(overrides)
    return GoalTurnRollover(**values)


def _response_turn(engine: GoalEngine, case_id: str, turn: int, previous: GoalSnapshot | None):
    case = _turn_case(case_id, turn)
    rollover = (
        _rollover(previous, case)
        if previous is not None and stale_turn_scoped_goals(previous, case["turn_context"]["cognitive_turn_id"])
        else None
    )
    request = build_request(case, previous_snapshot=previous, turn_rollover=rollover)
    return request, engine.update(request)


def _run_sequence(identity: str = "default"):
    engine = GoalEngine(identity_factory=IDENTITIES[identity])
    previous, results = None, []
    for turn, case_id in enumerate(SEQUENCE, start=1):
        request, result = _response_turn(engine, case_id, turn, previous)
        results.append((request, result))
        previous = result.snapshot
    return engine, results


def _obligations(snapshot: GoalSnapshot):
    return [item for item in snapshot.records if item.definition_ref.definition_key == RESPONSE]


# ---------------------------------------------------------------------------
# SH-EVAL-001: turn rollover
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("identity", sorted(IDENTITIES))
def test_consecutive_response_turns_keep_only_the_current_obligation_active(identity: str) -> None:
    _, results = _run_sequence(identity)
    previous_records: tuple = ()
    for index, (request, result) in enumerate(results):
        snapshot = result.snapshot
        turn = request.cognitive_turn_id
        obligations = _obligations(snapshot)
        assert [item.scope_ref for item in obligations if item.status == GoalStatus.ACTIVE] == [turn]
        earlier = [item for item in obligations if item.scope_ref != turn]
        assert len(earlier) == index
        # Scope closure is not success: every earlier obligation expired.
        assert all(item.status == GoalStatus.EXPIRED for item in earlier)
        assert not any(t.transition_kind == GoalTransitionKind.SATISFY for t in snapshot.transitions)
        # No non-terminal goal of another turn survives into this episode.
        assert not stale_turn_scoped_goals(snapshot, turn)
        # The rollover expired exactly the previous turn's non-terminal turn goals,
        # citing the trusted proof, before the new turn's goals were proposed.
        expired_now = [t for t in result.transitions if t.transition_kind == GoalTransitionKind.EXPIRE]
        if index == 0:
            assert request.turn_rollover is None and expired_now == []
        else:
            prior = results[index - 1][1].snapshot
            closable = {item.goal_id for item in stale_turn_scoped_goals(prior, turn)}
            assert {t.goal_id for t in expired_now} == closable and closable
            assert all(t.rule_id == ROLLOVER_RULE for t in expired_now)
            assert all(t.evidence_refs == (request.turn_rollover.rollover_id,) for t in expired_now)
            assert all(t.from_status in {GoalStatus.ACTIVE, GoalStatus.SUSPENDED} for t in expired_now)
            first_propose = next(
                i for i, t in enumerate(result.transitions) if t.transition_kind == GoalTransitionKind.PROPOSE
            )
            assert all(result.transitions.index(t) < first_propose for t in expired_now)
        # Historical records (rejected and expired included) keep canonical order.
        ids = tuple(item.goal_id for item in snapshot.records)
        assert ids[: len(previous_records)] == tuple(item.goal_id for item in previous_records)
        previous_records = snapshot.records
    final = results[-1][1].snapshot
    assert any(item.status == GoalStatus.REJECTED for item in final.records)
    assert sum(item.status == GoalStatus.EXPIRED for item in final.records) >= len(SEQUENCE) - 1


def test_conversation_scoped_goals_survive_rollover() -> None:
    _, results = _run_sequence()
    acknowledgement = next(
        item for item in results[1][1].snapshot.records
        if item.definition_ref.definition_key == "interaction.acknowledge_relevant_information"
    )
    assert acknowledgement.scope == GoalScope.CONVERSATION
    for _, result in results[1:]:
        record = next(item for item in result.snapshot.records if item.goal_id == acknowledgement.goal_id)
        assert record.status == GoalStatus.ACTIVE
        assert acknowledgement.goal_id in result.snapshot.active_goal_refs


def test_evidence_satisfied_prior_goal_keeps_its_terminal_state() -> None:
    engine = GoalEngine()
    _, first = _response_turn(engine, "direct-information-request", 1, None)
    obligation = next(item for item in _obligations(first.snapshot) if item.status == GoalStatus.ACTIVE)
    case = _turn_case("direct-information-request", 2)
    case["new_goal_evidence"] = [{
        "evidence_id": "turn-1-response-delivered", "evidence_kind": "realization_validation",
        "source_type": "realization-validator", "source_ref": "turn-1-delivery",
        "episode_or_turn_ref": "rollover-turn-1", "target_goal_ref": obligation.goal_id,
        "target_requirement_ref": obligation.requirements[0].requirement_id,
        "provenance": {"producer": "realization-validator", "review_state": "pending"},
        "finality": "final", "outcome_id": "turn-1-delivery-outcome",
    }]
    request = build_request(case, previous_snapshot=first.snapshot, turn_rollover=_rollover(first.snapshot, case))
    second = engine.update(request)
    by_goal = {t.goal_id: t for t in second.transitions}
    assert by_goal[obligation.goal_id].transition_kind == GoalTransitionKind.SATISFY
    record = next(item for item in second.snapshot.records if item.goal_id == obligation.goal_id)
    assert record.status == GoalStatus.SATISFIED
    # The unsatisfied request goal of turn 1 still expires.
    answer = next(
        item for item in second.snapshot.records
        if item.scope_ref == "rollover-turn-1" and item.definition_ref.definition_key != RESPONSE
    )
    assert answer.status == GoalStatus.EXPIRED


def test_missing_or_mismatched_rollover_fails_closed() -> None:
    engine = GoalEngine()
    _, first = _response_turn(engine, "direct-information-request", 1, None)
    case = _turn_case("direct-information-request", 2)
    with pytest.raises(ValidationError, match="stale turn-scoped goals require a trusted turn rollover"):
        build_request(case, previous_snapshot=first.snapshot)
    for field, value in (
        ("closed_goal_snapshot_ref", "goal-snapshot-forged"),
        ("closed_turn_ref", "rollover-turn-0"),
        ("closing_response_obligation_ref", "rollover-response-obligation-forged"),
        ("conversation_ref", "another-conversation"),
    ):
        with pytest.raises(ValidationError, match="does not match this update's lineage"):
            build_request(case, previous_snapshot=first.snapshot, turn_rollover=_rollover(first.snapshot, case, **{field: value}))
    with pytest.raises(ValidationError, match="must close a different turn"):
        _rollover(first.snapshot, case, closed_turn_ref="rollover-turn-2")
    runtime_step = {"trigger": GoalUpdateTrigger.TRUSTED_RUNTIME_CHANGE.value}
    with pytest.raises(ValidationError, match="belongs only to a response turn"):
        build_request(case, step=runtime_step, previous_snapshot=first.snapshot,
                      turn_rollover=_rollover(first.snapshot, case))


def test_same_turn_updates_never_expire_their_own_goals() -> None:
    # Goals owned by the current turn are not stale: no rollover is required
    # and nothing expires when a later update of the same turn arrives.
    engine = GoalEngine()
    _, first = _response_turn(engine, "direct-information-request", 1, None)
    case = _turn_case("direct-information-request", 1)
    same_turn = build_request(case, step={"trigger": "trusted_runtime_change"}, previous_snapshot=first.snapshot)
    assert same_turn.turn_rollover is None
    result = engine.update(same_turn)
    assert not any(t.transition_kind == GoalTransitionKind.EXPIRE for t in result.transitions)
    assert result.snapshot.active_goal_refs == first.snapshot.active_goal_refs


_PROBE = (
    "import sys, json, hashlib; sys.path.insert(0, 'tests');"
    "from test_goal_v0_turn_rollover import _run_sequence;"
    "_, results = _run_sequence();"
    "payload = [r.snapshot.model_dump(mode='json') for _, r in results];"
    "print(hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest())"
)


def test_rollover_is_deterministic_across_replays_and_hash_seeds() -> None:
    first = [r.snapshot.model_dump(mode="json") for _, r in _run_sequence()[1]]
    second = [r.snapshot.model_dump(mode="json") for _, r in _run_sequence()[1]]
    assert first == second
    in_process = hashlib.sha256(json.dumps(first, sort_keys=True).encode()).hexdigest()
    outputs = {
        subprocess.run([sys.executable, "-c", _PROBE], cwd=BACKEND, capture_output=True, text=True, check=True,
                       env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(BACKEND)}).stdout.split()[-1]
        for seed in ("0", "1", "4242")
    }
    assert outputs == {in_process}


# ---------------------------------------------------------------------------
# SH-EVAL-002: focus provenance
# ---------------------------------------------------------------------------

def _arrangements(records: tuple) -> dict[str, list]:
    records = list(records)
    obligations = [item for item in records if item.definition_ref.definition_key == RESPONSE]
    others = [item for item in records if item not in obligations]
    return {
        "canonical": records,
        "reversed": records[::-1],
        "interleaved": records[1::2] + records[0::2],
        "historical_obligations_first": obligations + others,
        "historical_obligations_last": others + obligations[::-1],
    }


@pytest.mark.parametrize("identity", sorted(IDENTITIES))
def test_focus_evidence_is_owned_by_the_current_obligation_in_every_record_order(identity: str) -> None:
    engine, results = _run_sequence(identity)
    request, result = results[-1]
    snapshot = result.snapshot
    current = next(
        item for item in _obligations(snapshot)
        if item.status == GoalStatus.ACTIVE and item.scope_ref == request.cognitive_turn_id
    )
    historical = [item for item in _obligations(snapshot) if item.goal_id != current.goal_id]
    assert len(historical) == len(SEQUENCE) - 1
    assert snapshot.focus_decision.coordination_floor_goal_ref == current.goal_id
    assert snapshot.focus_decision.evidence_refs == current.source_refs == ("rollover-response-obligation-5",)
    for name, arranged in _arrangements(snapshot.records).items():
        focus = engine._focus(request, arranged, snapshot.conflicts, snapshot.focus_decision.route)
        assert focus.coordination_floor_goal_ref == current.goal_id, name
        assert focus.evidence_refs == current.source_refs, name
        assert focus.primary_goal_ref == snapshot.focus_decision.primary_goal_ref, name
        assert not set(focus.evidence_refs) & {ref for item in historical for ref in item.source_refs}, name


def test_without_a_floor_evidence_comes_from_the_exact_focal_records() -> None:
    engine = GoalEngine()
    request, first = _response_turn(engine, "social-contact", 1, None)
    obligation = next(item for item in _obligations(first.snapshot) if item.status == GoalStatus.ACTIVE)
    case = _turn_case("social-contact", 1)
    step = {"trigger": "realization_or_outcome", "new_goal_evidence": [{
        "evidence_id": "social-delivered", "evidence_kind": "realization_validation",
        "source_type": "realization-validator", "source_ref": "social-delivery",
        "episode_or_turn_ref": "rollover-turn-1", "target_goal_ref": obligation.goal_id,
        "target_requirement_ref": obligation.requirements[0].requirement_id,
        "provenance": {"producer": "realization-validator", "review_state": "pending"},
        "finality": "final", "outcome_id": "social-delivery-outcome",
    }]}
    second = engine.update(build_request(case, step=step, previous_snapshot=first.snapshot))
    focus = second.snapshot.focus_decision
    assert focus.coordination_floor_goal_ref is None
    primary = next(item for item in second.snapshot.records if item.goal_id == focus.primary_goal_ref)
    assert primary.definition_ref.definition_key != RESPONSE
    assert focus.evidence_refs == primary.source_refs
    # The satisfied obligation shares the definition but is not cited.
    assert not set(focus.evidence_refs) & set(obligation.source_refs)


def _tampered(snapshot: GoalSnapshot, **focus_updates) -> dict:
    payload = snapshot.model_dump(mode="python")
    payload["focus_decision"] = {**payload["focus_decision"], **focus_updates}
    return payload


def test_snapshot_rejects_focus_evidence_from_a_historical_goal() -> None:
    _, results = _run_sequence()
    snapshot = results[-1][1].snapshot
    historical = next(
        item for item in _obligations(snapshot) if item.status == GoalStatus.EXPIRED
    )
    with pytest.raises(ValidationError, match="focal or coordination-floor goal"):
        GoalSnapshot.model_validate(_tampered(snapshot, evidence_refs=historical.source_refs))
    with pytest.raises(ValidationError, match="this turn's active response obligation"):
        GoalSnapshot.model_validate(_tampered(
            snapshot, coordination_floor_goal_ref=historical.goal_id, evidence_refs=historical.source_refs,
        ))
    with pytest.raises(ValidationError, match="unknown goal"):
        GoalSnapshot.model_validate(_tampered(snapshot, coordination_floor_goal_ref="goal-unknown"))


def test_legacy_snapshots_remain_loadable_but_cannot_declare_a_floor() -> None:
    _, results = _run_sequence()
    snapshot = results[-1][1].snapshot
    historical = next(item for item in _obligations(snapshot) if item.status == GoalStatus.EXPIRED)
    legacy_versions = {**snapshot.versions.model_dump(mode="python"), "goal_contract_schema": "goal-contract-v0.1-draft"}
    legacy = _tampered(snapshot, evidence_refs=historical.source_refs, coordination_floor_goal_ref=None)
    legacy["versions"] = legacy_versions
    # A historical v0.1 snapshot with the definition-only lookup still loads for audit ...
    loaded = GoalSnapshot.model_validate(legacy)
    assert "coordination_floor_goal_ref" not in loaded.focus_decision.model_dump(mode="json")
    # ... but it cannot claim a field its contract never had.
    legacy["focus_decision"]["coordination_floor_goal_ref"] = snapshot.focus_decision.coordination_floor_goal_ref
    with pytest.raises(ValidationError, match="legacy goal snapshot"):
        GoalSnapshot.model_validate(legacy)
