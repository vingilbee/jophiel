"""Pure personality/state-to-delivery compilation.

Personality changes delivery, never facts, safety, authorization, selected
moves, or epistemic status.  Dynamic State changes bounded delivery controls
only.  Every control is resolved through ``style_manifest``; nothing here can
add, remove, or reorder a move.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..contracts.actions import ActionPersonalityProfile, PersonalityStatus
from ..contracts.realization import (
    DeliveryBand, DeliveryControls, ResolvedDeliveryProfile, StateControlSource, TraitBandSource,
)
from ..dynamic_state.models import StateSnapshot
from . import style_manifest as manifest
from .action_manifest import ActionRealizationEntry, StylePermission

TRAITS = ("curiosity", "warmth", "playfulness", "directness")
STATE_ORDER = (
    "response_energy", "repair_pressure", "clarification_pressure", "exploration_readiness",
    "initiative_readiness", "slow_cognition_pressure",
)
CORRECTION_ACTIONS = frozenset({"provide_information.correct", "manage_dialogue.self_correct"})
REPAIR_ACTIONS = frozenset({"manage_dialogue.repair", "manage_dialogue.self_correct"})


def trait_sources(personality: ActionPersonalityProfile) -> tuple[TraitBandSource, ...]:
    out = []
    for trait in TRAITS:
        value = getattr(personality, trait)
        if personality.status != PersonalityStatus.KNOWN or value is None:
            out.append(TraitBandSource(trait=trait, value=None, band=DeliveryBand.UNKNOWN))
        else:
            number = manifest.canonical_unit_decimal(value)
            out.append(TraitBandSource(trait=trait, value=str(number), band=manifest.band_for(number)))
    return tuple(out)


def compile_delivery_profile(
    personality: ActionPersonalityProfile,
    state: StateSnapshot,
    entries: Sequence[ActionRealizationEntry],
) -> ResolvedDeliveryProfile:
    """Resolve the delivery profile for the selected moves' manifest entries."""

    sources = trait_sources(personality)
    bands = {item.trait: item.band for item in sources}
    keys = {entry.action_key for entry in entries}
    permissions = set().union(*(entry.style_permissions for entry in entries)) if entries else set()
    seeks = any(key.startswith("seek_information.") for key in keys)
    plays = StylePermission.PLAYFULNESS in permissions
    warm = StylePermission.WARMTH_MARKER in permissions
    corrects = bool(keys & CORRECTION_ACTIONS)
    repairs = bool(keys & REPAIR_ACTIONS)

    derived = state.derived_controls
    state_bands = {name: (getattr(derived, name).value if derived is not None else None) for name in STATE_ORDER}
    applies = {
        "response_energy": derived is not None,
        "repair_pressure": repairs,
        "clarification_pressure": seeks,
        "exploration_readiness": seeks,
    }
    state_sources = []
    for name in STATE_ORDER:
        if name in {"initiative_readiness", "slow_cognition_pressure"}:
            effect = "intentionally_unused"
        elif derived is None:
            effect = "unavailable"
        else:
            effect = "applied" if applies[name] else "not_applicable"
        state_sources.append(StateControlSource(control=name, band=state_bands[name], effect=effect))

    def state_value(name: str, table: tuple[str, str, str]) -> str:
        return manifest.state_choice(state_bands[name], *table)

    directness = bands["directness"]
    controls = DeliveryControls(
        warmth_band=bands["warmth"],
        directness_band=directness,
        playfulness_band=bands["playfulness"],
        curiosity_band=bands["curiosity"],
        transition_style=manifest.TRANSITION_STYLE[directness],
        correction_style=manifest.CORRECTION_STYLE[directness] if corrects else "not_applicable",
        follow_up_style=manifest.FOLLOW_UP_STYLE[bands["curiosity"]] if seeks else "not_applicable",
        question_tone=manifest.QUESTION_TONE[directness] if seeks else "not_applicable",
        content_lead_style=manifest.CONTENT_LEAD_STYLE[directness],
        compression_preference=(state_value("response_energy", manifest.COMPRESSION) if derived is not None
                                else "balanced"),
        style_marker_budget=(min(manifest.MAXIMUM_STYLE_MARKER_BUDGET, manifest.STYLE_MARKER_BUDGET[bands["warmth"]])
                             if warm else 0),
        playfulness_intensity=manifest.PLAYFULNESS_INTENSITY[bands["playfulness"]] if plays else "none",
        question_directness=(state_value("clarification_pressure", manifest.QUESTION_DIRECTNESS)
                             if seeks else "not_applicable"),
        question_openness=(state_value("exploration_readiness", manifest.QUESTION_OPENNESS)
                           if seeks else "not_applicable"),
        repair_explicitness=(state_value("repair_pressure", manifest.REPAIR_EXPLICITNESS)
                             if repairs else "not_applicable"),
    )
    return ResolvedDeliveryProfile(
        personality_profile_ref=personality.profile_id,
        personality_mapper_version=personality.mapper_version,
        personality_status=personality.status.value,
        trait_sources=sources,
        state_snapshot_ref=state.state_snapshot_id,
        state_sources=tuple(state_sources),
        controls=controls,
    )


__all__ = ["CORRECTION_ACTIONS", "REPAIR_ACTIONS", "STATE_ORDER", "TRAITS", "compile_delivery_profile", "trait_sources"]
