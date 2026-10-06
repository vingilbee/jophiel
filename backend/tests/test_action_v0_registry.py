"""The typed 77-definition registry and its accepted lifecycle split."""

from __future__ import annotations

import re
from pathlib import Path

from app.ai.Cognitive.system.v0.action_selection_v0 import registry as registry_module
from app.ai.Cognitive.system.v0.action_selection_v0.registry import (
    BOUNDARY_ONLY_KEYS, DEFINITIONS, SHADOW_KEYS, UNCOVERED_KEYS, ActionRegistry,
)
from app.ai.Cognitive.system.v0.contracts.actions import (
    ActionDefinitionRef, ActionFamily, ActionLifecycle, BindingMode, FeatureKey,
)

SYSTEM = Path(registry_module.__file__).resolve().parent.parent
REGISTRY_DOC = SYSTEM / "data" / "CONVERSATIONAL_ACTION_REGISTRY_V0.md"
DESIGN_DOC = SYSTEM / "ACTION_SELECTION_V0_DESIGN.md"


COLLABORATION_KEYS = frozenset({"collaborate.deliver_artifact", "seek_information.inquire"})


def _code_block_after(text: str, marker: str) -> set[str]:
    start = text.index(marker)
    block = text[text.index("```text", start) + len("```text"): text.index("```", text.index("```text", start) + 7)]
    return {line.strip() for line in block.splitlines() if line.strip()}


def test_exact_registry_size_and_lifecycle_split() -> None:
    keys = [item.definition_ref.action_key for item in DEFINITIONS]
    assert len(keys) == len(set(keys)) == 77
    assert (len(SHADOW_KEYS), len(BOUNDARY_ONLY_KEYS), len(UNCOVERED_KEYS)) == (41, 12, 24)
    assert COLLABORATION_KEYS <= SHADOW_KEYS
    assert SHADOW_KEYS | BOUNDARY_ONLY_KEYS | UNCOVERED_KEYS == set(keys)
    assert not (SHADOW_KEYS & BOUNDARY_ONLY_KEYS or SHADOW_KEYS & UNCOVERED_KEYS or BOUNDARY_ONLY_KEYS & UNCOVERED_KEYS)
    lifecycle = {item.definition_ref.action_key: item.lifecycle for item in DEFINITIONS}
    assert {key for key, value in lifecycle.items() if value == ActionLifecycle.SHADOW} == SHADOW_KEYS
    assert {key for key, value in lifecycle.items() if value == ActionLifecycle.CANDIDATE} == BOUNDARY_ONLY_KEYS | UNCOVERED_KEYS
    assert not any(value == ActionLifecycle.APPROVED for value in lifecycle.values())


def test_lifecycle_sets_match_the_normative_design_documents() -> None:
    design = DESIGN_DOC.read_text(encoding="utf-8")
    assert _code_block_after(design, "## 7. Initial shadow-selectable subset") == SHADOW_KEYS - COLLABORATION_KEYS
    assert _code_block_after(design, "Registry v0.2 (Collaborative Content and Inquiry V0)") == COLLABORATION_KEYS
    assert _code_block_after(design, "These 12 keys occur only as a disfavored") == BOUNDARY_ONLY_KEYS
    doc = REGISTRY_DOC.read_text(encoding="utf-8")
    assert _code_block_after(doc, "### Uncovered candidates: 24") == UNCOVERED_KEYS
    assert _code_block_after(doc, "### Case-referenced candidates: 51") == (SHADOW_KEYS | BOUNDARY_ONLY_KEYS) - COLLABORATION_KEYS
    assert _code_block_after(doc, "### Collaborative Content V0 additions: 2") == COLLABORATION_KEYS


