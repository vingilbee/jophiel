"""Realization V0 personality and state style tests (section 19.3)."""

from __future__ import annotations

import itertools

import pytest

from app.ai.Cognitive.system.v0.contracts.realization import DeliveryBand
from app.ai.Cognitive.system.v0.dynamic_state.models import DerivedControls, QualitativeBand
from app.ai.Cognitive.system.v0.realization_v0 import band_for, compile_delivery_profile
from app.ai.Cognitive.system.v0.realization_v0 import style_manifest
from app.ai.Cognitive.system.v0.realization_v0.action_manifest import MANIFEST
from action_v0_support import request_for
from realization_v0_support import (
    case_inputs, compile_request, decimal_arg, known_personality, live_inputs, proposition, reference, validate_text,
)

CORE = request_for("acknowledge-without-agreement").personality.source_core_memory_ref
STATE = request_for("acknowledge-without-agreement").dynamic_state
ALL_ENTRIES = tuple(MANIFEST.values())
EXTREMES = (0.0, 1.0)


@pytest.mark.parametrize(("value", "band"), [
    (0, "very_low"), (0.199999, "very_low"), (0.2, "low"), (0.399999, "low"), (0.4, "moderate"),
    (0.599999, "moderate"), (0.6, "high"), (0.799999, "high"), (0.8, "very_high"), (1, "very_high"),
])
def test_exact_band_boundaries(value, band) -> None:
    assert band_for(value) == band


