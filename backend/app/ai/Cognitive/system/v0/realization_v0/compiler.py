"""Pure Action-to-Realization request compilation (``realization-compiler-v0.3``).

``RealizationCompilerV0.compile`` uses no model, database, clock, random value,
network, memory retrieval, or provider import.  It validates lineage, resolves
every selected move through the reviewed manifest, binds only authorized
current-turn content, compiles the delivery profile, unions every constraint
without weakening it, and builds one immutable request atomically.  There is
no partial request: missing required content is a typed skip, never neutral
text and never a request for the model to fill the gap.
"""

from __future__ import annotations
from ..contracts.actions import action_versions_supported

from dataclasses import dataclass

from ..contracts.actions import (
    ActionPersonalityProfile, ActionSafetyBasis, ActionSelectionResult, ActionTargetType, ActionVersions,
    GroundedActionMove, MovePriority, ObjectStatus, SafetyRestriction,
)
from ..contracts.realization import (
    ATTRIBUTED_SPEAKERS, GLOBAL_PROHIBITIONS, GLOBAL_SUBJECT, NON_ASSERTABLE_STATUSES, REALIZABLE_SENSITIVITY,
    REALIZATION_COMPILER_VERSION, SUPPORTED_REALIZATION_LANGUAGE, ArgumentValueType, ArtifactPresentation,
    AuthorizedQuote, InquiryPrompt,
    ConstraintOrigin, ContentBinding, ContentEpistemicStatus, DialogueReference, DiscourseControls, GapKind,
    GroundedProposition, GroundedQuestion, KnowledgeGap, Modality, Polarity, RealizationActionPlan,
    RealizationConstraintItem, RealizationContentInventory, RealizationFailurePhase, RealizationMoveBinding,
    RealizationLinguisticContext, RealizationMoveSpec, RealizationProvenance, RealizationRequestV0,
    RealizationSkip, RealizationStatus,
    ReferenceFormKind, content_is_generic, realization_uuid, request_digest_for, sha256_json,
)
from ..dynamic_state.models import StateSnapshot
from . import style_manifest
from .action_manifest import ActionRealizationEntry, BindingRequirement, entry_for
from .content import ContentIndex, move_references
from .errors import RealizationExecutionError
from .style_mapper import compile_delivery_profile
from .validator import CONSTRAINT_ENFORCEMENT

COMPILER_VERSION = REALIZATION_COMPILER_VERSION
_TENTATIVE_STATUSES = frozenset({ContentEpistemicStatus.TENTATIVE, ContentEpistemicStatus.ATTRIBUTED})
_GOAL_CLOSURE_TARGETS = frozenset({ActionTargetType.GOAL, ActionTargetType.TOPIC, ActionTargetType.PARTIAL_PLAN})
_CORRECTED_ROLE = "target:primary"
_REPLACEMENT_ROLES = frozenset({"evidence:replacement", "evidence:correction", "parameter:correction_content_refs"})
# Roles that refer to corrected or known-false content as the failure being
# addressed, never as an assertion (the validator forbids restating it).
_NON_ASSERTABLE_ROLES = frozenset({
    ("support_socially.apologize", "evidence:subject"),
    ("manage_dialogue.repair", "target:member"),
})


@dataclass(frozen=True)
class RealizationCompileSkip:
    """A typed reason no request can exist; it never becomes model input."""

    status: RealizationStatus
    skip: RealizationSkip


def _fail(phase: RealizationFailurePhase, code: str) -> RealizationExecutionError:
    return RealizationExecutionError(phase, code)


def _selector_matches(selector: str, role: str) -> bool:
    return role == selector or (selector.endswith(":*") and role.startswith(selector[:-1]))


def _qualifies(item, requirement: BindingRequirement) -> bool:
    if item.kind not in requirement.kinds:
        return False
    if requirement.anchored and content_is_generic(item):
        return False
    if requirement.tentative and not (
        isinstance(item, GroundedProposition) and item.epistemic_status in _TENTATIVE_STATUSES
    ):
        return False
    return True


