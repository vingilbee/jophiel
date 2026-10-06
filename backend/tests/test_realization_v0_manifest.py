"""Realization V0 action-manifest tests (section 19.2)."""

from __future__ import annotations

import pytest

from app.ai.Cognitive.system.v0.action_selection_v0.registry import (
    BOUNDARY_ONLY_KEYS, DEFAULT_REGISTRY, SHADOW_KEYS, UNCOVERED_KEYS,
)
from app.ai.Cognitive.system.v0.realization_v0 import RealizationCompileSkip
from app.ai.Cognitive.system.v0.realization_v0 import action_manifest as manifest
from app.ai.Cognitive.system.v0.realization_v0.action_manifest import (
    MANIFEST, PROFILES, AssertionPermission, entry_for, validate_manifest,
)
from app.ai.Cognitive.system.v0.realization_v0.validator import CONSTRAINT_ENFORCEMENT
from action_v0_support import documents, result_for
from realization_v0_support import case_inputs, compile_request

# Section 8.1: the 41 normative assignments, row for row (manifest v0.2 adds
# the two Collaborative Content and Inquiry V0 actions).
NORMATIVE_ASSIGNMENTS = {
    "provide_information.answer": "fact", "provide_information.explain": "fact",
    "provide_information.exemplify": "fact", "provide_information.correct": "correction",
    "provide_information.disclose_uncertainty": "uncertainty", "seek_information.clarify": "question",
    "seek_information.disambiguate": "question", "seek_information.verify": "question",
    "seek_information.explore_goal": "question", "seek_information.explore_preference": "question",
    "seek_information.request_evidence": "question", "seek_information.explore_reason": "question",
    "seek_information.explore_experience": "question", "seek_information.check_understanding": "question",
    "seek_information.request_feedback": "question", "give_feedback.acknowledge": "receipt",
    "give_feedback.signal_misunderstanding": "misunderstanding", "give_feedback.paraphrase": "paraphrase",
    "give_feedback.reflect": "reflection", "support_socially.reassure": "reassurance",
    "support_socially.validate_experience": "social_support", "support_socially.congratulate": "social_support",
    "support_socially.apologize": "apology", "advise_or_influence.advise": "advice",
    "commit_or_coordinate.offer": "coordination", "commit_or_coordinate.accept_request": "coordination",
    "commit_or_coordinate.decline_request": "coordination", "commit_or_coordinate.accept_suggestion": "coordination",
    "commit_or_coordinate.decline_suggestion": "coordination", "commit_or_coordinate.propose_plan": "coordination",
    "commit_or_coordinate.state_next_step": "coordination", "manage_dialogue.greet": "opening",
    "manage_dialogue.close": "closing", "manage_dialogue.introduce_topic": "dialogue",
    "manage_dialogue.repair": "repair", "manage_dialogue.return_to_topic": "dialogue",
    "manage_dialogue.self_correct": "correction", "social_play.joke": "play", "social_play.playful_reframe": "play",
    "collaborate.deliver_artifact": "collaboration", "seek_information.inquire": "inquiry",
}

# Section 8.1: the normative profile table.
NORMATIVE_PROFILES = {
    "fact": ("PG", 1, None, False, "grounded_only", "assertion", "neutral"),
    "correction": ("PDA", 2, None, False, "grounded_only", "correction", "neutral"),
    "uncertainty": ("PG", 1, None, False, "self_status_only", "uncertainty", "neutral"),
    "question": ("QGPD", 1, None, True, "attributed_only", "question", "question"),
    "receipt": ("DA", 1, 1, False, "none", "receipt", "warm"),
    "misunderstanding": ("GD", 1, 2, False, "self_status_only", "misunderstanding", "warm"),
    "paraphrase": ("PDA", 1, None, False, "attributed_only", "paraphrase", "neutral"),
    "reflection": ("PDA", 1, 2, False, "attributed_only", "reflection", "warm"),
    "social_support": ("PDA", 1, 2, False, "attributed_only", "social_act", "warm"),
    "reassurance": ("PD", 2, None, False, "grounded_only", "reassurance", "warm"),
    "apology": ("DP", 1, 2, False, "self_status_only", "apology", "warm"),
    "advice": ("PD", 1, None, False, "grounded_only", "advice", "neutral"),
    "coordination": ("PD", 1, None, False, "self_status_only", "coordination_act", "neutral"),
    "dialogue": ("DPA", 1, 2, False, "self_status_only", "dialogue_act", "neutral"),
    "opening": ("D", 0, 1, False, "none", "opening", "warm"),
    "closing": ("DP", 0, 1, False, "none", "closing", "warm"),
    "repair": ("DPAG", 1, None, False, "self_status_only", "repair", "warm"),
    "play": ("PDA", 1, 2, False, "attributed_only", "play_frame", "play"),
    "collaboration": ("WD", 1, None, False, "grounded_only", "artifact_delivery", "warm"),
    "inquiry": ("I", 1, 1, True, "none", "question", "question"),
}
SHORTHAND = {"P": "grounded_proposition", "Q": "grounded_question", "G": "knowledge_gap",
             "D": "dialogue_reference", "A": "authorized_quote", "W": "artifact_presentation",
             "I": "inquiry_prompt"}