def test_ten_families_versions_and_parents() -> None:
    assert {item.family for item in DEFINITIONS} == set(ActionFamily) and len(ActionFamily) == 10
    assert all(item.definition_ref.definition_version == 1 for item in DEFINITIONS)
    registry = ActionRegistry()
    for item in DEFINITIONS:
        assert item.definition_ref.action_key == f"{item.family.value}.{item.function}"
        if item.parent_action_ref is not None:
            assert registry.get(item.parent_action_ref) is not None
        assert registry.resolve(item.definition_ref) is item
    assert registry.get(ActionDefinitionRef(action_key="seek_information.clarify", definition_version=2)) is None


def test_every_definition_has_one_planned_effect_and_full_feature_relations() -> None:
    for item in DEFINITIONS:
        assert len(item.intended_effects) == 1
        effect = item.effect
        assert effect.actual_outcome_claim is False
        assert effect.effect_strength == "1.000000"
        assert effect.planned_state.endswith("_planned")
        assert item.policy_feature_relations == tuple(FeatureKey)
        assert set(item.hard_inhibitors) >= {
            "registry_lifecycle", "schema_reference_integrity", "truth_evidence", "capability",
            "authorization_consent_privacy", "application_safety",
        }
        declared = {kind for kinds in effect.target_roles.values() for kind in kinds}
        if effect.binding_mode != BindingMode.DIALOGUE_SCOPE:
            assert set(item.target_schema.accepted_types) <= declared


def test_effect_manifest_parity_with_the_registry_document() -> None:
    doc = REGISTRY_DOC.read_text(encoding="utf-8")
    section = doc[doc.index("### V0 transition-binding manifest"): doc.index("`STOP` has no entry in this manifest")]
    rows = re.findall(r"^\| `([a-z_]+\.[a-z_]+)` \| `([a-z_.]+)` \| `([a-z_]+)` / `([a-z_]+)` \| `([a-z_]+)`", section, re.MULTILINE)
    assert len(rows) == 77
    by_key = {item.definition_ref.action_key: item for item in DEFINITIONS}
    for key, effect_key, domain, planned_state, binding in rows:
        effect = by_key[key].effect
        assert (effect.effect_key, effect.transition_domain.value, effect.planned_state, effect.binding_mode.value) == (
            effect_key, domain, planned_state, binding,
        ), key


def test_question_family_schema_and_conditional_requirements() -> None:
    by_key = {item.definition_ref.action_key: item for item in DEFINITIONS}
    for key, item in by_key.items():
        if item.family != ActionFamily.SEEK_INFORMATION:
            continue
        form = item.parameter_rule("question_form")
        assert form is not None and form.required
        if key != "seek_information.check_understanding":
            assert dict(item.parameter_rule("candidate_answer_refs").required_when) == {"question_form": "choice"}
        if key not in {"seek_information.check_understanding", "seek_information.inquire"}:
            assert dict(item.parameter_rule("scale_min").required_when) == {"question_form": "scale"}
    inquire = by_key["seek_information.inquire"]
    assert inquire.parameter_rule("question_form").allowed_values == ("open_set", "yes_no", "choice")
    assert inquire.parameter_rule("inquiry_timing").required and inquire.parameter_rule("inquiry_criticality").required
    assert by_key["seek_information.check_understanding"].parameter_rule("question_form").allowed_values == ("check",)
    advise = by_key["advise_or_influence.advise"].parameter_rule("guidance_strength")
    assert advise.allowed_values == ("neutral_option", "low", "moderate", "high") and advise.required


def test_shared_signal_candidate_is_not_a_registry_entry() -> None:
    keys = {item.definition_ref.action_key for item in DEFINITIONS}
    assert not any("shared_signal" in key for key in keys)
    assert "candidate.manage_dialogue.establish_shared_signal" not in keys


def test_production_registry_and_construction_never_read_design_data() -> None:
    package = SYSTEM / "action_selection_v0"
    for name in ("registry.py", "construction.py", "gates.py", "features.py", "policy.py", "transition.py", "engine.py", "reference_index.py"):
        source = (package / name).read_text(encoding="utf-8")
        for construct in ("open(", "read_text", "read_bytes", "json.load", "import pathlib", "from pathlib", "fixture_adapter"):
            assert construct not in source, (name, construct)