def test_eight_tenths_is_unambiguously_very_high() -> None:
    assert band_for("0.800000") == DeliveryBand.VERY_HIGH
    assert band_for(0.8) == DeliveryBand.VERY_HIGH
    profile = compile_delivery_profile(known_personality(CORE, warmth=0.8), STATE, ALL_ENTRIES)
    warmth = next(item for item in profile.trait_sources if item.trait == "warmth")
    assert (warmth.value, warmth.band) == ("0.800000", "very_high")
    assert profile.controls.warmth_band == "very_high"
    for bad in (-0.000001, 1.000001, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            band_for(bad)


def test_model_receives_bands_and_controls_never_raw_numbers() -> None:
    from app.ai.Cognitive.system.v0.realization_v0 import canonical_payload_json, project_payload

    request = compile_request(case_inputs("acknowledge-without-agreement", (), personality=known_personality(
        CORE, curiosity=0.123456, warmth=0.654321, playfulness=0.333333, directness=0.777777)))
    payload = canonical_payload_json(project_payload(request))
    for raw in ("0.123456", "0.654321", "0.333333", "0.777777", "trait_sources", "personality"):
        assert raw not in payload
    assert '"warmth_band":"high"' in payload
    # Provenance keeps the numbers on the request, never in the payload.
    assert {item.value for item in request.delivery_profile.trait_sources} == {
        "0.123456", "0.654321", "0.333333", "0.777777"}


@pytest.mark.parametrize("trait", ["curiosity", "warmth", "playfulness", "directness"])
def test_one_personality_source_changes_only_its_declared_controls(trait) -> None:
    profiles = [
        compile_delivery_profile(known_personality(CORE, **{trait: value}), STATE, ALL_ENTRIES).controls.model_dump()
        for value in (0.0, 0.3, 0.5, 0.7, 0.9, 1.0)
    ]
    changed = {key for key in profiles[0] if len({str(item[key]) for item in profiles}) > 1}
    assert changed and changed <= style_manifest.TRAIT_CONTROLS[trait]


def test_invariant_controls_hold_at_every_extreme() -> None:
    for values in itertools.product(EXTREMES, repeat=4):
        personality = known_personality(CORE, **dict(zip(("curiosity", "warmth", "playfulness", "directness"), values)))
        controls = compile_delivery_profile(personality, STATE, ALL_ENTRIES).controls
        assert controls.endearment_policy == "forbid"
        assert controls.emotional_claim_policy == "forbid_unprovided"
        assert controls.exclamation_policy == "avoid"
        assert controls.style_marker_budget <= 1


def test_playfulness_has_no_effect_without_a_selected_play_action() -> None:
    entries = tuple(entry for entry in ALL_ENTRIES if not entry.action_key.startswith("social_play."))
    controls = [compile_delivery_profile(known_personality(CORE, playfulness=value), STATE, entries).controls
                for value in (0.0, 0.5, 1.0)]
    assert {item.playfulness_intensity for item in controls} == {"none"}
    assert len({item.model_copy(update={"playfulness_band": "unknown"}) for item in controls}) == 1
    with_play = compile_delivery_profile(known_personality(CORE, playfulness=1.0), STATE,
                                         (MANIFEST["social_play.joke"],)).controls
    assert with_play.playfulness_intensity == "light"


def test_curiosity_cannot_create_a_question() -> None:
    requests = [compile_request(case_inputs("acknowledge-without-agreement", (),
                                            personality=known_personality(CORE, curiosity=value)))
                for value in EXTREMES]
    assert requests[0].action_plan == requests[1].action_plan
    assert requests[0].move_bindings == requests[1].move_bindings
    assert {item.delivery_profile.controls.follow_up_style for item in requests} == {"not_applicable"}
    for request in requests:
        assert validate_text(request, "Got your message. What else is on your mind?").status == "rejected"


def test_warmth_cannot_create_an_acknowledgement_apology_or_offer() -> None:
    requests = [compile_request(case_inputs("missing-recipient-open-clarification", (),
                                            personality=known_personality(CORE, warmth=value)))
                for value in EXTREMES]
    assert requests[0].action_plan == requests[1].action_plan
    assert requests[0].constraints == requests[1].constraints
    for addition, code in (("Got it. ", "unplanned_move_detected"), ("Sorry to ask. ", "unplanned_move_detected"),
                           ("I can help with that. ", "unplanned_offer")):
        result = validate_text(requests[1], addition + "Who should receive it?",
                               {1: addition + "Who should receive it?"})
        assert code in {item.code for item in result.violations}, addition


FOUR_MOVE_ITEMS = [
    proposition("content-estimate", ("frame-1",), "the estimate", predicate="estimate.level",
                arguments=({"role": "value", "value_type": "entity", "value": "high", "surface_anchor": "higher than usual"},),
                source="trusted_runtime_observation", attribution="runtime"),
    proposition("content-cause", ("prop-estimate-cause",), "the portion", predicate="portion.size_comparison",
                arguments=({"role": "value", "value_type": "entity", "value": "larger", "surface_anchor": "larger than usual"},),
                source="trusted_runtime_observation", attribution="runtime", status="tentative", certainty="tentative"),
    proposition("content-example", ("example-servings",), "the plate", predicate="portion.servings",
                arguments=(decimal_arg("2.000000", "serving"),), source="trusted_runtime_observation",
                attribution="runtime", certainty="approximate"),
]


def test_directness_cannot_reorder_moves_or_remove_uncertainty() -> None:
    requests = [compile_request(case_inputs("four-move-plan-order-contrast", FOUR_MOVE_ITEMS,
                                            personality=known_personality(CORE, directness=value)))
                for value in EXTREMES]
    assert requests[0].discourse_controls.move_order == requests[1].discourse_controls.move_order
    assert requests[0].constraints == requests[1].constraints
    assert "uncertainty_marker_required" in {item.code for item in requests[1].constraints}
    assert {item.delivery_profile.controls.content_lead_style for item in requests} == {"context_first", "content_first"}
    text = ("The estimate came out higher than usual because the portion was larger than usual. "
            "As an example behind the estimate and the portion, the plate held 2 servings. Got your message. "
            "Does the estimate come from the portion looking larger than usual?")
    moves = {1: "The estimate came out higher than usual because the portion was larger than usual.",
             2: "As an example behind the estimate and the portion, the plate held 2 servings.",
             3: "Got your message.", 4: "Does the estimate come from the portion looking larger than usual?"}
    for request in requests:
        codes = {item.code for item in validate_text(request, text, moves).violations}
        assert "required_uncertainty_dropped" in codes


def test_dynamic_state_consumes_only_reviewed_derived_controls() -> None:
    entries = (MANIFEST["seek_information.clarify"], MANIFEST["manage_dialogue.repair"])
    personality = known_personality(CORE)
    state = live_inputs("hi")["state"]  # a real current-turn snapshot with derived controls
    assert isinstance(state.derived_controls, DerivedControls)
    base = compile_delivery_profile(personality, state, entries)
    # Raw state values and attention never change delivery.
    stripped = state.model_copy(update={"values": (), "attention": ()})
    assert compile_delivery_profile(personality, stripped, entries).controls == base.controls

    def with_controls(**changes) -> object:
        controls = state.derived_controls.model_copy(update=changes)
        return state.model_copy(update={"derived_controls": controls})

    low = QualitativeBand.VERY_LOW
    high = QualitativeBand.VERY_HIGH
    assert compile_delivery_profile(personality, with_controls(response_energy=low), entries).controls \
        .compression_preference == "concise"
    assert compile_delivery_profile(personality, with_controls(repair_pressure=high), entries).controls \
        .repair_explicitness == "explicit"
    assert compile_delivery_profile(personality, with_controls(clarification_pressure=high), entries).controls \
        .question_directness == "direct"
    assert compile_delivery_profile(personality, with_controls(exploration_readiness=high), entries).controls \
        .question_openness == "open"
    for unused in ("initiative_readiness", "slow_cognition_pressure"):
        for band in (low, high):
            profile = compile_delivery_profile(personality, with_controls(**{unused: band}), entries)
            assert profile.controls == base.controls
            assert next(item for item in profile.state_sources if item.control == unused).effect == "intentionally_unused"
    # Repair and clarification pressure only apply when their moves are selected.
    unrelated = (MANIFEST["give_feedback.acknowledge"],)
    assert compile_delivery_profile(personality, with_controls(repair_pressure=high), unrelated).controls \
        .repair_explicitness == "not_applicable"
    assert compile_delivery_profile(personality, with_controls(clarification_pressure=high), unrelated).controls \
        .question_directness == "not_applicable"
    missing = state.model_copy(update={"derived_controls": None})
    profile = compile_delivery_profile(personality, missing, entries)
    assert profile.controls.compression_preference == "balanced"
    assert {item.effect for item in profile.state_sources} == {"unavailable", "intentionally_unused"}


SEMANTIC_ITEMS = [
    reference("content-earlier-request", ("interpretation-prior-request",), ("your earlier request",),
              kind="request", source="current_user_statement", attribution="user", status="unresolved"),
    proposition("content-result", ("prop-result-10",), "the result", predicate="measurement.percentage",
                arguments=(decimal_arg("10.000000", "percent"),), source="authorized_semantic_memory"),
]


def test_meaning_content_and_actions_are_invariant_across_every_style_extreme() -> None:
    requests = []
    for values in itertools.product(EXTREMES, repeat=4):
        personality = known_personality(CORE, **dict(zip(("curiosity", "warmth", "playfulness", "directness"), values)))
        requests.append(compile_request(case_inputs("action-selection-prior-failure", SEMANTIC_ITEMS,
                                                    personality=personality)))
    reference_request = requests[0]
    for request in requests:
        assert request.action_plan == reference_request.action_plan
        assert request.content_items == reference_request.content_items
        assert request.move_bindings == reference_request.move_bindings
        assert request.constraints == reference_request.constraints
        assert request.discourse_controls.move_order == reference_request.discourse_controls.move_order
    assert len({request.delivery_profile.controls for request in requests}) > 1