def test_manifest_has_exact_parity_with_the_shadow_keys() -> None:
    assert manifest.MANIFEST_VERSION == "realization-action-manifest-v0.2"
    assert set(MANIFEST) == set(SHADOW_KEYS) == set(NORMATIVE_ASSIGNMENTS)
    assert len(MANIFEST) == 41


def test_every_assignment_and_profile_is_the_normative_row() -> None:
    for key, profile_name in NORMATIVE_ASSIGNMENTS.items():
        entry = MANIFEST[key]
        kinds, minimum, maximum, question, assertion, alignment, style = NORMATIVE_PROFILES[profile_name]
        assert entry.profile == profile_name
        assert {kind.value for kind in entry.allowed_content_kinds} == {SHORTHAND[c] for c in kinds}
        assert (entry.minimum_content_items, entry.maximum_content_items, entry.question_required) == (
            minimum, maximum, question)
        assert (entry.assertion_permission.value, entry.required_alignment_kind.value) == (assertion, alignment)
        assert entry.style_permissions == manifest.STYLE_PERMISSION_SETS[style]
        assert entry.mandatory_constraint_codes == PROFILES[profile_name].mandatory_constraint_codes
        assert entry.additional_constraint_codes, key


def test_every_definition_version_matches_and_nothing_else_is_realizable() -> None:
    for key, entry in MANIFEST.items():
        definition = DEFAULT_REGISTRY.by_key(key)
        assert definition.definition_ref.definition_version == entry.accepted_definition_version == 1
        assert entry_for(key, 1) is entry
        assert entry_for(key, 2) is None
    for key in BOUNDARY_ONLY_KEYS | UNCOVERED_KEYS:
        assert entry_for(key, 1) is None


def test_import_validation_fails_closed_on_drift() -> None:
    entries = dict(MANIFEST)
    with pytest.raises(RuntimeError, match="missing"):
        validate_manifest({key: value for key, value in entries.items() if key != "social_play.joke"}, SHADOW_KEYS)
    with pytest.raises(RuntimeError, match=r"unknown=\['express_stance.agree'\]"):
        validate_manifest({**entries, "express_stance.agree": entries["give_feedback.acknowledge"]}, SHADOW_KEYS)
    # A boundary-only (candidate-lifecycle) definition is never realizable, even if named as shadow.
    with pytest.raises(RuntimeError, match="not an installed shadow definition"):
        validate_manifest({**entries, "express_stance.agree": entries["give_feedback.acknowledge"]},
                          SHADOW_KEYS | {"express_stance.agree"})
    changed = entries["provide_information.answer"].model_copy(update={"accepted_definition_version": 2})
    with pytest.raises(RuntimeError, match="definition version"):
        validate_manifest({**entries, "provide_information.answer": changed}, SHADOW_KEYS)
    empty = entries["provide_information.answer"].model_copy(update={"minimum_content_items": 0,
                                                                      "required_bindings": ()})
    with pytest.raises(RuntimeError, match="zero authorized content"):
        validate_manifest({**entries, "provide_information.answer": empty}, SHADOW_KEYS)
    unbound = entries["seek_information.clarify"].model_copy(update={"required_bindings": ()})
    with pytest.raises(RuntimeError, match="question or unknown binding"):
        validate_manifest({**entries, "seek_information.clarify": unbound}, SHADOW_KEYS)


def test_assertion_actions_require_content_and_question_actions_an_unknown() -> None:
    for key, entry in MANIFEST.items():
        if entry.assertion_permission != AssertionPermission.NONE:
            assert entry.minimum_content_items >= 1 or any(item.minimum for item in entry.required_bindings), key
        if entry.question_required:
            primary = [item for item in entry.required_bindings if item.selector == "target:primary"]
            assert primary and all(
                {kind.value for kind in item.kinds} & {"grounded_question", "knowledge_gap", "dialogue_reference",
                                                       "inquiry_prompt"}
                or item.tentative for item in primary
            ), key
    # The only content-light exceptions are the reviewed opening and closing.
    assert {key for key, entry in MANIFEST.items() if entry.minimum_content_items == 0} == {
        "manage_dialogue.greet", "manage_dialogue.close"}


def test_every_manifest_constraint_is_enforced() -> None:
    for entry in MANIFEST.values():
        for code in (*entry.mandatory_constraint_codes, *entry.additional_constraint_codes):
            assert code in CONSTRAINT_ENFORCEMENT, code


def test_action_realization_constraints_survive_compilation() -> None:
    """Every Action ``realization_constraints`` item in every design case reaches the request unweakened."""

    seen = 0
    for case in documents()[1]["cases"]:
        try:
            result = result_for(case["case_id"])
        except Exception:  # noqa: BLE001 - failure-variant cases have no result
            continue
        if not result.plan.moves:
            continue
        compiled = compile_request(case_inputs(case["case_id"], ()))
        expected = {(item.code, item.subject_ref) for move in result.plan.moves for item in move.realization_constraints}
        if isinstance(compiled, RealizationCompileSkip):
            continue  # missing content: no request exists at all, so nothing was weakened
        found = {(item.code, item.subject_ref) for item in compiled.constraints if item.origin == "action"}
        assert expected <= found, case["case_id"]
        for move in compiled.action_plan.moves:
            original = next(item for item in result.plan.moves if item.move_id == move.move_ref)
            assert move.realization_constraints == original.realization_constraints
        seen += len(expected)
    assert seen > 0
