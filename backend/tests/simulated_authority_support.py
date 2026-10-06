"""The Gate 6 simulated-authority conversation runner (offline, hermetic).

Every turn runs the real path: capture -> live-shadow compiler -> lineage ->
``CognitivePipelineV0`` (model-backed semantic grounding, Goal, workspace
resolution, requirement resolution, collaborative content, Action) ->
Realization compilation.  Only the two models are reviewed doubles: the
semantic parser returns the reviewed corpus frames, and the collaboration
provider returns a reviewed structured candidate for each operation.

Between turns the explicit harness (``conversation_workspace_v0.simulation``)
injects a *marked* simulated delivery of exactly the moves Action selected:

- an outcome-time goal revision of the predecessor turn, from the
  ``ArtifactDeliveredEvent`` evidence that delivery supports; and
- ``simulated_authority`` workspace inputs (the predecessor's canonical
  snapshot, its turn record, and the simulated outcome).

Nothing here is used by live shadow, and no network call is made.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from cognitive_shadow_support import make_capture

from app.ai.Cognitive.system.v0.cognitive_pipeline_v0 import CognitivePipelineRequest, CognitivePipelineTrace
from app.ai.Cognitive.system.v0.collaborative_content_v0 import CollaborativeContentEngine
from app.ai.Cognitive.system.v0.collaborative_content_v0.doubles import (
    ScriptedCollaborationModel, empty_output, inquiry, item, operation,
)
from app.ai.Cognitive.system.v0.collaborative_content_v0.model_protocol import CollaborationModelPayload, SourceKind
from app.ai.Cognitive.system.v0.contracts.collaboration import WorkspaceTurnInputs
from app.ai.Cognitive.system.v0.contracts.goals import GoalSnapshot
from app.ai.Cognitive.system.v0.contracts.workspace import (
    DeliveredResponseOutcome, WorkspaceAuthorityMode, empty_workspace,
)
from app.ai.Cognitive.system.v0.conversation_workspace_v0.simulation import (
    delivery_events, next_workspace_inputs, simulated_delivery,
)
from app.ai.Cognitive.system.v0.goal_v0 import GoalEngine
from app.ai.Cognitive.system.v0.goal_v0.models import GoalUpdateRequest
from app.ai.Cognitive.system.v0.goal_v0.evidence_producer import produce_goal_evidence, trigger_for
from app.ai.Cognitive.system.v0.interpretation_v0 import InterpretationEngine
from app.ai.Cognitive.system.v0.interpretation_v0.model_grounding import ModelSemanticGrounder
from app.ai.Cognitive.system.v0.interpretation_v0.regression import FrozenCandidateModel, load_corpus
from app.ai.Cognitive.system.v0.live_shadow.compiler import (
    PredecessorRecord, compile_request, goal_turn_rollover_for, resolve_lineage,
)
from app.ai.Cognitive.system.v0.live_shadow.models import TerminalStatus
from app.ai.Cognitive.system.v0.live_shadow.realization import (
    collaboration_content_of, realization_inventory, requirement_content_of,
)
from app.ai.Cognitive.system.v0.live_shadow.service import build_pipeline
from app.ai.Cognitive.system.v0.realization_v0 import RealizationCompilerV0

SIMULATION_REF = "gate6-dinner-simulation"
CAPTURED_AT = "2026-10-01T18:{minute:02d}:00+00:00"
DINNER = next(case for case in load_corpus()["cases"] if case["case_id"] == "trace-collaborative-dinner")


# ---------------------------------------------------------------------------
# Model doubles
# ---------------------------------------------------------------------------

class DinnerSemanticModel:
    """The reviewed corpus frames; an inquiry answer cites the inquiry the payload offers.

    The corpus names its open inquiry by a fixed review id; in a live run the
    id is computed from the conversation, so the double answers whichever
    single open inquiry the protocol actually offered (never an invented one).
    """

    version = "frozen-candidate-double-v0.1"
    provenance = None

    def __init__(self, turns) -> None:
        self.candidates = {turn["user"]: turn["frozen_candidate"] for turn in turns}

    def generate(self, payload):
        focus = next(item for item in payload.messages if item.message_ref == payload.focus_message_ref)
        candidate = self.candidates[focus.text]
        frames = []
        for frame in candidate["frames"]:
            if frame["predicate"] == "inquiry_answer":
                [offered] = payload.open_inquiries
                frame = {**frame, "arguments": {**frame["arguments"], "inquiry_ref": offered.inquiry_ref}}
            frames.append(frame)
        return FrozenCandidateModel({"frames": frames}).generate(payload)


def _refs(payload: CollaborationModelPayload, kind: SourceKind) -> dict[str, str]:
    return {item.text: item.ref for item in payload.sources if item.kind == kind}


def _element(payload: CollaborationModelPayload, element_id: str) -> str:
    return f"{payload.artifact.revision_ref}#{element_id}"


def _request(payload: CollaborationModelPayload) -> str:
    [ref] = [item.ref for item in payload.sources if item.current_turn and item.kind in {
        SourceKind.USER_REQUEST, SourceKind.USER_ANSWER}]
    return ref


def _dish(dish_id, name, *, contains=(), traits=(), minutes=None):
    return {"dish_id": dish_id, "name": name, "content_class": "generated_proposal", "contains": list(contains),
            "traits": list(traits), "prep_time_minutes": minutes}


def _constraint(constraint_id, kind, value, *, reason="unspecified"):
    return {"constraint_id": constraint_id, "kind": kind, "value": value, "basis": "user_supplied_constraint",
            "reason": reason}


def dinner_collaboration(payload: CollaborationModelPayload) -> dict[str, Any]:
    """A reviewed structured candidate per operation (what a faithful provider returns)."""

    request = _request(payload)
    op = payload.operation.value
    if op == "create":
        needs = _refs(payload, SourceKind.USER_REQUIREMENT)
        veg, six, fast = needs["vegetarian"], needs["six people"], needs["under 30 minutes"]
        return empty_output(
            title="Vegetarian dinner for six",
            operations=[
                operation("set_servings", integer_value=6, integer_class="user_supplied_constraint", source_refs=[six]),
                operation("set_time_limit", integer_value=30, integer_class="user_supplied_constraint",
                          source_refs=[fast]),
                operation("add_constraint", constraint=_constraint("vegetarian", "dietary", "vegetarian"),
                          source_refs=[veg]),
                operation("set_main_dish", dish=_dish("main", "chickpea and spinach stew", contains=["dairy"],
                                                      traits=["one_pot", "quick"], minutes=25),
                          source_refs=[request]),
            ],
            content_items=[item("change", "A chickpea and spinach stew serves 6 and takes about 25 minutes.",
                                ["chickpea and spinach stew"], subject_ids=["main", "servings"],
                                source_refs=[request], numbers=[6, 25])],
        )
    if op == "explain":
        return empty_output(content_items=[
            item("explanation", "The chickpea and spinach stew has no meat, so it fits the vegetarian requirement.",
                 ["chickpea and spinach stew", "vegetarian"], claim_kind="constraint_fit",
                 subject_ids=["main", "vegetarian"], source_refs=[_element(payload, "main"),
                                                                  _element(payload, "vegetarian")]),
            item("explanation", "It cooks in one pot in about 25 minutes, inside the 30 minute limit.",
                 ["one pot"], claim_kind="constraint_fit", subject_ids=["main", "time_limit"],
                 source_refs=[_element(payload, "main"), _element(payload, "time_limit")], numbers=[25, 30]),
        ])
    if op == "revise":
        needs = _refs(payload, SourceKind.USER_REQUIREMENT)
        dairy, side = needs["dairy-free"], needs["add an inexpensive side"]
        return empty_output(
            operations=[
                operation("add_constraint", constraint=_constraint("dairy-free", "dietary", "dairy_free"),
                          source_refs=[dairy]),
                operation("update_dish", dish=_dish("main", "chickpea and spinach stew", traits=["one_pot", "quick"],
                                                    minutes=25), source_refs=[dairy]),
                operation("add_side_dish", dish=_dish("side-rice", "steamed rice", traits=["inexpensive"], minutes=20),
                          source_refs=[side]),
            ],
            content_items=[
                item("change", "The chickpea and spinach stew is now dairy-free.", ["chickpea and spinach stew",
                     "dairy-free"], subject_ids=["dairy-free", "main"], source_refs=[dairy]),
                item("change", "Steamed rice is an inexpensive side.", ["steamed rice"], subject_ids=["side-rice"],
                     source_refs=[side]),
            ],
            inquiries=[inquiry("dairy-free", asked_about="whether dairy-free is a preference or an allergy",
                               options=["preference", "allergy"], purpose="resolve_safety_relevant_context",
                               sensitivity="health", source_refs=[dairy])],
        )
    if op == "describe":
        return empty_output(content_items=[item(
            "description", "A fragrant chickpea and spinach stew served with fluffy steamed rice.",
            ["chickpea and spinach stew", "steamed rice"], content_class="artifact_content",
            subject_ids=["main", "side-rice"], source_refs=[_element(payload, "main"), _element(payload, "side-rice")],
        )])
    if op == "summarize":
        return empty_output(content_items=[item(
            "summary", "A vegetarian, dairy-free chickpea and spinach stew with steamed rice for 6, under 30 minutes.",
            ["chickpea and spinach stew", "steamed rice"], content_class="artifact_content",
            subject_ids=["dairy-free", "main", "servings", "side-rice", "time_limit", "vegetarian"],
            source_refs=[_element(payload, key) for key in ("dairy-free", "main", "servings", "side-rice",
                                                             "time_limit", "vegetarian")],
            numbers=[6, 30],
        )])
    if op == "evaluate":
        return empty_output(content_items=[
            item("evaluation", "The stew and rice already avoid dairy, so the plan still fits the allergy.",
                 ["the plan"], claim_kind="constraint_fit", subject_ids=["dairy-free", "main", "side-rice"],
                 source_refs=[_element(payload, key) for key in ("dairy-free", "main", "side-rice")]),
            item("safety_guidance", "It may help to check packaged ingredients such as stock for hidden dairy.",
                 ["hidden dairy"], content_class="uncertain_suggestion", claim_kind="general_practice",
                 subject_ids=["dairy-free"],
                 source_refs=[_element(payload, "dairy-free")]),
        ])
    raise AssertionError(f"no reviewed candidate for {op}")


def dinner_answer(payload: CollaborationModelPayload) -> dict[str, Any] | None:
    """Turn 7: the answer updates the attributed constraint reason (a revise of the open artifact)."""

    if not any(item.kind == SourceKind.USER_ANSWER for item in payload.sources):
        return None
    answer = _request(payload)
    needs = list(_refs(payload, SourceKind.USER_REQUIREMENT).values())
    return empty_output(
        operations=[operation("update_constraint", constraint=_constraint(
            "dairy-free", "dietary", "dairy_free", reason="allergy"), source_refs=[answer, *needs])],
        content_items=[item("change", "The dairy-free requirement is now recorded as a guest's allergy.",
                            ["dairy-free", "allergy"], content_class="attributed_user_claim",
                            subject_ids=["dairy-free"], source_refs=[answer])],
    )


def dinner_script(payload: CollaborationModelPayload):
    return dinner_answer(payload) or dinner_collaboration(payload)


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------

@dataclass
class SimulatedTurn:
    index: int
    user: str
    request: CognitivePipelineRequest
    trace: CognitivePipelineTrace
    outcome: DeliveredResponseOutcome
    realization: Any
    delivered_goal_snapshot: GoalSnapshot | None = None
    payloads: list = field(default_factory=list)


def _pipeline(turn_id: str, semantic, collaboration):
    pipeline = build_pipeline(turn_id)
    pipeline.interpretation_engine = InterpretationEngine(grounder=ModelSemanticGrounder(semantic))
    pipeline.collaboration_engine = CollaborativeContentEngine(collaboration)
    return pipeline


def _outcome_revision(previous: SimulatedTurn, outcome: DeliveredResponseOutcome) -> GoalSnapshot:
    """The predecessor turn's outcome-time goal revision (only from delivered evidence)."""

    trace = previous.trace
    events = delivery_events(trace, outcome)
    if not events:
        return trace.goal_snapshot
    evidence = produce_goal_evidence(trace.goal_snapshot, events)
    request = previous.request
    return GoalEngine().update(GoalUpdateRequest(
        trigger=trigger_for(events), cognitive_turn_id=trace.cognitive_turn_id,
        conversation_ref=trace.conversation_ref, interpretation=trace.interpretation,
        runtime=request.runtime.to_goal_runtime(trace.cognitive_turn_id),
        previous_goal_snapshot=trace.goal_snapshot, previous_dynamic_state=request.previous_state,
        new_goal_evidence=evidence,
    )).snapshot


