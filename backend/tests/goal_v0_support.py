"""Synthetic Goal V0 turns built through the reviewed fixture adapter.

Frames, hypotheses, and scenarios are written the way the frame-dominance
Interpretation path emits them (each act and speaker goal cites its own
frames), then validated by the canonical ``InterpretationEnvelope`` exactly as
fixture cases are.  Nothing here is fixture wording or an expected output.
"""

from __future__ import annotations

from typing import Any

from app.ai.Cognitive.system.v0.contracts.goals import (
    GoalRecord,
    GoalSnapshot,
    GoalStatus,
    GoalTurnRollover,
    GoalUpdateTrigger,
    stale_turn_scoped_goals,
)
from app.ai.Cognitive.system.v0.contracts.interpretation import HYPOTHESIS_DIMENSION_BY_KEY
from app.ai.Cognitive.system.v0.goal_v0 import GoalEngine
from app.ai.Cognitive.system.v0.goal_v0.fixture_adapter import build_request
from app.ai.Cognitive.system.v0.goal_v0.models import GoalUpdateRequest

EVIDENCE = "text-evidence"


def frame(frame_id: str, predicate: str, *, polarity: str = "positive", unresolved: tuple[str, ...] = (), **arguments: Any) -> dict:
    arguments = dict(arguments)
    for name in unresolved:
        arguments[name] = None
    return {
        "frame_id": frame_id, "predicate": predicate, "arguments": arguments, "polarity": polarity,
        "evidence_refs": [EVIDENCE], "unresolved_arguments": list(unresolved),
        "grounding_rule_ids": ["semantic-grounding.model.v1"],
    }


def hyp(hypothesis_id: str, key: str, support: str = "strong", *, frames: tuple[str, ...] = (),
        disposition: str = "leading", contradictions: tuple[str, ...] = ()) -> dict:
    return {
        "hypothesis_id": hypothesis_id, "hypothesis_key": f"interpretation.{key}",
        "dimension": HYPOTHESIS_DIMENSION_BY_KEY[f"interpretation.{key}"].value,
        "support": support, "disposition": disposition, "semantic_frame_refs": list(frames),
        "supporting_evidence_refs": [EVIDENCE] if support != "unsupported" else [],
        "contradiction_refs": list(contradictions),
    }


def scenario(scenario_id: str, hypotheses: list[dict], frames: list[dict], *, disposition: str = "leading",
             unknowns: tuple[str, ...] = (), contradictions: tuple[str, ...] = ()) -> dict:
    refs: dict[str, list[str]] = {}
    for item in hypotheses:
        refs.setdefault(item["dimension"], []).append(item["hypothesis_id"])
    return {
        "scenario_id": scenario_id, "support": "strong", "disposition": disposition,
        "semantic_frame_refs": [item["frame_id"] for item in frames], "hypothesis_refs": refs,
        "supporting_evidence_refs": [EVIDENCE], "contradiction_refs": list(contradictions),
        "unknown_refs": list(unknowns), "compatibility_rule_ids": ["scenario.compatible-literal.v1"],
    }


def unknown(unknown_id: str, key: str) -> dict:
    return {"unknown_id": unknown_id, "key": key, "owner": "user", "status": "unresolved", "evidence_refs": [EVIDENCE]}


def contradiction(contradiction_id: str, status: str = "active") -> dict:
    item = {"contradiction_id": contradiction_id, "status": status, "description": "typed test contradiction",
            "evidence_refs": [EVIDENCE]}
    if status == "resolved":
        item["resolution_ref"] = f"{contradiction_id}-resolution"
    return item


