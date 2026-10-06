"""Project resolutions into existing Action inputs and closed-world Realization content.

The projection populates fields Action already understands -- it adds no
special case to the scorer:

- **answers**: ``producers_v0.project_action_objects`` exactly (a factual
  QUESTION whose answer content is the produced fact, or the explicit
  knowledge-gap record), and ``project_realization_content`` for wording;
- **clarification**: the exact unresolved semantic argument gets a
  user-owned ``missing_value`` descriptor about its goal, and every other
  representation of the same missing value is marked as represented by it,
  so it is asked once and only there.  Its Realization content is an
  anchored unresolved-element reference with a reviewed anchor naming the
  gap ("what you are referring to"), never the user's words;
- **requests**: one current-turn REQUEST per operational or unsafe request,
  with an explicit ``acceptance_status``, its registered capabilities,
  authorization and consent needs, trusted safety policy records, and
  grounded decline reasons -- typed CAPABILITY or EVIDENCE_RECORD objects,
  never meaning encoded in an identifier;
- **withheld or conflicting answers**: the question becomes a scoped,
  conversational REQUEST that can only be truthfully declined, with a typed
  reason (content not authorized, or trusted records conflict).  The
  protected or competing values themselves are never projected;
- **suppression**: an unresolved element of a request frame that is not a
  blocking user-input prerequisite is represented by the request (or answer
  question) itself, so the details of a prohibited or already-answered
  request are never elicited.

Wording is never produced here.  Realization content carries reviewed anchors
and typed predicates only.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass

from ..contracts.actions import (
    ActionGroundedObject, ActionRuntimeContext, ActionTargetDescriptor, ActionTargetType as T,
)
from ..contracts.goals import GoalSnapshot
from ..contracts.interpretation import InterpretationEnvelope
from ..contracts.realization import (
    ContentAttribution, ContentEpistemicStatus, ContentSourceKind, DialogueReference, DialogueReferenceKind,
    GroundedProposition, Polarity, PropositionTerm, RealizationContentItem, ReferenceForm,
)
from ..contracts.requirement_resolution import (
    REQUIREMENT_RESOLUTION_CONTENT_PRODUCER, RequirementActionBinding, RequirementActionHandoff, RequirementKind as K, RequirementReason as R,
    RequirementResolution, RequirementResolutionStatus as S,
)
from ..perception_v0.evidence import stable_id
from ..producers_v0.projection import project_action_objects, project_realization_content, question_object_id
from .engine import ContentRecord, RequirementResolutionOutcome
from .manifest import COLLABORATION_GOAL_DEFINITIONS, HANDLER_GOAL, capability_for_predicate, elicitation_anchor

REQUIREMENT_CONTENT_PRODUCER = REQUIREMENT_RESOLUTION_CONTENT_PRODUCER
# The reviewed anchor for the user's request.  It attributes the request to the
# user (the validator's user-attribution rule) and reads naturally in a decline.
REQUEST_ANCHOR = "your request"

# The acceptance status a blocking execution state gives the REQUEST, in
# precedence order (the first present wins).
_ACCEPTANCE_PRECEDENCE = ("prohibited", "unauthorized", "unavailable", "pending")
_DECLINABLE = frozenset({"prohibited", "unauthorized", "unavailable"})

# Blocking execution states that are grounded decline reasons, with the typed
# reason object each becomes and the predicate its Realization content carries.
# Every reason is the *negation* of a positive state (polarity ``negative``):
# "your request is not a registered capability", "... is not authorized", so a
# natural refusal ("I can't ...") preserves the recorded polarity.
_REASONS: dict[tuple[K, S], tuple[T, str]] = {
    (K.CAPABILITY, S.UNAVAILABLE): (T.CAPABILITY, "runtime.requested_capability_available"),
    (K.AUTHORIZATION, S.UNAUTHORIZED): (T.EVIDENCE_RECORD, "runtime.request_authorized"),
    (K.SAFETY, S.PROHIBITED): (T.EVIDENCE_RECORD, "application_safety.request_permitted"),
    (K.SAFETY, S.UNAVAILABLE): (T.EVIDENCE_RECORD, "application_safety.request_cleared"),
    (K.TOOL_OUTCOME, S.TEMPORARY_FAILURE): (T.EVIDENCE_RECORD, "runtime.requested_operation_completed"),
    (K.TOOL_OUTCOME, S.UNAVAILABLE): (T.EVIDENCE_RECORD, "runtime.requested_operation_available"),
}
# Answer content that exists but cannot be given: the typed reason (negated
# positive state) and the acceptance status of the conversational request it
# makes declinable.
_CONTENT_REFUSALS: dict[S, tuple[str, str]] = {
    S.UNAUTHORIZED: ("unauthorized", "runtime.requested_content_shareable"),
    S.CONFLICTING: ("unavailable", "runtime.requested_content_consistent"),
}
# Predicates distinguishing an unregistered from an unavailable capability.
_CAPABILITY_PREDICATES = {
    R.CAPABILITY_UNREGISTERED: "runtime.requested_capability_registered",
    R.CAPABILITY_UNAVAILABLE: "runtime.requested_capability_available",
}


def _digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]


def argument_ref(frame_id: str, argument: str) -> str:
    """The Action index reference of an unresolved semantic argument."""

    return f"{frame_id}#{argument}"


def request_object_id(goal_ref: str, frame_ref: str) -> str:
    return f"requirement-request:{_digest(goal_ref, frame_ref)}"


def reason_object_id(resolution: RequirementResolution) -> str:
    return f"requirement-reason:{resolution.resolution_id}"


def content_id_for(object_id: str) -> str:
    return f"requirement-content:{object_id}"


def unresolved_object_id(resolution: RequirementResolution) -> str:
    return f"requirement-unresolved:{resolution.resolution_id}"


# Projection v0.2: the elicitation anchor of each conversational-deliverable blocker.
_UNRESOLVED_ANCHORS: dict[K, str] = {
    K.ARTIFACT_TARGET: "target_ref", K.REQUESTED_CHANGE: "requested_change",
    K.REQUESTED_OUTCOME: "requested_outcome",
}


@dataclass(frozen=True)
class RequirementProjection:
    """Everything the projection adds to one turn, plus the handoff Action consumes."""

    grounded_objects: tuple[ActionGroundedObject, ...]
    target_descriptors: tuple[ActionTargetDescriptor, ...]
    realization_content: tuple[RealizationContentItem, ...]
    handoff: RequirementActionHandoff


class RequirementProjectorV0:
    """Deterministic projection of one resolution outcome into Action and Realization inputs."""

    def project(
        self, outcome: RequirementResolutionOutcome, *, interpretation: InterpretationEnvelope,
        goal_snapshot: GoalSnapshot, runtime: ActionRuntimeContext,
        existing_objects: tuple[ActionGroundedObject, ...] = (),
        existing_descriptors: tuple[ActionTargetDescriptor, ...] = (),
    ) -> RequirementProjection:
        snapshot = outcome.snapshot
        records = {item.resolution_ref: item for item in outcome.content_records}
        definitions = {record.goal_id: record.definition_ref.definition_key for record in goal_snapshot.records}
        frames = {frame.frame_id: frame for frame in interpretation.semantic_frames}
        taken = {item.object_id for item in existing_objects}
        described = {(item.target_type, item.ref) for item in existing_descriptors}
        objects: dict[str, ActionGroundedObject] = {}
        descriptors: dict[tuple[T, str], ActionTargetDescriptor] = {}
        content: dict[str, RealizationContentItem] = {}
        per_resolution: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

        def put_object(item: ActionGroundedObject, owner: str) -> None:
            if item.object_id in taken:
                raise ValueError("a projected object collides with an existing action object")
            objects.setdefault(item.object_id, item)
            per_resolution[owner]["objects"].add(item.object_id)

        descriptor_owners: dict[tuple[T, str], tuple[str, bool]] = {}

        def put_descriptor(item: ActionTargetDescriptor, owner: str, *, own_frame: bool = False) -> None:
            key = (item.target_type, item.ref)
            if key in described:
                return  # the dialogue stage already typed this object
            if key in descriptors and descriptors[key] != item:
                # Frames sharing evidence can both claim one argument unknown.
                # Only that case is resolved, by the unknown's own frame (its
                # exact semantic-compiler identity); every other disagreement
                # still fails closed.
                previous_owner, previous_own = descriptor_owners[key]
                if own_frame and not previous_own:
                    per_resolution[previous_owner]["descriptors"].discard(item.ref)
                elif previous_own and not own_frame:
                    return
                else:
                    raise ValueError("two resolutions describe one object differently")
            descriptors[key] = item
            descriptor_owners[key] = (owner, own_frame)
            per_resolution[owner]["descriptors"].add(item.ref)

        def put_content(item: RealizationContentItem, owner: str) -> None:
            content.setdefault(item.content_id, item)
            per_resolution[owner]["content"].add(item.content_id)

        grouped: dict[tuple[str, str], list[RequirementResolution]] = defaultdict(list)
        for resolution in snapshot.resolutions:
            grouped[(resolution.goal_ref, resolution.frame_ref)].append(resolution)

        for (goal_ref, frame_ref), group in sorted(grouped.items()):
            frame = frames[frame_ref]
            handler = definitions[goal_ref] == HANDLER_GOAL
            anchor_object: str | None = None

            # -- trusted content (answers) --------------------------------------
            for resolution in group:
                if resolution.kind != K.TRUSTED_CONTENT:
                    continue
                record: ContentRecord = records[resolution.resolution_id]
                projected = project_action_objects(record.query, record.result, record.gap)
                for item in projected:
                    put_object(item, resolution.resolution_id)
                for item in project_realization_content(record.query, record.result, record.gap):
                    put_content(item, resolution.resolution_id)
                if projected:
                    anchor_object = question_object_id(record.query)
                    if resolution.blocking:
                        per_resolution[resolution.resolution_id]["targets"].update(
                            item.object_id for item in projected if item.object_type in {T.QUESTION, T.EVIDENCE_RECORD}
                        )
                elif resolution.status in _CONTENT_REFUSALS:
                    anchor_object = self._content_refusal(goal_ref, frame, resolution, interpretation, runtime,
                                                          put_object, put_content, per_resolution)

            # -- the request object (actions) -----------------------------------------
            if handler:
                anchor_object = self._request(goal_ref, frame, group, interpretation, runtime,
                                              put_object, put_content, per_resolution)
            if definitions[goal_ref] in COLLABORATION_GOAL_DEFINITIONS:
                # A conversational deliverable's optional unstated arguments are
                # represented by its own request frame: they are never asked about.
                anchor_object = frame.frame_id
                for resolution in group:
                    if resolution.kind in _UNRESOLVED_ANCHORS and resolution.blocking \
                            and resolution.status == S.MISSING_USER_INPUT:
                        self._unresolved(goal_ref, frame, resolution, interpretation, put_object, put_content,
                                         put_descriptor, per_resolution)

            # -- missing user input: one canonical elicitation target --------------------
            elicited: set[str] = set()
            for resolution in group:
                if resolution.kind not in {K.REQUIRED_ARGUMENT, K.DIALOGUE_REFERENT}:
                    continue
                if not (resolution.blocking and resolution.status == S.MISSING_USER_INPUT):
                    continue
                name = resolution.argument_name
                arg_ref = argument_ref(frame.frame_id, name)
                unknowns = sorted(
                    item.unknown_id for item in interpretation.unknowns
                    if item.key == name and set(item.evidence_refs) & set(frame.evidence_refs)
                )
                indexed_arg = name in frame.unresolved_arguments
                canonical = arg_ref if indexed_arg else (unknowns[0] if unknowns else None)
                targets = set(ref for ref in resolution.subject_refs if ref.startswith("dialogue:"))
                if canonical is not None:
                    targets.add(canonical)
                    canonical_type = T.SEMANTIC_ARGUMENT if indexed_arg else T.UNKNOWN
                    # Two frames sharing evidence can each elicit the same missing
                    # argument: an unknown is described by its own frame (exact
                    # semantic-compiler identity) when both claim it.
                    own_id = stable_id("interpretation-unknown", interpretation.cognitive_turn_id,
                                       frame.frame_id, name)
                    put_descriptor(ActionTargetDescriptor(
                        ref=canonical, target_type=canonical_type, owner="user", unknown_kind="missing_value",
                        about_refs=(goal_ref,),
                    ), resolution.resolution_id, own_frame=indexed_arg or canonical == own_id)
                    for ref in unknowns:
                        if ref != canonical:
                            targets.add(ref)
                            put_descriptor(ActionTargetDescriptor(
                                ref=ref, target_type=T.UNKNOWN, represents_ref=canonical,
                            ), resolution.resolution_id, own_frame=ref == own_id)
                    elicited.update({canonical, *unknowns, arg_ref})
                    put_content(DialogueReference(
                        content_id=content_id_for(canonical), cognitive_turn_id=interpretation.cognitive_turn_id,
                        source_kind=ContentSourceKind.CURRENT_USER_STATEMENT, source_refs=(frame.frame_id,),
                        represents_refs=(canonical,), authorized_for_realization=True,
                        epistemic_status=ContentEpistemicStatus.UNRESOLVED, attribution=ContentAttribution.USER,
                        surface_anchors=(elicitation_anchor(name),),
                        reference_kind=DialogueReferenceKind.UNRESOLVED_ELEMENT, target_ref=canonical,
                        reference_form=ReferenceForm.ANCHORED,
                    ), resolution.resolution_id)
                per_resolution[resolution.resolution_id]["targets"].update(targets)

            # -- suppression of non-blocking unresolved elements ---------------------------
            if anchor_object is not None:
                owner = group[0].resolution_id
                for name in frame.unresolved_arguments:
                    ref = argument_ref(frame.frame_id, name)
                    if ref not in elicited:
                        put_descriptor(ActionTargetDescriptor(
                            ref=ref, target_type=T.SEMANTIC_ARGUMENT, represents_ref=anchor_object,
                        ), owner)
                for item in interpretation.unknowns:
                    if item.unknown_id in elicited or not (set(item.evidence_refs) & set(frame.evidence_refs)):
                        continue
                    if item.key not in frame.unresolved_arguments:
                        continue
                    own = item.unknown_id == stable_id(
                        "interpretation-unknown", interpretation.cognitive_turn_id, frame.frame_id, item.key)
                    put_descriptor(ActionTargetDescriptor(
                        ref=item.unknown_id, target_type=T.UNKNOWN, represents_ref=anchor_object,
                    ), owner, own_frame=own)

        bindings = tuple(
            RequirementActionBinding(
                resolution_ref=resolution.resolution_id, goal_ref=resolution.goal_ref, blocking=resolution.blocking,
                target_refs=tuple(sorted(per_resolution[resolution.resolution_id]["targets"]))
                if resolution.blocking else (),
                object_refs=tuple(sorted(per_resolution[resolution.resolution_id]["objects"])),
                descriptor_refs=tuple(sorted(per_resolution[resolution.resolution_id]["descriptors"])),
                content_refs=tuple(sorted(per_resolution[resolution.resolution_id]["content"])),
            )
            for resolution in snapshot.resolutions
        )
        return RequirementProjection(
            grounded_objects=tuple(objects[key] for key in sorted(objects)),
            target_descriptors=tuple(descriptors[key] for key in sorted(descriptors, key=lambda k: (k[1], k[0].value))),
            realization_content=tuple(content[key] for key in sorted(content)),
            handoff=RequirementActionHandoff(snapshot=snapshot, bindings=bindings),
        )

    # -- conversational-deliverable blockers (projection v0.2) ---------------------------
    def _unresolved(self, goal_ref, frame, resolution, interpretation, put_object, put_content, put_descriptor,
                    per_resolution) -> None:
        """A user-owned unresolved element the clarification must target exactly once."""

        turn = interpretation.cognitive_turn_id
        object_id = unresolved_object_id(resolution)
        owner = resolution.resolution_id
        put_object(ActionGroundedObject(
            object_id=object_id, object_type=T.UNKNOWN, cognitive_turn_id=turn, source_refs=frame.evidence_refs,
            about_refs=(frame.frame_id, goal_ref), owner="user", unresolved=True, asserted_by="user",
        ), owner)
        put_descriptor(ActionTargetDescriptor(
            ref=object_id, target_type=T.UNKNOWN, owner="user", unknown_kind="missing_value", about_refs=(goal_ref,),
        ), owner)
        put_content(DialogueReference(
            content_id=content_id_for(object_id), cognitive_turn_id=turn,
            source_kind=ContentSourceKind.CURRENT_USER_STATEMENT, source_refs=(frame.frame_id,),
            represents_refs=(object_id,), authorized_for_realization=True,
            epistemic_status=ContentEpistemicStatus.UNRESOLVED, attribution=ContentAttribution.USER,
            surface_anchors=(elicitation_anchor(_UNRESOLVED_ANCHORS[resolution.kind]),),
            reference_kind=DialogueReferenceKind.UNRESOLVED_ELEMENT, target_ref=object_id,
            reference_form=ReferenceForm.ANCHORED,
        ), owner)
        per_resolution[owner]["targets"].add(object_id)

    # -- withheld or conflicting answers -------------------------------------------------
    def _content_refusal(self, goal_ref, frame, resolution, interpretation, runtime, put_object, put_content,
                         per_resolution) -> str:
        turn = interpretation.cognitive_turn_id
        acceptance, predicate = _CONTENT_REFUSALS[resolution.status]
        request_id = request_object_id(goal_ref, frame.frame_id)
        reason_id = reason_object_id(resolution)
        reason = ActionGroundedObject(
            object_id=reason_id, object_type=T.EVIDENCE_RECORD, cognitive_turn_id=turn,
            source_refs=(runtime.runtime_snapshot_ref,), about_refs=(frame.frame_id,), asserted_by="runtime",
            epistemic_status="supported",
        )
        request = ActionGroundedObject(
            object_id=request_id, object_type=T.REQUEST, cognitive_turn_id=turn, source_refs=(frame.frame_id,),
            about_refs=(frame.frame_id, goal_ref), focus_contribution=True, asserted_by="user", owner="user",
            requested_by_focus=True, fulfillment_mode="conversational", acceptance_status=acceptance,
            decline_reason_refs=(reason_id,),
        )
        owner = resolution.resolution_id
        put_object(reason, owner)
        put_object(request, owner)
        put_content(GroundedProposition(
            content_id=content_id_for(reason_id), cognitive_turn_id=turn,
            source_kind=ContentSourceKind.TRUSTED_RUNTIME_OBSERVATION, source_refs=(runtime.runtime_snapshot_ref,),
            represents_refs=(reason_id,), authorized_for_realization=True,
            epistemic_status=ContentEpistemicStatus.GROUNDED, attribution=ContentAttribution.RUNTIME,
            subject=PropositionTerm(ref=request_id, surface_anchor=REQUEST_ANCHOR), predicate=predicate,
            polarity=Polarity.NEGATIVE,
        ), owner)
        put_content(DialogueReference(
            content_id=content_id_for(request_id), cognitive_turn_id=turn,
            source_kind=ContentSourceKind.CURRENT_USER_STATEMENT, source_refs=(frame.frame_id,),
            represents_refs=(request_id,), authorized_for_realization=True,
            epistemic_status=ContentEpistemicStatus.OBSERVED, attribution=ContentAttribution.USER,
            surface_anchors=(REQUEST_ANCHOR,), reference_kind=DialogueReferenceKind.REQUEST, target_ref=request_id,
            reference_form=ReferenceForm.ANCHORED,
        ), owner)
        if resolution.blocking:
            per_resolution[resolution.resolution_id]["targets"].add(request_id)
        return request_id

    # -- requests ---------------------------------------------------------------------
    def _request(self, goal_ref, frame, group, interpretation, runtime, put_object, put_content, per_resolution) -> str:
        turn = interpretation.cognitive_turn_id
        request_id = request_object_id(goal_ref, frame.frame_id)
        blocking = [item for item in group if item.blocking]
        statuses = set()
        reasons: list[str] = []
        for resolution in blocking:
            if resolution.kind == K.SAFETY:
                statuses.add("prohibited" if resolution.status == S.PROHIBITED else "unauthorized")
            elif resolution.kind == K.AUTHORIZATION:
                statuses.add("unauthorized")
            elif resolution.kind in {K.CAPABILITY, K.TOOL_OUTCOME}:
                statuses.add("unavailable")
            elif resolution.status == S.MISSING_USER_INPUT:
                statuses.add("pending")
        acceptance = next((item for item in _ACCEPTANCE_PRECEDENCE if item in statuses), "acceptable")
        entry = capability_for_predicate(frame.predicate)
        consent_missing = any(item.kind == K.CONSENT and item.status != S.SATISFIED for item in group)
        prohibited = any(item.kind == K.SAFETY and item.status == S.PROHIBITED for item in group)

        consent_refs: tuple[str, ...] = ()
        reason_objects: list[ActionGroundedObject] = []
        for resolution in blocking:
            spec = _REASONS.get((resolution.kind, resolution.status))
            if spec is None or acceptance not in _DECLINABLE:
                continue
            object_type, predicate = spec
            if resolution.kind == K.CAPABILITY:
                predicate = _CAPABILITY_PREDICATES[resolution.reason_code]
            reason_id = reason_object_id(resolution)
            source = resolution.evidence_refs[0] if resolution.evidence_refs else runtime.runtime_snapshot_ref
            reason = ActionGroundedObject(
                object_id=reason_id, object_type=object_type, cognitive_turn_id=turn, source_refs=(source,),
                about_refs=(frame.frame_id,), asserted_by="runtime", epistemic_status="supported",
                capability_status=(
                    ("unregistered" if resolution.reason_code == R.CAPABILITY_UNREGISTERED else "unavailable")
                    if resolution.kind == K.CAPABILITY else None
                ),
            )
            reason_objects.append(reason)
            reasons.append(reason_id)
            put_object(reason, resolution.resolution_id)
            put_content(GroundedProposition(
                content_id=content_id_for(reason_id), cognitive_turn_id=turn,
                source_kind=ContentSourceKind.TRUSTED_RUNTIME_OBSERVATION, source_refs=(source,),
                represents_refs=(reason_id,), authorized_for_realization=True,
                epistemic_status=ContentEpistemicStatus.GROUNDED, attribution=ContentAttribution.RUNTIME,
                subject=PropositionTerm(ref=request_id, surface_anchor=REQUEST_ANCHOR), predicate=predicate,
                polarity=Polarity.NEGATIVE,
            ), resolution.resolution_id)
        if consent_missing:
            consent = next(item for item in group if item.kind == K.CONSENT)
            consent_id = reason_object_id(consent)
            put_object(ActionGroundedObject(
                object_id=consent_id, object_type=T.EVIDENCE_RECORD, cognitive_turn_id=turn,
                source_refs=(runtime.runtime_snapshot_ref,), about_refs=(frame.frame_id,), asserted_by="runtime",
                epistemic_status="unknown",
            ), consent.resolution_id)
            consent_refs = (consent_id,)

        request = ActionGroundedObject(
            object_id=request_id, object_type=T.REQUEST, cognitive_turn_id=turn, source_refs=(frame.frame_id,),
            about_refs=(frame.frame_id, goal_ref), focus_contribution=True, asserted_by="user", owner="user",
            requested_by_focus=True, fulfillment_mode="task", acceptance_status=acceptance,
            decline_reason_refs=tuple(reasons) if acceptance in _DECLINABLE else (),
            required_capabilities=(entry.capability_key,) if entry is not None else (),
            requires_authorization=bool(entry is not None and entry.requires_authorization),
            requires_consent_refs=consent_refs,
            safety_policy_refs=tuple(runtime.policy_records) if prohibited else (),
        )
        owner = group[0].resolution_id
        put_object(request, owner)
        put_content(DialogueReference(
            content_id=content_id_for(request_id), cognitive_turn_id=turn,
            source_kind=ContentSourceKind.CURRENT_USER_STATEMENT, source_refs=(frame.frame_id,),
            represents_refs=(request_id,), authorized_for_realization=True,
            epistemic_status=ContentEpistemicStatus.OBSERVED, attribution=ContentAttribution.USER,
            surface_anchors=(REQUEST_ANCHOR,), reference_kind=DialogueReferenceKind.REQUEST, target_ref=request_id,
            reference_form=ReferenceForm.ANCHORED,
        ), owner)
        for resolution in group:
            per_resolution[resolution.resolution_id]["objects"].add(request_id)
            if resolution.blocking and resolution.kind in {K.CAPABILITY, K.AUTHORIZATION, K.SAFETY, K.TOOL_OUTCOME}:
                per_resolution[resolution.resolution_id]["targets"].add(request_id)
        return request_id


__all__ = [
    "REQUEST_ANCHOR", "REQUIREMENT_CONTENT_PRODUCER", "RequirementProjection", "RequirementProjectorV0",
    "content_id_for", "reason_object_id", "request_object_id", "unresolved_object_id",
]
