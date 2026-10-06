"""Read-only, content-minimized summaries of durable cognitive shadow traces.

The raw terminal row is intentionally exhaustive and can contain the captured
conversation.  This module extracts review signals without emitting messages,
quoted spans, evidence text, request payloads, or raw model prompts.

Summaries branch on the terminal status first (SH-EVAL-009): a failed trace
shows its content-free failure envelope and which stages did not run; a
skipped trace shows its skip reason; only a completed trace summarizes stage
output.  Every stage section carries an explicit ``status`` so "did not run"
is never confused with "ran and produced nothing".  Decision structure keeps
non-content identities (goal ids, scope ownership, short candidate and target
keys) so multiplicity can be told apart from duplication (SH-EVAL-010), and
review axes keep fidelity validation, behavioral review, and live authority
separate (SH-EVAL-011).  Summary v0.3 adds the ``requirements`` stage: per
current request goal, its readiness, blocker kinds, and typed resolution
statuses; for the primary goal, whether an answer, clarification, decline,
or other move was constructible, and why acknowledgement did not address the
blocker.  Producer values, anchors, and query arguments are never emitted.

Run locally with, for example::

    uv run python -m app.ai.Cognitive.system.v0.trace_summary --latest
    uv run python -m app.ai.Cognitive.system.v0.trace_summary --trace-id <uuid>

``--latest`` additionally prints the selected trace's focus user message and
final accepted realization response.  Those two fields are local-sensitive
conversation content; the command warns on stderr and they remain outside the
closed, content-minimized summary schema.

``--reveal-final-candidate`` additionally prints the final accepted candidate
wording of the selected trace for local human review only.  It never prints
captured messages, prompts, rejected candidates, or evidence text, and must
never be exposed over HTTP or written to ordinary logs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

SUMMARY_SCHEMA_VERSION = "cognitive-shadow-trace-summary-v0.6"
# Per-stage run state.  ``ran``: output is summarized below.  ``failed``: this
# stage raised the terminal failure.  ``not_persisted``: the stage ran but a
# later failure discarded its output.  ``not_run``: the stage never executed.
# ``unknown``: the failure envelope cannot establish whether it ran.
STAGE_STATUSES = ("ran", "failed", "not_persisted", "not_run", "unknown")
# v0.5: ``workspace`` and ``collaboration`` (Collaborative Content V0), in pipeline order.
STAGES = ("perception", "interpretation", "goals", "workspace", "requirements", "collaboration", "appraisal", "state",
          "action")
# Stages a historical runner did not have (``not_run`` in a completed trace).
OPTIONAL_STAGES = frozenset({"workspace", "requirements", "collaboration"})
LIVE_AUTHORITY = "never_granted_by_shadow"
FIDELITY_VALIDATION_VALUES = ("passed", "failed", "not_run")
BEHAVIORAL_REVIEW_VALUES = ("passed", "failed", "not_reviewed")

TOP_LEVEL_KEYS = frozenset({
    "summary_schema", "trace", "flags", "versions", "failure", "skip", "review",
    *STAGES, "realization", "timings_ms", "latency",
})
COMPLETED_SECTION_KEYS = {
    "perception": frozenset({
        "status", "important_cues", "overall_uncertainty", "input_message_count",
        "context_truncated", "missing_information",
    }),
    "interpretation": frozenset({
        "status", "semantic_predicates", "hypotheses", "unknown_keys", "contradiction_count",
    }),
    "goals": frozenset({"status", "snapshot_turn", "revision", "records", "focus"}),
    "workspace": frozenset({"status", "authority_mode", "artifact_count", "revision_count", "open_inquiry_count",
                            "targets", "reconciliation_states", "answer_bindings"}),
    "requirements": frozenset({"status", "versions", "goals", "primary_goal"}),
    "collaboration": frozenset({"status", "versions", "goals"}),
    "appraisal": frozenset({"status", "frame_completeness", "known_dimensions", "missing_inputs", "implementation", "replay"}),
    "state": frozenset({"status", "values", "controls"}),
    "action": frozenset({
        "status", "selected_moves", "candidates", "termination", "shadow_only",
        "safety_disposition", "safety_restriction",
    }),
}
FAILURE_KEYS = frozenset({
    "stage", "nested_stage", "nested_phase", "code", "retryable", "attempt_count", "safe_message",
})
SKIP_KEYS = frozenset({"reason"})
REVIEW_KEYS = frozenset({"fidelity_validation", "behavioral_review", "live_authority"})


class SummaryError(ValueError):
    """The stored terminal row is internally inconsistent; no summary is invented."""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _value(record: Any, key: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(key, default)
    return getattr(record, key, default)


def _timestamp(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value is not None else None


def _unique_strings(values: list[Any]) -> list[str]:
    return sorted({value for value in values if isinstance(value, str)})


def _key(value: Any) -> str | None:
    """A stable short key for an identifier that may embed source identities."""

    if not isinstance(value, str) or not value:
        return None
    return "k" + hashlib.sha256(value.encode()).hexdigest()[:12]


def _normalized_missing_input(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if value.startswith("interpretation-unknown"):
        return "interpretation_unknown"
    return value


# ---------------------------------------------------------------------------
# Terminal-status projections
# ---------------------------------------------------------------------------

def _stage_statuses_for_failure(failure: Mapping[str, Any]) -> dict[str, str]:
    """Which stages ran, failed, or never ran, from the envelope alone."""

    def upto(failed_index: int | None, *, failed_status: str = "failed") -> dict[str, str]:
        statuses = {}
        for index, stage in enumerate(STAGES):
            if failed_index is None:
                statuses[stage] = "not_run"
            elif index < failed_index:
                statuses[stage] = "not_persisted"
            elif index == failed_index:
                statuses[stage] = failed_status
            else:
                statuses[stage] = "not_run"
        return statuses

    stage = failure.get("stage")
    nested = failure.get("nested_stage")
    if stage in {"capture", "compile", "pipeline.validation"}:
        return upto(None)
    if stage in {"persistence", "pipeline.trace_validation"}:
        return {name: "not_persisted" for name in STAGES}
    if stage == "worker":
        return {name: "unknown" for name in STAGES}
    position = {name: index for index, name in enumerate(STAGES)}
    simple = {
        "pipeline.perception": (position["perception"], "failed"),
        "pipeline.handoff_validation": (position["interpretation"], "not_run"),
        "pipeline.interpretation": (position["interpretation"], "failed"),
        "pipeline.goal_update": (position["goals"], "failed"),
        "pipeline.workspace_resolution": (position["workspace"], "failed"),
        "pipeline.requirement_resolution": (position["requirements"], "failed"),
        "pipeline.collaborative_content": (position["collaboration"], "failed"),
        "pipeline.goal_handoff_validation": (position["appraisal"], "not_run"),
        "pipeline.turn_request": (position["appraisal"], "not_run"),
    }
    if stage in simple:
        index, status = simple[stage]
        return upto(index, failed_status=status)
    if stage == "pipeline.cognitive_turn":
        turn = {
            "validation": (position["appraisal"], "not_run"),
            "appraisal": (position["appraisal"], "failed"),
            "dynamic_state": (position["state"], "failed"),
            "action_handoff_validation": (position["action"], "not_run"),
            "action_selection": (position["action"], "failed"),
        }
        if nested in turn:
            index, status = turn[nested]
            return upto(index, failed_status=status)
        if nested == "trace_validation":
            return {name: "not_persisted" for name in STAGES}
        statuses = upto(position["appraisal"], failed_status="unknown")
        statuses.update(state="unknown", action="unknown")
        return statuses
    raise SummaryError("failure envelope names an unregistered stage")


def _failure_projection(record: Any) -> dict[str, Any]:
    failure = _value(record, "failure_payload")
    if not isinstance(failure, Mapping):
        raise SummaryError("a failed trace has no failure envelope")
    required = ("stage", "code", "retryable", "attempt_count")
    if any(failure.get(name) is None for name in required):
        raise SummaryError("the failure envelope is incomplete")
    if not isinstance(failure.get("retryable"), bool) or not isinstance(failure.get("attempt_count"), int):
        raise SummaryError("the failure envelope has malformed fields")
    return {
        "stage": failure["stage"],
        "nested_stage": failure.get("nested_stage"),
        "nested_phase": failure.get("nested_phase"),
        "code": failure["code"],
        "retryable": failure["retryable"],
        "attempt_count": failure["attempt_count"],
        # Part of the approved content-free envelope (``ShadowFailure``).
        "safe_message": failure.get("safe_message"),
    }


def _versions_projection(record: Any) -> dict[str, Any]:
    versions = _mapping(_value(record, "versions"))
    pipeline = _mapping(versions.get("pipeline"))
    goal = _mapping(pipeline.get("goal"))
    action = _mapping(pipeline.get("action"))
    return {
        "capture_schema": versions.get("capture_schema"),
        "compiler": versions.get("compiler"),
        "capability_map": versions.get("capability_map"),
        "memory_retrieval_policy": versions.get("memory_retrieval_policy"),
        "personality_projection": versions.get("personality_projection"),
        "pipeline_schema": pipeline.get("pipeline_schema"),
        "pipeline_runner": pipeline.get("pipeline_runner"),
        "goal_lifecycle_policy": goal.get("goal_lifecycle_policy"),
        "goal_engine": goal.get("goal_engine"),
        "action_registry": action.get("registry"),
    }


def _review_axes(realization_status: Any, realization: Mapping[str, Any], behavioral_review: str | None) -> dict[str, str]:
    """Three separate axes; shadow acceptance is never a quality or live verdict."""

    attempts = [_mapping(item) for item in _list(realization.get("attempts"))]
    rejected = any(_mapping(item.get("validation")).get("status") == "rejected" for item in attempts)
    if realization_status == "accepted_shadow":
        fidelity = "passed"
    elif realization_status == "rejected" or rejected:
        fidelity = "failed"
    else:
        fidelity = "not_run"
    review = behavioral_review or "not_reviewed"
    if review not in BEHAVIORAL_REVIEW_VALUES:
        raise SummaryError("unregistered behavioral review value")
    return {"fidelity_validation": fidelity, "behavioral_review": review, "live_authority": LIVE_AUTHORITY}


# ---------------------------------------------------------------------------
# Completed-trace stage projections
# ---------------------------------------------------------------------------

def _perception(perception: Mapping[str, Any]) -> dict[str, Any]:
    cues = []
    for cue in _list(perception.get("cue_activations")):
        cue = _mapping(cue)
        state = cue.get("state")
        if state not in {"absent", "not_applicable", None}:
            cues.append({"key": cue.get("cue_key"), "state": state, "value": cue.get("value")})
    return {
        "status": "ran",
        "important_cues": cues,
        "overall_uncertainty": perception.get("overall_uncertainty"),
        "input_message_count": len(_list(perception.get("input_message_ids"))),
        "context_truncated": bool(perception.get("context_truncated")),
        "missing_information": _list(perception.get("missing_information")),
    }


def _interpretation(interpretation: Mapping[str, Any]) -> dict[str, Any]:
    hypotheses = []
    for hypothesis in _list(interpretation.get("hypotheses")):
        hypothesis = _mapping(hypothesis)
        hypotheses.append({
            "dimension": hypothesis.get("dimension"),
            "key": hypothesis.get("hypothesis_key"),
            "support": hypothesis.get("support"),
            "disposition": hypothesis.get("disposition"),
        })
    return {
        "status": "ran",
        "semantic_predicates": _unique_strings([
            _mapping(frame).get("predicate") for frame in _list(interpretation.get("semantic_frames"))
        ]),
        "hypotheses": hypotheses,
        "unknown_keys": _unique_strings([
            _mapping(unknown).get("key") for unknown in _list(interpretation.get("unknowns"))
        ]),
        "contradiction_count": len(_list(interpretation.get("contradictions"))),
    }


def _goals(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    turn = snapshot.get("cognitive_turn_id")
    focus = _mapping(snapshot.get("focus_decision"))
    primary = focus.get("primary_goal_ref")
    supporting = set(_list(focus.get("supporting_goal_refs")))
    non_focal = set(_list(focus.get("non_focal_active_goal_refs")))
    floor = focus.get("coordination_floor_goal_ref")
    transitions = {
        _mapping(item).get("transition_id"): _mapping(item) for item in _list(snapshot.get("transitions"))
    }
    records = []
    for goal in _list(snapshot.get("records")):
        goal = _mapping(goal)
        goal_id = goal.get("goal_id")
        scope, scope_ref = goal.get("scope"), goal.get("scope_ref")
        if scope == "turn":
            ownership = "current_turn" if scope_ref == turn else "other_turn"
        else:
            ownership = scope
        if goal_id == primary:
            role = "primary"
        elif goal_id in supporting:
            role = "supporting"
        elif goal_id in non_focal:
            role = "non_focal"
        else:
            role = "inactive"
        last = transitions.get(goal.get("last_transition_ref"), {})
        records.append({
            "goal_id": goal_id,
            "definition": _mapping(goal.get("definition_ref")).get("definition_key"),
            "status": goal.get("status"),
            "priority": goal.get("priority_band"),
            "owner": goal.get("owner"),
            "scope": scope,
            "scope_ownership": ownership,
            "created_turn": goal.get("created_turn_ref"),
            "lineage": "current" if goal.get("created_turn_ref") == turn else "historical",
            "focus_role": role,
            "coordination_floor": goal_id == floor,
            "last_transition": {"kind": last.get("transition_kind"), "rule_id": last.get("rule_id")},
        })
    return {
        "status": "ran",
        "snapshot_turn": turn,
        "revision": snapshot.get("revision"),
        "records": records,
        "focus": {
            "primary_goal_id": primary,
            "supporting_goal_ids": _list(focus.get("supporting_goal_refs")),
            "non_focal_goal_ids": _list(focus.get("non_focal_active_goal_refs")),
            "coordination_floor_goal_id": floor,
            "rule_ids": _list(focus.get("rule_ids")),
            "evidence_ref_count": len(_list(focus.get("evidence_refs"))),
            "route": focus.get("route"),
        },
    }


_ANSWER_ACTIONS = frozenset({"provide_information.answer"})
_CLARIFY_ACTIONS = frozenset({
    "seek_information.clarify", "seek_information.disambiguate", "seek_information.explore_goal",
    "seek_information.explore_preference", "seek_information.explore_experience", "seek_information.verify",
})
_DECLINE_ACTIONS = frozenset({"commit_or_coordinate.decline_request"})
_ACKNOWLEDGE = "give_feedback.acknowledge"


def _workspace(pipeline: Mapping[str, Any]) -> dict[str, Any]:
    """Workspace structure only: counts, operations, statuses, and states -- never artifact values."""

    workspace = _mapping(_mapping(pipeline.get("collaboration")).get("workspace"))
    snapshot = _mapping(workspace.get("snapshot"))
    return {
        "status": "ran",
        "authority_mode": snapshot.get("authority_mode"),
        "artifact_count": len(_list(snapshot.get("heads"))),
        "revision_count": len(_list(snapshot.get("revisions"))),
        "open_inquiry_count": sum(_mapping(item).get("status") == "open" for item in _list(snapshot.get("inquiries"))),
        "targets": [{
            "operation": _mapping(item).get("operation"), "artifact_type": _mapping(item).get("artifact_type"),
            "status": _mapping(item).get("status"), "rule_id": _mapping(item).get("rule_id"),
        } for item in _list(workspace.get("targets"))],
        "reconciliation_states": sorted(
            _mapping(item).get("state") for item in _list(workspace.get("reconciliations"))
            if isinstance(_mapping(item).get("state"), str)),
        "answer_bindings": [_mapping(item).get("answer_kind") for item in _list(workspace.get("answer_bindings"))],
    }


def _collaboration(pipeline: Mapping[str, Any]) -> dict[str, Any]:
    """Proposal structure only: operations, roles, classes, failure codes -- never statements or values."""

    collaboration = _mapping(pipeline.get("collaboration"))
    goals = []
    for outcome in (_mapping(item) for item in _list(collaboration.get("outcomes"))):
        proposal = _mapping(outcome.get("proposal"))
        failure = _mapping(outcome.get("failure"))
        inquiries = [_mapping(item) for item in _list(proposal.get("inquiry_opportunities"))]
        goals.append({
            "goal_key": _key(outcome.get("goal_ref")),
            "status": outcome.get("status"),
            "operation": proposal.get("operation"),
            "creates_revision": proposal.get("proposed_revision") is not None,
            "item_roles": sorted(_mapping(item).get("role") for item in _list(proposal.get("content_items"))),
            "content_classes": sorted(_unique_strings(_list(proposal.get("epistemic_classes")))),
            "inquiries": [{"disposition": item.get("disposition"), "timing": item.get("timing"),
                           "criticality": item.get("criticality"), "purpose": item.get("purpose"),
                           "suppression": item.get("suppression_reason")} for item in inquiries],
            "failure": {"stage": failure.get("stage"), "code": failure.get("code")} if failure else None,
            "skip_reason": outcome.get("skip_reason"),
            "provider_model": _mapping(outcome.get("provenance")).get("model_id"),
        })
    return {"status": "ran", "versions": dict(_mapping(collaboration.get("versions"))), "goals": goals}


def _requirements(pipeline: Mapping[str, Any]) -> dict[str, Any]:
    """Readiness structure only: kinds, statuses, reason codes, and action keys -- never content."""

    resolution = _mapping(pipeline.get("requirement_resolution"))
    handoff = _mapping(resolution.get("handoff"))
    snapshot = _mapping(handoff.get("snapshot"))
    versions = _mapping(resolution.get("versions"))
    goals_snapshot = _mapping(pipeline.get("goal_snapshot"))
    definitions = {
        _mapping(goal).get("goal_id"): _mapping(_mapping(goal).get("definition_ref")).get("definition_key")
        for goal in _list(goals_snapshot.get("records"))
    }
    primary = _mapping(goals_snapshot.get("focus_decision")).get("primary_goal_ref")
    action = _mapping(_mapping(pipeline.get("cognitive_turn")).get("action_selection"))
    conditions = [_mapping(item) for item in _list(action.get("planning_conditions"))]
    content = {
        _mapping(item).get("resolution_ref"): _mapping(item) for item in _list(snapshot.get("content_queries"))
    }
    readiness = {_mapping(item).get("goal_ref"): _mapping(item) for item in _list(snapshot.get("goal_readiness"))}
    goals = []
    for goal_ref in _list(snapshot.get("goal_refs")):
        own = [_mapping(item) for item in _list(snapshot.get("resolutions")) if _mapping(item).get("goal_ref") == goal_ref]
        state = readiness.get(goal_ref, {})
        goals.append({
            "goal_id": goal_ref,
            "definition": definitions.get(goal_ref),
            "readiness": state.get("readiness"),
            "blocking_tier": state.get("blocking_tier"),
            "blocker_kinds": sorted({item.get("kind") for item in conditions
                                     if item.get("blocked_goal_ref") == goal_ref and item.get("resolution_refs")}),
            "is_primary": goal_ref == primary,
            "resolutions": [{
                "kind": item.get("kind"), "key": item.get("key"), "status": item.get("status"),
                "reason": item.get("reason_code"), "blocking": item.get("blocking"), "tier": item.get("tier"),
            } for item in own],
            "content": [{
                "query_kind": content[item.get("resolution_id")].get("query_kind"),
                "producer_status": content[item.get("resolution_id")].get("producer_status"),
                "producer_reason": content[item.get("resolution_id")].get("producer_reason"),
                "authority_class": content[item.get("resolution_id")].get("authority_class"),
            } for item in own if item.get("resolution_id") in content],
        })
    primary_goal = None
    summary = next((item for item in goals if item["is_primary"]), None)
    if summary is not None:
        steps = [_mapping(item) for item in _list(action.get("steps"))]
        first = steps[0] if steps else {}
        constructed = {
            _mapping(item).get("candidate_id"): _mapping(_mapping(item).get("definition_ref")).get("action_key")
            for item in _list(first.get("constructed_candidates"))
        }
        eligible = sorted({constructed[_mapping(item).get("candidate_ref")] for item in _list(first.get("gate_results"))
                           if _mapping(item).get("status") in {"shadow_only", "passed"}})
        restricted = {constructed.get(ref) for ref in _list(first.get("condition_restricted_candidate_refs"))}
        plan = _mapping(action.get("plan"))
        primary_move = next((_mapping(item) for item in _list(plan.get("moves"))
                             if _mapping(item).get("move_id") == plan.get("primary_move_ref")), {})
        primary_action = _mapping(primary_move.get("definition_ref")).get("action_key")
        blocked = bool(summary["blocker_kinds"])
        if _ACKNOWLEDGE not in eligible:
            acknowledgement = "not_eligible"
        elif blocked:
            # A receipt is structurally incapable of discharging a blocker.
            acknowledgement = "receipt_never_addresses_a_blocker"
        elif primary_action == _ACKNOWLEDGE:
            acknowledgement = "selected_without_a_blocker"
        else:
            acknowledgement = "outscored_by_goal_directed_move"
        primary_goal = {
            "goal_id": summary["goal_id"],
            "definition": summary["definition"],
            "readiness": summary["readiness"],
            "blocker_kinds": summary["blocker_kinds"],
            "resolution_statuses": sorted({item["status"] for item in summary["resolutions"]}),
            "constructible": {
                "answer": bool(_ANSWER_ACTIONS & set(eligible)),
                "clarification": bool(_CLARIFY_ACTIONS & set(eligible)),
                "decline": bool(_DECLINE_ACTIONS & set(eligible)),
                "other": sorted(set(eligible) - _ANSWER_ACTIONS - _CLARIFY_ACTIONS - _DECLINE_ACTIONS - {_ACKNOWLEDGE}),
            },
            "selected_primary_action": primary_action,
            "acknowledgement": {
                "outcome": acknowledgement,
                "restricted_by_blocker": _ACKNOWLEDGE in restricted,
            },
        }
    return {
        "status": "ran",
        "versions": {
            "engine": versions.get("engine"), "manifest": versions.get("manifest"),
            "capability_map": versions.get("capability_map"), "producer_manifest": versions.get("producer_manifest"),
        },
        "goals": goals,
        "primary_goal": primary_goal,
    }


def _appraisal(appraisal: Mapping[str, Any]) -> dict[str, Any]:
    is_model = _mapping(appraisal.get("model_trace")).get("implementation") == "model"
    known_dimensions, missing, completeness = [], [], []
    for frame in _list(appraisal.get("frames")):
        frame = _mapping(frame)
        if frame.get("completeness") is not None:
            completeness.append(frame.get("completeness"))
        missing.extend(_list(frame.get("missing_inputs")))
        for dimension in _list(frame.get("dimensions")):
            dimension = _mapping(dimension)
            if dimension.get("status") == "known":
                known_dimensions.append({"key": dimension.get("dimension_key"), "value": dimension.get("value")})
            missing.extend(_list(dimension.get("missing_input_keys")))
    return {
        "status": "ran",
        "implementation": "model" if is_model else "deterministic",
        "replay": "frozen_output_only" if is_model else "deterministic",
        "frame_completeness": _unique_strings(completeness),
        "known_dimensions": known_dimensions,
        "missing_inputs": _unique_strings([_normalized_missing_input(value) for value in missing]),
    }


def _state(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    values = []
    for state in _list(snapshot.get("values")):
        state = _mapping(state)
        values.append({
            "key": state.get("state_key"), "band": state.get("band"),
            "score": state.get("score"), "status": state.get("status"),
        })
    return {"status": "ran", "values": values, "controls": dict(_mapping(snapshot.get("derived_controls")))}


def _targets(target_refs: Any) -> list[dict[str, Any]]:
    """Target structure only: role, type, and a short key (never the raw ref)."""

    return [
        {"role": _mapping(item).get("role"), "type": _mapping(item).get("target_type"),
         "key": _key(_mapping(item).get("ref"))}
        for item in _list(target_refs)
    ]


def _action(action: Mapping[str, Any]) -> dict[str, Any]:
    plan = _mapping(action.get("plan"))
    priorities = {
        _mapping(item).get("move_ref"): _mapping(item).get("priority") for item in _list(plan.get("move_priorities"))
    }
    selected_candidates = set()
    moves = []
    for move in _list(plan.get("moves")):
        move = _mapping(move)
        selected_candidates.add(move.get("candidate_ref"))
        moves.append({
            "candidate": _key(move.get("candidate_ref")),
            "action": _mapping(move.get("definition_ref")).get("action_key"),
            "family": move.get("family"),
            "step": move.get("step_index"),
            "score": move.get("selection_score"),
            "priority": priorities.get(move.get("move_id")),
            "targets": _targets(move.get("target_refs")),
        })
    candidates = []
    for step in _list(action.get("steps")):
        step = _mapping(step)
        constructed = {
            _mapping(item).get("candidate_id"): _mapping(item) for item in _list(step.get("constructed_candidates"))
        }
        gates = {_mapping(item).get("candidate_ref"): _mapping(item) for item in _list(step.get("gate_results"))}
        scores = {_mapping(item).get("candidate_ref"): _mapping(item) for item in _list(step.get("scored_candidates"))}
        for candidate_id, candidate in constructed.items():
            gate = gates.get(candidate_id, {})
            candidates.append({
                "step": step.get("step_index"),
                "candidate": _key(candidate_id),
                "action": _mapping(candidate.get("definition_ref")).get("action_key"),
                "targets": _targets(candidate.get("target_refs")),
                "gate": gate.get("status"),
                "gate_codes": _unique_strings([_mapping(item).get("code") for item in _list(gate.get("violations"))]),
                "score": _mapping(scores.get(candidate_id, {}).get("score")).get("score"),
                "selected_at_step": step.get("selected_candidate_ref") == candidate_id,
                "in_plan": candidate_id in selected_candidates,
            })
    safety = _mapping(action.get("safety"))
    return {
        "status": "ran",
        "selected_moves": moves,
        "candidates": candidates,
        "termination": plan.get("termination"),
        "shadow_only": action.get("shadow_only"),
        "safety_disposition": safety.get("disposition"),
        "safety_restriction": safety.get("restriction"),
    }


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def summarize_trace(record: Any, *, behavioral_review: str | None = None) -> dict[str, Any]:
    """Return the important diagnostic index for one terminal trace.

    This function deliberately never reads ``compiled_request_payload``,
    realization requests, message arrays, evidence spans, or candidate text.
    It is safe to unit test with either a SQLAlchemy row or a plain mapping.
    ``behavioral_review`` is the adjudicated outcome of a separate reviewed
    quality record, when one exists; shadow validation never implies it.
    """

    status = _value(record, "terminal_status")
    pipeline_payload = _value(record, "pipeline_trace_payload")
    failure_payload = _value(record, "failure_payload")
    skip_reason = _value(record, "skip_reason")
    realization = _mapping(_value(record, "realization_trace_payload"))
    realization_status = _value(record, "realization_status")

    failure = skip = None
    if status == "failed":
        if pipeline_payload is not None or skip_reason is not None:
            raise SummaryError("a failed trace cannot carry a pipeline trace or a skip reason")
        failure = _failure_projection(record)
        stage_status = _stage_statuses_for_failure(failure)
    elif status == "skipped":
        if pipeline_payload is not None or failure_payload is not None or not isinstance(skip_reason, str):
            raise SummaryError("a skipped trace carries exactly a skip reason")
        skip = {"reason": skip_reason}
        stage_status = {name: "not_run" for name in STAGES}
    elif status == "completed":
        if not isinstance(pipeline_payload, Mapping) or failure_payload is not None or skip_reason is not None:
            raise SummaryError("a completed trace carries exactly a pipeline trace")
        required = ("perception", "interpretation", "goal_snapshot", "cognitive_turn")
        if any(not isinstance(pipeline_payload.get(name), Mapping) for name in required):
            raise SummaryError("the completed pipeline trace is missing a stage")
        stage_status = {name: "ran" for name in STAGES}
        if not isinstance(pipeline_payload.get("requirement_resolution"), Mapping):
            # A historical runner had no requirement-resolution stage.
            stage_status["requirements"] = "not_run"
        if not isinstance(pipeline_payload.get("collaboration"), Mapping):
            # A historical runner had no workspace or collaborative-content stage.
            stage_status["workspace"] = stage_status["collaboration"] = "not_run"
    else:
        raise SummaryError("unregistered terminal status")

    pipeline = _mapping(pipeline_payload)
    sections: dict[str, dict[str, Any]]
    if status == "completed":
        turn = _mapping(pipeline.get("cognitive_turn"))
        sections = {
            "perception": _perception(_mapping(pipeline.get("perception"))),
            "interpretation": _interpretation(_mapping(pipeline.get("interpretation"))),
            "goals": _goals(_mapping(pipeline.get("goal_snapshot"))),
            "workspace": (_workspace(pipeline) if stage_status["workspace"] == "ran" else {"status": "not_run"}),
            "requirements": (_requirements(pipeline) if stage_status["requirements"] == "ran"
                             else {"status": "not_run"}),
            "collaboration": (_collaboration(pipeline) if stage_status["collaboration"] == "ran"
                              else {"status": "not_run"}),
            "appraisal": _appraisal(_mapping(turn.get("appraisal"))),
            "state": _state(_mapping(_mapping(turn.get("dynamic_state_update")).get("snapshot"))),
            "action": _action(_mapping(turn.get("action_selection"))),
        }
    else:
        sections = {name: {"status": stage_status[name]} for name in STAGES}

    realization_attempts = _list(realization.get("attempts"))
    reset_reason = _value(record, "segment_reset_reason")
    missing_predecessors = _list(_value(record, "missing_predecessor_refs", []))

    flags = []
    if reset_reason:
        flags.append(reset_reason)
    if missing_predecessors:
        flags.append("missing_predecessor")
    if failure is not None:
        flags.append(f"failed:{failure['stage']}")
    if skip is not None:
        flags.append(f"skipped:{skip['reason']}")
    if status == "completed":
        if sections["perception"]["context_truncated"]:
            flags.append("context_truncated")
        if sections["interpretation"]["unknown_keys"]:
            flags.append("interpretation_unresolved")
        appraisal_frames = _list(_mapping(_mapping(pipeline.get("cognitive_turn")).get("appraisal")).get("frames"))
        if not sections["appraisal"]["known_dimensions"] and appraisal_frames:
            flags.append("appraisal_unresolved")
        primary_goal = sections["requirements"].get("primary_goal")
        if primary_goal is not None and primary_goal["readiness"] == "blocked":
            flags.append("primary_request_blocked")
    if realization_status not in {None, "not_requested", "accepted_shadow"}:
        flags.append(f"realization_{realization_status}")
    if realization_status == "accepted_shadow" and realization.get("final_candidate") is None:
        flags.append("realization_missing_candidate")

    realization_timings = _mapping(_value(record, "realization_timings"))
    summary = {
        "summary_schema": SUMMARY_SCHEMA_VERSION,
        "trace": {
            "id": str(_value(record, "id")),
            "turn_id": str(_value(record, "turn_id")),
            "conversation_id": str(_value(record, "conversation_id")),
            "terminal_status": status,
            "segment_reset_reason": reset_reason,
            "missing_predecessor_count": len(missing_predecessors),
            "started_at": _timestamp(_value(record, "started_at")),
            "completed_at": _timestamp(_value(record, "completed_at")),
        },
        "flags": flags,
        "versions": _versions_projection(record),
        "failure": failure,
        "skip": skip,
        "review": _review_axes(realization_status, realization, behavioral_review),
        **sections,
        "realization": {
            "status": realization_status,
            "attempt_count": len(realization_attempts),
            "attempt_outcomes": [_mapping(item).get("outcome") for item in realization_attempts],
            "provider_error_codes": _unique_strings([
                _mapping(attempt).get("provider_error_code") for attempt in realization_attempts
            ]),
            "validation_codes": _unique_strings([
                _mapping(violation).get("code")
                for attempt in realization_attempts
                for violation in _list(_mapping(_mapping(attempt).get("validation")).get("violations"))
            ]),
            "validation_policy": realization.get("validation_policy", "strict-v0.1"),
            "warning_codes": _unique_strings([
                _mapping(warning).get("code")
                for attempt in realization_attempts
                for warning in _list(_mapping(_mapping(attempt).get("validation")).get("warnings"))
            ]),
            "has_final_candidate": realization.get("final_candidate") is not None,
            "unavailable_reason": realization.get("unavailable_reason"),
        },
        "latency": _latency_projection(pipeline, _value(record, "failure_payload")),
        "timings_ms": {
            "core": {
                key: value for key, value in _mapping(pipeline.get("timings")).items()
                if key.endswith("_ms") and isinstance(value, (int, float))
            },
            "terminal_compile": _mapping(_value(record, "timings")).get("compile_ms"),
            "realization": {
                key: value for key, value in realization_timings.items()
                if key.endswith("_ms") and isinstance(value, (int, float))
            },
        },
    }
    validate_summary(summary)
    return summary



def _latency_projection(pipeline, failure):
    """Only validated, closed telemetry fields; older traces have null call counts."""
    from .latency import LatencySpan
    pipeline = _mapping(pipeline)
    turn = _mapping(pipeline.get("cognitive_turn"))
    raw = _list(_mapping(turn.get("timings")).get("appraisal_spans"))
    orchestration = _mapping(pipeline.get("orchestration"))
    if orchestration:
        raw = _list(orchestration.get("spans"))
    raw += _list(_mapping(failure).get("latency_spans"))
    spans = []
    for item in raw:
        # Ignore extra stored fields rather than leaking arbitrary telemetry text.
        selected = {key: value for key, value in _mapping(item).items() if key in LatencySpan.model_fields}
        try:
            spans.append(LatencySpan.model_validate(selected).model_dump(mode="json"))
        except ValueError:
            continue
    calls = [span for span in spans if span["component"] == "provider_attempt" and span["status"] != "skipped"]
    processing_wall = _mapping(pipeline.get("timings")).get("total_ms")
    if (isinstance(processing_wall, bool) or not isinstance(processing_wall, (int, float))
            or not math.isfinite(processing_wall) or processing_wall < 0):
        processing_wall = None
    combined = any(s["stage"] in {"call_one", "call_two"} for s in spans)
    result = {
        "schema_version": "cognitive-latency-summary-v0.2" if combined else "cognitive-latency-summary-v0.1",
        "measurement_scope": "shadow_worker_attempt" if combined else "shadow_turn",
        "coverage": "two_call" if any(s["stage"] in {"call_one", "call_two"} for s in spans) else ("appraisal_only" if spans else "legacy_unavailable"),
        "queue_wait_ms": None, "response_ready_ms": None, "first_delivery_ms": None,
        "processing_wall_ms": processing_wall,
        "provider_work_sum_ms": sum(span["elapsed_ms"] for span in calls) if spans else None,
        "model_call_count": len(calls) if spans else None,
        "retry_count": sum(span["attempt_number"] is not None and span["attempt_number"] > 1 for span in calls) if spans else None,
        "retry_wait_ms": None,
        "stages": spans,
    }
    if orchestration.get('protocol')=='context-conditioned-two-call-v0.1':
        allowed_sections={'protocol','turn_context','evidence_regions','predicates','argument_specs','reference_scope','decision_context',
            'observations','readings','event_appraisals','existing_goal_impacts','unknowns','conflicts','content_ops','moves','segments','inquiries',
            *('decision_context.'+k for k in ('version','shared_context','understanding','state_after','goals','readiness',
                'content_requests','answer_permissions','choices','templates'))}
        def finite_number(value):
            return value if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0 else None
        attribution=[]
        for call in _list(orchestration.get('calls')):
            call=_mapping(call);raw=_mapping(call.get('attribution'))
            if call.get('stage') not in {'call_one','call_two'}:continue
            attribution.append(dict(stage=call['stage'],unit='unicode_characters',tokenizer=None,
                token_estimation='characters_divided_by_4_rough_estimate',first_token_ms=None,
                **{k:finite_number(raw.get(k)) for k in ('estimated_input_tokens','estimated_output_tokens','prompt_characters','schema_characters')},
                **{k:{name:finite_number(value) for name,value in _mapping(raw.get(k)).items() if name in allowed_sections}
                   for k in ('payload_sections','output_sections')}))
        result.update(schema_version='cognitive-latency-summary-v0.3',protocol=orchestration['protocol'],
            first_token_ms=None,section_attribution=attribution)
    elif orchestration.get('protocol')=='cognitive-two-call-v1':
        allowed_sections={'schema_version','protocol','focus','dialogue_context','perception','context','runtime','ontology',
            'accepted_understanding','state_after','active_goals','goal_readiness','personality','relationship',
            'runtime_constraints','planning_conditions','grounded_objects','eligible_candidates','policy_mode',
            'policy_mode_router','max_moves','iterative_planning','selection_factor_registry','policy_guidance',
            'events','existing_goal_impacts','unknowns','conflicts','selected_candidate_refs','primary_index',
            'stop_after_selected','selection_factors'}
        def finite_number(value):
            return value if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0 else None
        attribution=[]
        for call in _list(orchestration.get('calls')):
            call=_mapping(call);raw=_mapping(call.get('attribution'))
            if call.get('stage') not in {'call_one','call_two'}:continue
            attribution.append(dict(stage=call['stage'],unit='unicode_characters',tokenizer=None,
                token_estimation='characters_divided_by_4_rough_estimate',first_token_ms=None,
                **{k:finite_number(raw.get(k)) for k in ('estimated_input_tokens','estimated_output_tokens','prompt_characters','schema_characters')},
                **{k:{name:finite_number(value) for name,value in _mapping(raw.get(k)).items() if name in allowed_sections}
                   for k in ('payload_sections','output_sections')}))
        # Every measured component separately; middle stages and Realization are
        # in ``timings_ms``. Persistence happens after the trace is sealed.
        breakdown={}
        for span in spans:
            key=f"{span['stage']}.{span['component']}"
            breakdown[key]=breakdown.get(key,0.0)+span['elapsed_ms']
        result.update(schema_version='cognitive-latency-summary-v0.4',protocol=orchestration['protocol'],
            first_token_ms=None,section_attribution=attribution,component_ms=breakdown,
            persistence_ms=None,persistence_measurement='not_recorded_in_trace')
    return result

def validate_summary(summary: Mapping[str, Any]) -> None:
    """Closed-schema guard: fields cannot silently appear or disappear."""

    if set(summary) != TOP_LEVEL_KEYS or summary.get("summary_schema") != SUMMARY_SCHEMA_VERSION:
        raise SummaryError("summary top-level fields drifted from the closed schema")
    status = summary["trace"]["terminal_status"]
    for stage in STAGES:
        section = summary[stage]
        if section.get("status") not in STAGE_STATUSES:
            raise SummaryError("stage section has an unregistered status")
        ran = status == "completed" and section.get("status") == "ran"
        if status == "completed" and stage not in OPTIONAL_STAGES and not ran:
            raise SummaryError("a completed trace ran every stage but the historically optional ones")
        expected = COMPLETED_SECTION_KEYS[stage] if ran else frozenset({"status"})
        if set(section) != expected:
            raise SummaryError("stage section fields drifted from the closed schema")
    if (summary["failure"] is None) != (status != "failed"):
        raise SummaryError("only a failed summary carries a failure projection")
    if summary["failure"] is not None and set(summary["failure"]) != FAILURE_KEYS:
        raise SummaryError("failure projection fields drifted")
    if (summary["skip"] is None) != (status != "skipped"):
        raise SummaryError("only a skipped summary carries a skip projection")
    if summary["skip"] is not None and set(summary["skip"]) != SKIP_KEYS:
        raise SummaryError("skip projection fields drifted")
    review = summary["review"]
    if (
        set(review) != REVIEW_KEYS
        or review["fidelity_validation"] not in FIDELITY_VALIDATION_VALUES
        or review["behavioral_review"] not in BEHAVIORAL_REVIEW_VALUES
        or review["live_authority"] != LIVE_AUTHORITY
    ):
        raise SummaryError("review axes drifted from the closed schema")


def reveal_final_candidate(record: Any) -> str | None:
    """The final accepted candidate wording of one trace, for local review only.

    Returns nothing for any trace without an accepted final candidate; never
    returns captured messages, prompts, rejected candidates, or evidence text.
    """

    if _value(record, "realization_status") != "accepted_shadow":
        return None
    candidate = _mapping(_mapping(_value(record, "realization_trace_payload")).get("final_candidate"))
    text = candidate.get("text")
    return text if isinstance(text, str) else None


def latest_content_projection(
    record: Any, user_message: Any, *, focus_message_id: Any,
) -> dict[str, str | None]:
    """The two local-sensitive content fields requested by ``--latest``.

    The message must be the exact user focus row retained by the terminal
    trace.  Refusing a mismatched row prevents a query or fixture regression
    from silently printing content from another turn.
    """

    if _value(user_message, "role") != "user":
        raise SummaryError("the latest trace focus message is not a user message")
    if str(_value(user_message, "id")) != str(focus_message_id):
        raise SummaryError("the latest trace focus message identity differs")
    if str(_value(user_message, "turn_id")) != str(_value(record, "turn_id")):
        raise SummaryError("the latest trace focus message belongs to another turn")
    content = _value(user_message, "content")
    if not isinstance(content, str):
        raise SummaryError("the latest trace focus message has invalid content")
    return {
        "last_user_message": content,
        "last_realization_response": reveal_final_candidate(record),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Print a content-minimized summary of one cognitive shadow trace."
    )
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--latest", action="store_true", help="summarize the newest terminal trace")
    selector.add_argument("--trace-id", type=uuid.UUID, help="summarize this terminal trace UUID")
    selector.add_argument("--turn-id", type=uuid.UUID, help="summarize the trace for this turn UUID")
    parser.add_argument(
        "--reveal-final-candidate", action="store_true",
        help="LOCAL SENSITIVE DATA: also print the final accepted candidate wording of this trace only",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()

    # Database imports stay out of the pure extractor and occur only for CLI use.
    from sqlalchemy import select

    from app.db.models import ChatMessage, CognitiveShadowJob, CognitiveShadowTrace
    from app.db.session import SessionLocal

    statement = select(CognitiveShadowTrace)
    if args.trace_id:
        statement = statement.where(CognitiveShadowTrace.id == args.trace_id)
    elif args.turn_id:
        statement = statement.where(CognitiveShadowTrace.turn_id == args.turn_id)
    else:
        statement = statement.order_by(CognitiveShadowTrace.completed_at.desc()).limit(1)

    with SessionLocal() as session:
        trace = session.scalars(statement).first()
        if trace is None:
            raise SystemExit("No matching cognitive shadow trace found.")
        shadow_job = (
            session.scalar(
                select(CognitiveShadowJob).where(
                    CognitiveShadowJob.id == trace.job_id,
                    CognitiveShadowJob.turn_id == trace.turn_id,
                    CognitiveShadowJob.user_id == trace.user_id,
                )
            )
            if args.latest
            else None
        )
        user_message = (
            session.scalar(
                select(ChatMessage).where(
                    ChatMessage.id == shadow_job.user_message_id,
                    ChatMessage.turn_id == trace.turn_id,
                    ChatMessage.role == "user",
                )
            )
            if shadow_job is not None
            else None
        )

    try:
        output: dict[str, Any] = summarize_trace(trace)
        if args.latest:
            if shadow_job is None or user_message is None:
                raise SummaryError("the latest trace has no matching shadow job and focus user message")
            output = {
                **output,
                **latest_content_projection(
                    trace, user_message, focus_message_id=shadow_job.user_message_id,
                ),
            }
    except SummaryError as exc:
        raise SystemExit(f"The stored trace is inconsistent and was not summarized: {exc}") from None
    if args.latest:
        print(
            "WARNING: printing the latest user message and accepted shadow realization response. "
            "This is local-sensitive review data; do not share, log, or paste it into tickets.",
            file=sys.stderr,
        )
    if args.reveal_final_candidate:
        print(
            "WARNING: printing shadow candidate wording. This is local-sensitive review data; "
            "do not share, log, or paste it into tickets.",
            file=sys.stderr,
        )
        output = {**output, "final_candidate_text": reveal_final_candidate(trace)}
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