def make_case(turn: str, *, frames: list[dict] = (), hypotheses: list[dict] = (), scenarios: list[dict] | None = None,
              unknowns: list[dict] = (), contradictions: list[dict] = (), conversation: str = "goal-test-conversation",
              text: str = "A synthetic user message.", task_ref: str | None = None,
              authorization: str | None = "not_required", safety_decision: str | None = None,
              last_tool_event: dict | None = None) -> dict:
    frames, hypotheses = list(frames), list(hypotheses)
    if scenarios is None:
        scenarios = [scenario(f"{turn}-scenario", hypotheses, frames,
                              unknowns=tuple(item["unknown_id"] for item in unknowns),
                              contradictions=tuple(item["contradiction_id"] for item in contradictions))]
    message = f"{turn}-message"
    return {
        "case_id": turn,
        "conversation": {"conversation_ref": conversation, "focus_message_id": message,
                         "messages": [{"message_id": message, "role": "user", "content": text}]},
        "turn_context": {
            "cognitive_turn_id": turn, "runtime_snapshot_ref": f"{turn}-runtime",
            "response_obligation": "required", "response_obligation_ref": f"{turn}-response-obligation",
            "conversation_status": "open", "task_ref": task_ref, "authorization": authorization,
            "safety_decision": safety_decision, "last_tool_event": last_tool_event,
        },
        "interpretation_input": {
            "interpretation_trace_ref": f"{turn}-interpretation",
            "evidence": [{
                "evidence_id": EVIDENCE, "source_kind": "message_span", "source_ref": message, "message_id": message,
                "span": {"message_id": message, "start": 0, "end": len(text), "quoted_text": text},
            }],
            "semantic_frames": frames, "hypotheses": hypotheses, "scenarios": scenarios,
            "unknowns": list(unknowns), "contradictions": list(contradictions),
        },
    }


def rollover_for(previous: GoalSnapshot | None, case: dict) -> GoalTurnRollover | None:
    turn = case["turn_context"]
    if previous is None or not stale_turn_scoped_goals(previous, turn["cognitive_turn_id"]):
        return None
    return GoalTurnRollover(
        rollover_id=f"rollover:{previous.cognitive_turn_id}->{turn['cognitive_turn_id']}",
        proof_kind="successor_response_turn", producer="test-trusted-runtime",
        conversation_ref=previous.conversation_ref, closed_turn_ref=previous.cognitive_turn_id,
        closed_goal_snapshot_ref=previous.goal_snapshot_id, closing_turn_ref=turn["cognitive_turn_id"],
        closing_response_obligation_ref=turn["response_obligation_ref"],
    )


def respond(case: dict, previous: GoalSnapshot | None = None, *, engine: GoalEngine | None = None, evidence=(),
            state=None):
    """A response turn.  ``state`` optionally rewrites the previous Dynamic State."""

    request = build_request(case, previous_snapshot=previous, turn_rollover=rollover_for(previous, case))
    updates = {}
    if evidence:
        updates["new_goal_evidence"] = tuple(evidence)
    if state is not None:
        updates["previous_dynamic_state"] = state(request.previous_dynamic_state)
    if updates:
        request = GoalUpdateRequest(**{**dict(request), **updates})
    return (engine or GoalEngine()).update(request)


def revise(case: dict, previous: GoalSnapshot, trigger: GoalUpdateTrigger, evidence, *, engine: GoalEngine | None = None,
           state=None):
    """A lifecycle-only revision of ``previous`` within its own turn."""

    request = build_request(case, previous_snapshot=previous)
    updates = {"trigger": trigger, "new_goal_evidence": tuple(evidence)}
    if state is not None:
        updates["previous_dynamic_state"] = state(request.previous_dynamic_state)
    request = GoalUpdateRequest(**{**dict(request), **updates})
    return (engine or GoalEngine()).update(request)


def records(snapshot: GoalSnapshot, key: str) -> list[GoalRecord]:
    return [item for item in snapshot.records if item.definition_ref.definition_key == key]


def only(snapshot: GoalSnapshot, key: str, status: GoalStatus | None = None) -> GoalRecord:
    [item] = [item for item in records(snapshot, key) if status is None or item.status == status]
    return item


def rejection(result, goal_id: str):
    [transition] = [item for item in result.transitions if item.goal_id == goal_id and item.transition_kind.value == "reject"]
    return transition


def requirement(record: GoalRecord):
    [item] = record.requirements
    return item


def target(record: GoalRecord, role: str) -> str:
    return next(item.ref for item in requirement(record).target_refs if item.role == role)
