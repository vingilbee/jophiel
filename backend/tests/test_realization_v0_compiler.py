"""Realization V0 deterministic compiler tests (section 19.4)."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.ai.Cognitive.system.v0.contracts.realization import (
    RealizationRequestV0, RealizationStatus, canonical_json,
)
from app.ai.Cognitive.system.v0.realization_v0 import (
    RealizationCompileSkip, RealizationCompilerV0, RealizationExecutionError, canonical_payload_json, project_payload,
)
from app.ai.Cognitive.system.v0.realization_v0 import compiler as compiler_module
from realization_v0_support import (
    case_inputs, compile_request, decimal_arg, entity_arg, gap, inventory_for, live_inputs, proposition, reference,
)

BACKEND = Path(__file__).resolve().parents[1]
SEMANTIC_CASE = "action-selection-prior-failure"
REQUEST_REF = reference("content-earlier-request", ("interpretation-prior-request",), ("your earlier request",),
                        kind="request", source="current_user_statement", attribution="user", status="unresolved")
RESULT = proposition("content-result", ("prop-result-10",), "the result", predicate="measurement.percentage",
                     arguments=(decimal_arg("10.000000", "percent"),), source="authorized_semantic_memory")

# Golden identity of the compiled request for (SEMANTIC_CASE, [REQUEST_REF, RESULT]) under the
# installed Action V0 and Realization V0 versions.  A change here is a reviewed version change.
# Updated for realization contract/compiler/model protocol v0.2: the exact
# current utterance is now hashed into the identity and carried as explicitly
# non-authoritative linguistic context.
# Updated for goal-contract/lifecycle/formation v0.2 + goal-engine-v0.2 and action-contract/features/
# planner v0.2 (SH-EVAL-001/002/003/006): restoring exactly those version strings reproduces the
# previous golden (5aa5b953-..., a6b34a68..., 6c6bef50...) byte for byte; ids change only because the
# Action request digest records the new versions.
# Updated for goal-contract/lifecycle/formation v0.3, goal-priority/satisfaction v0.2, goal-engine-v0.3,
# and the new goal adoption/evidence policy versions (Goal V0 remediation, SH-EVAL-013/014): restoring
# exactly those version strings reproduces the previous golden (3204e18d-..., c48263aa..., b0599888...)
# byte for byte; ids change only because the Action request digest records the new versions.
# Updated for goal-ontology v0.2, goal-formation v0.4, goal-priority/satisfaction v0.3, goal-adoption/
# evidence policy v0.2, goal-engine-v0.4, and action-contract/features/planner v0.3 (typed requirement
# resolution): restoring exactly those version strings reproduces the previous golden (d7653fcb-...,
# a311daff..., bbf7ce9c...) byte for byte; ids change only because the Action request digest records the
# new versions.
# Updated for Collaborative Content and Inquiry V0 (goal-ontology v0.3, goal-formation v0.5, goal-priority/
# satisfaction v0.4, goal-adoption/evidence policy v0.3, goal-engine-v0.5, action-contract/features/planner
# v0.4, action-construction v0.2, registry conversational-actions-v0.2-shadow, requirement-resolution
# engine/manifest/contract/projection v0.2, and the realization contract/content/compiler/protocol/validator
# v0.3 and action manifest v0.2): restoring exactly those version strings reproduces the previous golden
# (21532f63-..., 8ca0bc2b..., 3c79d226...) byte for byte; ids change only because the request records the
# new versions.
GOLDEN_REQUEST_ID = "84811005-ea1c-5dc0-83f9-63c8e7f2eb88"
GOLDEN_REQUEST_DIGEST = "1996a162b7cdfaa864596c6066befa75168dfc32885c0d98137f54d2c8ed417e"
GOLDEN_JSON_SHA256 = "bcc908d058c1ec587efbf101519ab8149f97268d8a1843696b076e37b2eff0ee"


def _request() -> RealizationRequestV0:
    return compile_request(case_inputs(SEMANTIC_CASE, [REQUEST_REF, RESULT]))


def test_golden_request_has_deterministic_identity_and_byte_equivalent_json() -> None:
    first, second = _request(), _request()
    assert (first.request_id, first.request_digest) == (GOLDEN_REQUEST_ID, GOLDEN_REQUEST_DIGEST)
    assert first.model_dump_json() == second.model_dump_json()
    assert hashlib.sha256(first.model_dump_json().encode()).hexdigest() == GOLDEN_JSON_SHA256
    assert canonical_json(first.model_dump(mode="json")) == canonical_json(second.model_dump(mode="json"))


def test_current_utterance_is_exact_and_part_of_request_identity() -> None:
    inputs = case_inputs(SEMANTIC_CASE, [REQUEST_REF, RESULT])
    first = compile_request(inputs)
    second = compile_request({**inputs, "current_user_utterance": "A different exact utterance."})
    assert first.linguistic_context.text == inputs["current_user_utterance"]
    assert first.request_id != second.request_id
    assert first.request_digest != second.request_digest


def test_compilation_is_deterministic_across_processes_and_hash_seeds() -> None:
    probe = (
        "import sys; sys.path.insert(0, 'tests');"
        "from test_realization_v0_compiler import _request; r = _request();"
        "import hashlib; print(r.request_id, r.request_digest, hashlib.sha256(r.model_dump_json().encode()).hexdigest())"
    )
    outputs = {
        subprocess.run([sys.executable, "-c", probe], cwd=BACKEND, capture_output=True, text=True, check=True,
                       env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(BACKEND)}).stdout.split()[-3:]
        .__str__() for seed in ("0", "1", "4242")
    }
    assert outputs == {str([GOLDEN_REQUEST_ID, GOLDEN_REQUEST_DIGEST, GOLDEN_JSON_SHA256])}


def test_every_selected_move_binds_exactly_once_in_order() -> None:
    inputs = case_inputs(SEMANTIC_CASE, [REQUEST_REF, RESULT])
    request = compile_request(inputs)
    moves = inputs["action_result"].plan.moves
    assert tuple(item.move_ref for item in request.move_bindings) == tuple(move.move_id for move in moves)
    assert tuple(item.move_ref for item in request.action_plan.moves) == tuple(move.move_id for move in moves)
    assert request.discourse_controls.move_order == tuple(move.move_id for move in moves)
    assert all(binding.required is True for binding in request.move_bindings)
    for spec, move in zip(request.action_plan.moves, moves):
        assert (spec.target_refs, dict(spec.parameters), spec.realization_constraints, spec.definition_version) == (
            move.target_refs, dict(move.parameters), move.realization_constraints, move.definition_ref.definition_version)
    assert request.action_plan.primary_move_ref == inputs["action_result"].plan.primary_move_ref


def test_missing_content_is_a_typed_skip_never_neutral_content() -> None:
    skipped = compile_request(case_inputs(SEMANTIC_CASE, [REQUEST_REF]))
    assert isinstance(skipped, RealizationCompileSkip)
    assert skipped.status == RealizationStatus.SKIPPED_MISSING_CONTENT
    assert skipped.skip.code == "required_content_missing"
    assert all(ref.startswith("move-") for ref in skipped.skip.subject_refs)
    # Generic dialogue structure never satisfies a content-bearing move.
    assert isinstance(compile_request(case_inputs("validate-without-factual-endorsement", ())), RealizationCompileSkip)


def test_knowledge_gap_cannot_bind_as_an_answer_proposition() -> None:
    items = [proposition("content-estimate", ("frame-1",), "the estimate", source="trusted_runtime_observation",
                         attribution="runtime"),
             gap("content-cause-gap", ("prop-estimate-cause",), "the cause")]
    # explain requires a proposition as its basis; a gap cannot stand in for it.
    skipped = compile_request(case_inputs("four-move-plan-order-contrast", items))
    assert isinstance(skipped, RealizationCompileSkip)
    assert any(ref.endswith("#evidence:basis") for ref in skipped.skip.subject_refs)
    request = compile_request(case_inputs(SEMANTIC_CASE, [REQUEST_REF, gap("content-gap", ("prop-result-10",),
                                                                        "the percentage")]))
    gap_item = request.content("content-gap")
    assert gap_item.kind == "knowledge_gap"
    codes = {(item.code, item.subject_ref) for item in request.constraints}
    assert ("no_gap_answer", "content-gap") in codes


def test_user_assertions_retain_attribution() -> None:
    request = _request()
    item = request.content("content-earlier-request")
    assert (item.source_kind, item.attribution) == ("current_user_statement", "user")
    assert ("preserve_attribution", "content-earlier-request") in {(c.code, c.subject_ref) for c in request.constraints}
    payload = json.loads(canonical_payload_json(project_payload(request)))
    projected = next(entry for entry in payload["content_items"] if entry["content_id"] == "content-earlier-request")
    assert projected["attribution"] == "user"


def test_numbers_units_dates_polarity_modality_and_uncertainty_survive_exactly() -> None:
    items = [REQUEST_REF, proposition(
        "content-result", ("prop-result-10",), "the result", predicate="measurement.percentage",
        arguments=(decimal_arg("12.600000", "gram"), {"role": "when", "value_type": "date", "value": "2026-06-05"},
                   entity_arg("maya", "Maya", role="who")),
        polarity="negative", modality="conditional", certainty="approximate", source="authorized_semantic_memory",
    )]
    inputs = case_inputs(SEMANTIC_CASE, items)
    request = compile_request(inputs)
    original = next(item for item in inputs["inventory"].items if item.content_id == "content-result")
    assert request.content("content-result") == original
    payload = json.loads(canonical_payload_json(project_payload(request)))
    projected = next(entry for entry in payload["content_items"] if entry["content_id"] == "content-result")
    assert projected["arguments"] == [
        {"role": "value", "value_type": "decimal", "value": "12.600000", "unit": "gram", "surface_anchor": None},
        {"role": "when", "value_type": "date", "value": "2026-06-05", "unit": None, "surface_anchor": None},
        {"role": "who", "value_type": "entity", "value": "maya", "unit": None, "surface_anchor": "Maya"},
    ]
    assert (projected["polarity"], projected["modality"], projected["certainty"]) == (
        "negative", "conditional", "approximate")
    codes = {item.code for item in request.constraints if item.subject_ref == "content-result"}
    assert {"preserve_polarity", "preserve_modality", "uncertainty_marker_required", "preserve_numeric_value"} <= codes


@pytest.mark.parametrize(("field", "value", "code"), [
    ("authorized_for_realization", False, "content_unauthorized"),
    ("cognitive_turn_id", "some-other-turn", "content_cross_turn"),
    ("status", "superseded", "content_not_current"),
    ("status", "deleted", "content_not_current"),
    ("sensitivity_class", "sensitive", "content_sensitivity_not_permitted"),
    ("sensitivity_class", "restricted", "content_sensitivity_not_permitted"),
])
def test_unauthorized_stale_cross_turn_or_sensitive_content_fails_closed(field, value, code) -> None:
    with pytest.raises(RealizationExecutionError) as caught:
        compile_request(case_inputs(SEMANTIC_CASE, [REQUEST_REF, {**RESULT, field: value}]))
    assert caught.value.code == code and caught.value.phase == "content_resolution"
    assert "10" not in caught.value.safe_message and "result" not in caught.value.safe_message


def test_unbound_unauthorized_content_is_never_sent() -> None:
    stray = {**proposition("zz-stray", ("unrelated-ref",), "a secret"), "authorized_for_realization": False}
    request = compile_request(case_inputs(SEMANTIC_CASE, [REQUEST_REF, RESULT, stray]))
    assert "zz-stray" not in {item.content_id for item in request.content_items}
    assert "a secret" not in canonical_payload_json(project_payload(request))


def test_non_assertable_content_is_bound_only_as_the_corrected_side() -> None:
    with pytest.raises(RealizationExecutionError) as caught:
        compile_request(case_inputs(SEMANTIC_CASE, [REQUEST_REF, {**RESULT, "epistemic_status": "corrected"}]))
    assert caught.value.code == "non_assertable_content_bound"


def test_content_cardinality_above_the_profile_maximum_fails_closed() -> None:
    extra = reference("content-user-act", ("hyp-1",), ("your message",), kind="user_contribution",
                      source="current_user_statement", attribution="user")
    with pytest.raises(RealizationExecutionError) as caught:
        compile_request(case_inputs("acknowledge-without-agreement", [extra]))  # generic + anchored > receipt max 1
    assert caught.value.code == "content_cardinality_exceeded"


def test_unsupported_action_is_a_typed_skip(monkeypatch) -> None:
    real = compiler_module.entry_for
    monkeypatch.setattr(compiler_module, "entry_for",
                        lambda key, version: None if key == "give_feedback.acknowledge" else real(key, version))
    skipped = compile_request(case_inputs("acknowledge-without-agreement", ()))
    assert isinstance(skipped, RealizationCompileSkip)
    assert skipped.status == RealizationStatus.SKIPPED_UNSUPPORTED_ACTION
    assert skipped.skip.code == "action_not_in_realization_manifest"


def test_lineage_and_ownership_are_validated() -> None:
    base = case_inputs(SEMANTIC_CASE, [REQUEST_REF, RESULT])
    other = live_inputs("hi")
    for overrides, code in (
        ({"language_tag": "fr"}, "language_unsupported"),
        ({"safety_basis": other["safety_basis"]}, "safety_basis_mismatch"),
        ({"state": other["state"]}, "state_lineage_mismatch"),
        ({"inventory": other["inventory"]}, "inventory_turn_mismatch"),
    ):
        with pytest.raises(RealizationExecutionError) as caught:
            compile_request({**base, **overrides})
        assert caught.value.code == code
    with pytest.raises(RealizationExecutionError) as caught:
        RealizationCompilerV0().compile(**base, configured_maximum_characters=0)
    assert caught.value.code == "maximum_characters_invalid"


def test_no_input_object_is_mutated() -> None:
    inputs = case_inputs(SEMANTIC_CASE, [REQUEST_REF, RESULT])
    before = {key: copy.deepcopy(value.model_dump(mode="json") if hasattr(value, "model_dump") else value)
              for key, value in inputs.items()}
    compile_request(inputs)
    after = {key: value.model_dump(mode="json") if hasattr(value, "model_dump") else value for key, value in inputs.items()}
    assert before == after


def test_generic_structure_lets_only_content_light_moves_realize() -> None:
    # Live greeting, acknowledgement, closing, and clarification compile with no grounded producer.
    for text in ("hi", "thanks, got it", "bye for now"):
        assert isinstance(compile_request(live_inputs(text)), RealizationRequestV0), text
    clarify = compile_request(case_inputs("missing-recipient-open-clarification", ()))
    assert isinstance(clarify, RealizationRequestV0)
    assert [item.reference_form for item in clarify.content_items] == ["generic"]
    # The generic projection alone never supports a content-bearing move.
    empty = inventory_for(case_inputs(SEMANTIC_CASE, ())["action_result"], (), generic=True)
    assert all(item.reference_form == "generic" for item in empty.items)


def test_compiler_is_pure_and_imports_no_provider_database_or_agent() -> None:
    probe = ("import sys, app.ai.Cognitive.system.v0.realization_v0 as r, "
             "app.ai.Cognitive.system.v0.contracts.realization as c; print('\\n'.join(sorted(sys.modules)))")
    loaded = set(subprocess.run([sys.executable, "-c", probe], cwd=BACKEND, capture_output=True, text=True, check=True,
                                env={**os.environ, "PYTHONPATH": str(BACKEND)}).stdout.split())
    forbidden = ("openai", "anthropic", "sqlalchemy", "app.db", "app.services", "app.ai.chat", "app.ai.agent",
                 "app.ai.prompts", "app.ai.tools", "langchain", "langchain_openai", "langgraph", "redis", "httpx",
                 "app.ai.Cognitive.system.v0.realization_provider")
    assert sorted(name for name in loaded for prefix in forbidden if name == prefix or name.startswith(prefix + ".")) == []
    import re

    source = (BACKEND / "app/ai/Cognitive/system/v0/realization_v0/compiler.py").read_text()
    imports = "\n".join(line for line in source.splitlines() if re.match(r"\s*(import|from)\s", line))
    for module in ("time", "datetime", "random", "secrets", "os", "uuid"):
        assert not re.search(rf"\b(import|from)\s+{module}\b", imports), module
    for call in ("uuid4(", "time.time(", "perf_counter(", "datetime.now(", "random."):
        assert call not in source