class RealizationCompilerV0:
    """Deterministic, pure compilation of one selected plan into one realization request."""

    version = COMPILER_VERSION

    def compile(
        self,
        *,
        action_result: ActionSelectionResult,
        inventory: RealizationContentInventory,
        personality: ActionPersonalityProfile,
        state: StateSnapshot,
        language_tag: str | None,
        current_user_utterance: str,
        safety_basis: ActionSafetyBasis,
        configured_maximum_characters: int,
    ) -> RealizationRequestV0 | RealizationCompileSkip:
        turn = action_result.cognitive_turn_id
        self._validate_lineage(action_result, inventory, personality, state, language_tag, current_user_utterance,
                               safety_basis,
                               configured_maximum_characters)
        moves = action_result.plan.moves

        # 3. Every selected action resolves through the reviewed manifest.
        entries: list[ActionRealizationEntry] = []
        unsupported = []
        for move in moves:
            entry = entry_for(move.definition_ref.action_key, move.definition_ref.definition_version)
            if entry is None:
                unsupported.append(move.move_id)
            else:
                entries.append(entry)
        if unsupported:
            return RealizationCompileSkip(RealizationStatus.SKIPPED_UNSUPPORTED_ACTION, RealizationSkip(
                code="action_not_in_realization_manifest", subject_refs=tuple(sorted(unsupported)),
            ))

        # 4-5. Resolve refs through the authorized content index.
        index = ContentIndex(inventory)
        bindings: list[tuple[ContentBinding, ...]] = []
        missing: list[str] = []
        for move, entry in zip(moves, entries):
            move_bindings, move_missing = self._bind_move(move, entry, index, turn)
            bindings.append(move_bindings)
            missing.extend(move_missing)
        if missing:
            return RealizationCompileSkip(RealizationStatus.SKIPPED_MISSING_CONTENT, RealizationSkip(
                code="required_content_missing", subject_refs=tuple(sorted(set(missing))),
            ))

        # 7-8. Personality and reviewed Dynamic State controls -> delivery profile.
        try:
            delivery = compile_delivery_profile(personality, state, entries)
        except ValueError as exc:
            raise _fail(RealizationFailurePhase.STYLE_MAPPING, "delivery_profile_invalid") from exc

        bound_ids = sorted({binding.content_ref for group in bindings for binding in group})
        content_items = tuple(index.get(content_id) for content_id in bound_ids)
        constraints = self._constraints(action_result, safety_basis, moves, entries, content_items)
        move_specs = self._move_specs(action_result)
        move_bindings = tuple(
            self._move_binding(move, group, index, constraints) for move, group in zip(moves, bindings)
        )
        compression = delivery.controls.compression_preference
        discourse = DiscourseControls(
            move_order=tuple(move.move_id for move in moves),
            maximum_sentences=max(len(moves), min(
                style_manifest.MAXIMUM_SENTENCES_CAP, style_manifest.SENTENCES_PER_MOVE[compression] * len(moves),
            )),
            maximum_characters=min(configured_maximum_characters,
                                   style_manifest.CHARACTERS_PER_MOVE[compression] * len(moves)),
            allowed_reference_forms=tuple(
                form for form in ReferenceFormKind
                if form != ReferenceFormKind.ANCHORED_SURFACE or any(not content_is_generic(item) for item in content_items)
            ),
            already_expressed_content_refs=(),
        )
        provenance = RealizationProvenance(
            action_result_ref=action_result.result_id, action_request_digest=action_result.request_digest,
            action_context_ref=action_result.context_ref, action_versions=action_result.versions,
            safety_decision_ref=safety_basis.decision_ref, safety_restriction=safety_basis.restriction,
            inventory_ref=inventory.inventory_id, content_producer_refs=inventory.producer_refs,
            configured_maximum_characters=configured_maximum_characters,
        )
        request_id = realization_uuid(
            COMPILER_VERSION, "request", action_result.result_id, inventory.inventory_id, personality.profile_id,
            state.state_snapshot_id, str(language_tag), sha256_json(current_user_utterance),
            str(configured_maximum_characters),
        )
        fields = dict(
            request_id=request_id, cognitive_turn_id=turn, conversation_ref=action_result.conversation_ref,
            language_tag=language_tag,
            linguistic_context=RealizationLinguisticContext(text=current_user_utterance),
            action_result_ref=action_result.result_id,
            action_plan=RealizationActionPlan(
                plan_ref=action_result.plan.plan_id, moves=move_specs,
                primary_move_ref=action_result.plan.primary_move_ref,
                supporting_move_refs=action_result.plan.supporting_move_refs,
            ),
            content_items=content_items, move_bindings=move_bindings, discourse_controls=discourse,
            delivery_profile=delivery, constraints=constraints, provenance=provenance,
        )
        # 10-12. Build atomically, hash canonically, reparse and require equality.
        try:
            draft = RealizationRequestV0.model_construct(**fields, request_digest="0" * 64)
            request = RealizationRequestV0(**fields, request_digest=request_digest_for(draft.model_dump(mode="json")))
            if RealizationRequestV0.model_validate(request.model_dump(mode="json")) != request:
                raise ValueError("request changed across a JSON round trip")
        except ValueError as exc:
            raise _fail(RealizationFailurePhase.REQUEST_COMPILATION, "request_contract_invalid") from exc
        return request

    # ------------------------------------------------------------------
    # 1-2. Ownership and version lineage
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_lineage(
        result, inventory, personality, state, language_tag, current_user_utterance, safety_basis, maximum,
    ) -> None:
        phase = RealizationFailurePhase.REQUEST_VALIDATION
        if not isinstance(result, ActionSelectionResult) or result.shadow_only is not True:
            raise _fail(phase, "action_result_not_shadow_only")
        if not action_versions_supported(result.versions):
            raise _fail(phase, "action_versions_unsupported")
        if not result.plan.moves:
            raise _fail(phase, "empty_action_plan")
        if language_tag != SUPPORTED_REALIZATION_LANGUAGE:
            raise _fail(phase, "language_unsupported")
        if not isinstance(current_user_utterance, str):
            raise _fail(phase, "current_user_utterance_invalid")
        if safety_basis != result.safety:
            raise _fail(phase, "safety_basis_mismatch")
        if safety_basis.restriction == SafetyRestriction.RESPONSE:
            raise _fail(phase, "safety_blocks_response")
        if (state.cognitive_turn_id, state.conversation_ref) != (result.cognitive_turn_id, result.conversation_ref):
            raise _fail(phase, "state_lineage_mismatch")
        if not isinstance(personality, ActionPersonalityProfile):
            raise _fail(phase, "personality_profile_invalid")
        if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
            raise _fail(phase, "maximum_characters_invalid")
        if not isinstance(inventory, RealizationContentInventory):
            raise _fail(RealizationFailurePhase.CONTENT_RESOLUTION, "inventory_invalid")
        if inventory.cognitive_turn_id != result.cognitive_turn_id:
            raise _fail(RealizationFailurePhase.CONTENT_RESOLUTION, "inventory_turn_mismatch")

    # ------------------------------------------------------------------
    # 4-5. Binding
    # ------------------------------------------------------------------

    @staticmethod
    def _integrity(item, turn: str) -> None:
        phase = RealizationFailurePhase.CONTENT_RESOLUTION
        if item.authorized_for_realization is not True:
            raise _fail(phase, "content_unauthorized")
        if item.cognitive_turn_id != turn:
            raise _fail(phase, "content_cross_turn")
        if item.status != ObjectStatus.CURRENT:
            raise _fail(phase, "content_not_current")
        if item.sensitivity_class not in REALIZABLE_SENSITIVITY:
            raise _fail(phase, "content_sensitivity_not_permitted")

    def _bind_move(
        self, move: GroundedActionMove, entry: ActionRealizationEntry, index: ContentIndex, turn: str,
    ) -> tuple[tuple[ContentBinding, ...], list[str]]:
        references = tuple(move_references(move))
        out: dict[tuple[str, str, str], ContentBinding] = {}
        for role, ref in references:
            for item in index.resolve(ref):
                if item.kind not in entry.allowed_content_kinds:
                    continue
                if content_is_generic(item) and not (
                    role in entry.generic_roles and item.reference_kind in entry.generic_reference_kinds
                ):
                    continue
                self._integrity(item, turn)
                if item.epistemic_status in NON_ASSERTABLE_STATUSES and (entry.action_key, role) not in _NON_ASSERTABLE_ROLES \
                        and not (entry.profile == "correction" and role == _CORRECTED_ROLE):
                    raise _fail(RealizationFailurePhase.CONTENT_RESOLUTION, "non_assertable_content_bound")
                binding = ContentBinding(role=role, action_ref=ref, content_ref=item.content_id)
                out[binding.sort_key] = binding
        bound = tuple(out[key] for key in sorted(out))
        items = {binding.content_ref: index.get(binding.content_ref) for binding in bound}
        missing: list[str] = []

        for requirement in entry.required_bindings:
            matching = [binding for binding in bound if _selector_matches(requirement.selector, binding.role)
                        and _qualifies(items[binding.content_ref], requirement)]
            if requirement.every:
                refs = {ref for role, ref in references if _selector_matches(requirement.selector, role)}
                covered = {binding.action_ref for binding in matching}
                if not refs or refs - covered:
                    missing.append(f"{move.move_id}#{requirement.selector}")
            if len({binding.content_ref for binding in matching}) < requirement.minimum:
                missing.append(f"{move.move_id}#{requirement.selector}")

        count = len(items)
        if count < entry.minimum_content_items:
            missing.append(f"{move.move_id}#minimum_content")
        if entry.maximum_content_items is not None and count > entry.maximum_content_items:
            raise _fail(RealizationFailurePhase.CONTENT_RESOLUTION, "content_cardinality_exceeded")

        key = entry.action_key
        anchored = [item for item in items.values() if not content_is_generic(item)]
        if key == "manage_dialogue.greet" and not items and not any(
            evidence.role == "locator" for evidence in move.supporting_evidence_refs
        ):
            missing.append(f"{move.move_id}#opening_context")
        if key == "manage_dialogue.close" and any(
            target.target_type in _GOAL_CLOSURE_TARGETS for target in move.target_refs
        ) and not anchored:
            missing.append(f"{move.move_id}#goal_completion_content")
        if key == "seek_information.check_understanding" and move.parameters.get("question_form") != "check":
            raise _fail(RealizationFailurePhase.ACTION_MANIFEST_RESOLUTION, "question_form_not_check")
        if entry.profile == "correction":
            corrected = {binding.content_ref for binding in bound if binding.role == _CORRECTED_ROLE}
            replacement = {binding.content_ref for binding in bound if binding.role in _REPLACEMENT_ROLES}
            if corrected & replacement:
                raise _fail(RealizationFailurePhase.CONTENT_RESOLUTION, "correction_roles_conflict")
        for item in items.values():
            if isinstance(item, GroundedQuestion) and not set(item.option_content_refs) <= set(items):
                missing.append(f"{move.move_id}#question_options")
        return bound, missing

    # ------------------------------------------------------------------
    # 6. Exact move projection
    # ------------------------------------------------------------------

    @staticmethod
    def _move_specs(result: ActionSelectionResult) -> tuple[RealizationMoveSpec, ...]:
        priorities = {dict(item)["move_ref"]: dict(item)["priority"] for item in result.plan.move_priorities}
        return tuple(
            RealizationMoveSpec(
                move_ref=move.move_id, step_index=move.step_index, action_key=move.definition_ref.action_key,
                definition_version=move.definition_ref.definition_version, family=move.family,
                priority=MovePriority(priorities[move.move_id]), target_refs=move.target_refs,
                parameters=move.parameters, realization_constraints=move.realization_constraints,
            )
            for move in result.plan.moves
        )

    # ------------------------------------------------------------------
    # 9. Constraint union (never weakened)
    # ------------------------------------------------------------------

    @staticmethod
    def _constraints(result, safety: ActionSafetyBasis, moves, entries, items) -> tuple[RealizationConstraintItem, ...]:
        out: dict[tuple, RealizationConstraintItem] = {}

        def add(code: str, subject: str, origin: ConstraintOrigin, evidence: tuple[str, ...] = ()) -> None:
            item = RealizationConstraintItem(code=code, subject_ref=subject, origin=origin, evidence_refs=evidence)
            out[item.sort_key] = item

        for move, entry in zip(moves, entries):
            for constraint in move.realization_constraints:
                add(constraint.code, constraint.subject_ref, ConstraintOrigin.ACTION, constraint.evidence_refs)
            for code in (*entry.mandatory_constraint_codes, *entry.additional_constraint_codes):
                add(code, move.move_id, ConstraintOrigin.ACTION_MANIFEST)
        if safety.restriction == SafetyRestriction.COMMITMENTS:
            add("no_operation_commitment", safety.decision_ref, ConstraintOrigin.APPLICATION_SAFETY,
                (safety.decision_ref,))
        for item in items:
            subject = item.content_id
            if content_is_generic(item):
                continue  # a generic reference carries no wording to restrict
            if item.attribution in ATTRIBUTED_SPEAKERS or item.epistemic_status == ContentEpistemicStatus.ATTRIBUTED:
                add("preserve_attribution", subject, ConstraintOrigin.CONTENT)
            if isinstance(item, GroundedProposition):
                add("preserve_epistemic_status", subject, ConstraintOrigin.CONTENT)
                if item.requires_uncertainty:
                    add("uncertainty_marker_required", subject, ConstraintOrigin.CONTENT)
                if item.polarity == Polarity.NEGATIVE:
                    add("preserve_polarity", subject, ConstraintOrigin.CONTENT)
                if item.modality != Modality.ASSERTED:
                    add("preserve_modality", subject, ConstraintOrigin.CONTENT)
                if any(arg.value_type in {ArgumentValueType.DECIMAL, ArgumentValueType.INTEGER, ArgumentValueType.DATE}
                       for arg in item.arguments):
                    add("preserve_numeric_value", subject, ConstraintOrigin.CONTENT)
            elif isinstance(item, KnowledgeGap):
                add("no_gap_answer", subject, ConstraintOrigin.CONTENT)
                if item.gap_kind == GapKind.NO_RECORD:
                    add("absence_is_not_negation", subject, ConstraintOrigin.CONTENT)
            elif isinstance(item, AuthorizedQuote):
                add("preserve_quote_verbatim", subject, ConstraintOrigin.CONTENT)
            elif isinstance(item, GroundedQuestion):
                add("preserve_question_form", subject, ConstraintOrigin.CONTENT)
            elif isinstance(item, ArtifactPresentation):
                # Shared work keeps its epistemic class: a proposal is never a fact.
                add("preserve_epistemic_status", subject, ConstraintOrigin.CONTENT)
                if item.requires_hedge:
                    add("uncertainty_marker_required", subject, ConstraintOrigin.CONTENT)
                if item.arguments:
                    add("preserve_numeric_value", subject, ConstraintOrigin.CONTENT)
            elif isinstance(item, InquiryPrompt):
                add("preserve_question_form", subject, ConstraintOrigin.CONTENT)
            elif isinstance(item, DialogueReference):
                add("preserve_reference_scope", subject, ConstraintOrigin.CONTENT)
        for code in GLOBAL_PROHIBITIONS:
            add(code, GLOBAL_SUBJECT, ConstraintOrigin.GLOBAL)
        unenforceable = sorted({item.code for item in out.values()} - set(CONSTRAINT_ENFORCEMENT))
        if unenforceable:
            raise _fail(RealizationFailurePhase.REQUEST_COMPILATION, "constraint_unenforceable")
        return tuple(out[key] for key in sorted(out))

    @staticmethod
    def _move_binding(move, group, index: ContentIndex, constraints) -> RealizationMoveBinding:
        bound = {binding.content_ref for binding in group}
        subjects = {move.move_id, *(target.ref for target in move.target_refs), *bound}
        return RealizationMoveBinding(
            move_ref=move.move_id, action_key=move.definition_ref.action_key, content_refs=group,
            question_refs=tuple(sorted(ref for ref in bound if isinstance(index.get(ref), GroundedQuestion))),
            target_surface_refs=tuple(sorted({binding.content_ref for binding in group
                                              if binding.role.startswith("target:")})),
            constraint_codes=tuple(sorted({item.code for item in constraints if item.subject_ref in subjects})),
        )


__all__ = ["COMPILER_VERSION", "RealizationCompileSkip", "RealizationCompilerV0"]