def _realize(trace: CognitivePipelineTrace, request: CognitivePipelineRequest):
    action = trace.action_selection
    focus = next(item for item in request.messages if item.message_id == request.focus_message_id).content
    return RealizationCompilerV0().compile(
        action_result=action, inventory=realization_inventory(
            action, None, requirement_content_of(trace), collaboration_content_of(trace)),
        personality=trace.cognitive_turn.input_snapshot.action_personality,
        state=trace.cognitive_turn.dynamic_state_update.snapshot, language_tag="en", current_user_utterance=focus,
        safety_basis=action.safety, configured_maximum_characters=900,
    )


def run_simulated_conversation(turns=None, *, script=dinner_script, deliver=None,
                               progress: list | None = None) -> list[SimulatedTurn]:
    """Run ``turns`` uninterrupted, feeding each marked simulated delivery forward.

    ``deliver(index, trace)`` may return a ``DeliveryStatus`` to inject (default:
    every selected response is delivered).
    """

    turns = turns or DINNER["turns"]
    semantic = DinnerSemanticModel(turns)
    user, conversation = str(uuid.uuid4()), str(uuid.uuid4())
    history: list[tuple[str, str]] = []
    ids: list[str] = []
    out: list[SimulatedTurn] = [] if progress is None else progress
    previous: SimulatedTurn | None = None
    first_turn_segment = None
    for index, turn in enumerate(turns, start=1):
        history.append(("user", turn["user"]))
        ids.append(str(uuid.uuid4()))
        capture = make_capture(user_ref=user, conversation_ref=conversation, contents=tuple(history),
                               message_ids=tuple(ids), captured_at=CAPTURED_AT.format(minute=index),
                               previous_user_message_id=previous.request.focus_message_id if previous else None)
        predecessor = None
        if previous is not None:
            predecessor = PredecessorRecord(
                terminal_id=f"simulated-terminal:{previous.trace.cognitive_turn_id}", turn_id=previous.trace.cognitive_turn_id,
                user_ref=user, conversation_ref=conversation, focus_message_id=previous.request.focus_message_id,
                segment_id=first_turn_segment, status=TerminalStatus.COMPLETED,
                # Live shadow refuses a simulated workspace; the harness supplies its own
                # simulated-authority inputs below, so lineage sees only state and goals.
                pipeline_trace=previous.trace.model_copy(update={"collaboration": None}),
            )
        lineage = resolve_lineage(capture, predecessor)
        assert lineage.reset_reason is None or previous is None, lineage.reset_reason
        first_turn_segment = lineage.segment_id
        request = compile_request(capture, lineage)
        if previous is None:
            inputs = WorkspaceTurnInputs(
                authority_mode=WorkspaceAuthorityMode.SIMULATED_AUTHORITY,
                previous_snapshot=empty_workspace(conversation, WorkspaceAuthorityMode.SIMULATED_AUTHORITY),
            )
            updates = {"workspace_inputs": inputs}
        else:
            delivered_goals = previous.delivered_goal_snapshot
            updates = {
                "workspace_inputs": next_workspace_inputs(previous.trace, (previous.outcome,)),
                "previous_goal_snapshot": delivered_goals,
                "goal_turn_rollover": goal_turn_rollover_for(capture, delivered_goals),
            }
        request = CognitivePipelineRequest.model_validate({**request.model_dump(mode="python"), **updates})
        collaboration = ScriptedCollaborationModel(script)
        trace = _pipeline(capture.turn_id, semantic, collaboration).run(request)
        realization = _realize(trace, request)
        status = deliver(index, trace) if deliver else None
        outcome = simulated_delivery(
            trace, simulation_ref=SIMULATION_REF, observed_at=CAPTURED_AT.format(minute=index).replace(":00+", ":30+"),
            realization_ref=getattr(realization, "request_id", f"realization-skip:{trace.cognitive_turn_id}"),
            **({"delivery_status": status} if status is not None else {}),
        )
        current = SimulatedTurn(index=index, user=turn["user"], request=request, trace=trace, outcome=outcome,
                                realization=realization, payloads=collaboration.payloads)
        current.delivered_goal_snapshot = _outcome_revision(current, outcome)
        out.append(current)
        history.append(("assistant", turn["assistant"]))
        ids.append(str(uuid.uuid4()))
        previous = current
    return out


__all__ = ["DINNER", "DinnerSemanticModel", "SIMULATION_REF", "SimulatedTurn", "dinner_collaboration",
           "dinner_script", "run_simulated_conversation"]
