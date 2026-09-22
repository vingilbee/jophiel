"""Pure construction of revision 1 for a new conversation."""

from __future__ import annotations

from .models import (
    PersonalityProfile,
    ScopeType,
    StateKey,
    StateSnapshot,
    StateValue,
)
from .policy import DEFAULT_BASELINE_SCORES, POLICY_VERSION, band_for_score

INITIAL_CONVERSATION_KEYS = (
    StateKey.VALENCE,
    StateKey.ACTIVATION,
    StateKey.ENGAGEMENT,
    StateKey.SOCIAL_ALIGNMENT,
    StateKey.INTERACTION_STRAIN,
)


class DynamicStateSnapshotFactory:
    """Create the one deterministic initial snapshot for a conversation."""

    def create_initial(
        self,
        *,
        conversation_ref: str,
        cognitive_turn_id: str,
        personality: PersonalityProfile,
    ) -> StateSnapshot:
        values = tuple(
            self._initial_value(
                state_key=state_key,
                conversation_ref=conversation_ref,
                personality=personality,
            )
            for state_key in INITIAL_CONVERSATION_KEYS
        )
        return StateSnapshot(
            state_snapshot_id=f"{cognitive_turn_id}:state:r1",
            cognitive_turn_id=cognitive_turn_id,
            conversation_ref=conversation_ref,
            revision=1,
            supersedes_snapshot_id=None,
            status="current",
            policy_version=POLICY_VERSION,
            personality_mapper_version=personality.mapper_version,
            source_appraisal_revision_ids=(),
            source_outcome_ids=(),
            values=values,
            attention=(),
            derived_controls=None,
        )

    @staticmethod
    def _initial_value(
        *,
        state_key: StateKey,
        conversation_ref: str,
        personality: PersonalityProfile,
    ) -> StateValue:
        control = personality.state_controls.get(state_key)
        baseline = (
            control.baseline_score
            if control is not None
            else DEFAULT_BASELINE_SCORES[state_key]
        )
        return StateValue(
            state_key=state_key,
            scope_type=ScopeType.CONVERSATION,
            scope_ref=conversation_ref,
            status="known",
            score=baseline,
            band=band_for_score(state_key, baseline),
            baseline_score=baseline,
            evidence_refs=(f"state-initialization:{conversation_ref}",),
            active=True,
            learnable=True,
        )
