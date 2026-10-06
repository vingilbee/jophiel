"""Trusted answer-content producers (SH-EVAL-005).

For each producer class: authorized-known, unavailable, stale, unauthorized,
conflicting, corrected, and cross-user inputs.  Known content must permit an
answer; unavailable content must yield a grounded knowledge gap; unauthorized
content must remain inaccessible; unsupported queries remain explicit.
"""

from __future__ import annotations

import pathlib
import re
from datetime import datetime, timezone

import pytest

from action_v0_support import request_for
from app.ai.Cognitive.system.v0.action_selection_v0 import ActionSelectionEngine
from app.ai.Cognitive.system.v0.contracts.actions import ActionSelectionRequest
from app.ai.Cognitive.system.v0.contracts.realization import ContentSourceKind, GroundedProposition, KnowledgeGap
from app.ai.Cognitive.system.v0.producers_v0 import (
    QUERY_CAPABILITIES, ApplicationIdentityProducer, AuthorityClass, Availability, ProducerReason, ProducerRecord,
    ProducerRegistry, ProducerStatus, QueryKind, SemanticQuery, captured_runtime_producer, delivered_claim_producer,
    knowledge_gap_for, project_action_objects, project_realization_content, reviewed_knowledge_producer,
    tool_result_producer,
)
from app.ai.Cognitive.system.v0.realization_v0 import RealizationCompileSkip, RealizationCompilerV0
from realization_v0_support import inventory_for, reference, validate_text

TURN_TIME = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
USER, OTHER = "user-a", "user-b"


def _query(kind: str, *, user: str = USER, subject: str = "frame-1", arguments: dict | None = None,
           turn: str = "turn-producers") -> SemanticQuery:
    return SemanticQuery(query_id=f"query:{kind}:{user}", cognitive_turn_id=turn, user_ref=user, query_kind=kind,
                         subject_ref=subject, subject_anchor="that", arguments=arguments or {})


# (factory, query kind, arguments, admitted state, owner of user-scoped records)
CLASSES = {
    "captured_runtime": (captured_runtime_producer, QueryKind.CURRENT_DATE.value, {}, "captured"),
    "reviewed_knowledge": (reviewed_knowledge_producer, QueryKind.NUTRIENT_AMOUNT.value,
                           {"food": "egg", "nutrient": "protein"}, "approved"),
    "delivered_claim": (delivered_claim_producer, QueryKind.DELIVERED_CLAIM.value, {"claim_ref": "claim-1"}, "delivered"),
    "tool_result": (tool_result_producer, QueryKind.TOOL_OUTCOME.value, {"tool": "nutrition_image_estimation"}, "final"),
}


def _record(name: str, record_id: str, value: str, *, revision: str | None = None, **overrides) -> ProducerRecord:
    _, kind, arguments, state = CLASSES[name]
    values = dict(
        record_id=record_id, query_kind=kind, arguments=arguments, predicate="fact.value", value=value,
        value_type="string", value_anchor=value, owner="user", owner_ref=USER, authorized=True,
        admission_state=state, revision=revision or f"rev:{record_id}",
        observed_at="2026-09-28T11:59:00+00:00", max_age_seconds=3600,
    )
    values.update(overrides)
    return ProducerRecord(**values)


def _resolve(name: str, records, *, user: str = USER):
    factory, kind, arguments, _ = CLASSES[name]
    registry = ProducerRegistry([factory(records, turn_time=TURN_TIME)], allow_review_classes=True)
    return registry.resolve(_query(kind, user=user, arguments=arguments))


@pytest.mark.parametrize("name", sorted(CLASSES))
def test_authorized_known(name: str) -> None:
    result = _resolve(name, [_record(name, "r1", "six grams")])
    assert result.status == ProducerStatus.KNOWN and result.reason is None
    [fact] = result.facts
    assert fact.value == "six grams" and fact.owner_ref == USER and fact.revision == "rev:r1"
    assert fact.producer_id and fact.producer_version and fact.source_ref and fact.observed_at


@pytest.mark.parametrize("name", sorted(CLASSES))
def test_unavailable_is_explicit_and_never_negative_evidence(name: str) -> None:
    query_kind = CLASSES[name][1]
    result = _resolve(name, [])
    assert (result.status, result.reason) == (ProducerStatus.UNAVAILABLE, ProducerReason.NO_MATCHING_RECORD)
    gap = knowledge_gap_for(_query(query_kind, arguments=CLASSES[name][2]), result)
    assert gap is not None and gap.gap_kind.value == "no_grounded_answer"


@pytest.mark.parametrize("name", sorted(CLASSES))
def test_stale(name: str) -> None:
    result = _resolve(name, [_record(name, "r1", "six grams", observed_at="2026-09-27T00:00:00+00:00")])
    assert (result.status, result.reason) == (ProducerStatus.STALE, ProducerReason.STALE_OBSERVATION)
    expired = _resolve(name, [_record(name, "r2", "six grams", valid_until="2026-09-28T11:00:00+00:00")])
    assert expired.status == ProducerStatus.STALE


@pytest.mark.parametrize("name", sorted(CLASSES))
def test_unauthorized_stays_inaccessible(name: str) -> None:
    for record in (
        _record(name, "r1", "six grams", authorized=False),
        _record(name, "r2", "six grams", admission_state="pending"),
        _record(name, "r3", "six grams", sensitivity="restricted"),
    ):
        result = _resolve(name, [record])
        assert (result.status, result.reason) == (ProducerStatus.UNAUTHORIZED, ProducerReason.NOT_AUTHORIZED)
        assert result.facts == () and result.withheld_fact_refs
        query = _query(CLASSES[name][1], arguments=CLASSES[name][2])
        assert knowledge_gap_for(query, result) is None
        assert project_action_objects(query, result) == () and project_realization_content(query, result) == ()


@pytest.mark.parametrize("name", sorted(CLASSES))
def test_conflicting(name: str) -> None:
    result = _resolve(name, [_record(name, "r1", "six grams"), _record(name, "r2", "seven grams")])
    assert (result.status, result.reason) == (ProducerStatus.CONFLICTING, ProducerReason.CONFLICTING_VALUES)
    assert len(result.conflicting_fact_refs) == 2 and result.facts == ()


@pytest.mark.parametrize("name", sorted(CLASSES))
def test_corrected_revision_supersedes(name: str) -> None:
    result = _resolve(name, [
        _record(name, "r1", "seven grams", revision="rev-1"),
        _record(name, "r2", "six grams", revision="rev-2", supersedes_ref="rev-1"),
    ])
    assert result.status == ProducerStatus.KNOWN
    assert [fact.value for fact in result.facts] == ["six grams"]
    assert len(result.superseded_fact_refs) == 1


@pytest.mark.parametrize("name", sorted(CLASSES))
def test_cross_user_records_are_withheld(name: str) -> None:
    result = _resolve(name, [_record(name, "r1", "six grams", owner_ref=OTHER)])
    assert result.status == ProducerStatus.UNAUTHORIZED and result.facts == ()
    mixed = _resolve(name, [_record(name, "r1", "six grams"), _record(name, "r2", "nine grams", owner_ref=OTHER)])
    assert [fact.value for fact in mixed.facts] == ["six grams"] and len(mixed.withheld_fact_refs) == 1


@pytest.mark.parametrize("name", sorted(CLASSES))
def test_state_after_the_turn_boundary_does_not_exist(name: str) -> None:
    result = _resolve(name, [_record(name, "r1", "six grams", observed_at="2026-09-28T12:30:00+00:00")])
    assert result.status == ProducerStatus.UNAVAILABLE


def test_application_identity_is_reviewed_immutable_and_user_independent() -> None:
    producer = ApplicationIdentityProducer()
    registry = ProducerRegistry([producer])
    first = registry.resolve(_query(QueryKind.ASSISTANT_NAME.value, user=USER))
    second = registry.resolve(_query(QueryKind.ASSISTANT_NAME.value, user=OTHER))
    assert first.status == second.status == ProducerStatus.KNOWN
    assert first.facts[0].value == second.facts[0].value == "Columbina"
    fact = first.facts[0]
    assert fact.owner == "application" and fact.owner_ref is None
    assert fact.source_kind == ContentSourceKind.REVIEWED_SYSTEM_KNOWLEDGE and fact.observed_at is None
    # A producer answers only its declared queries.
    unsupported = producer.produce(_query("application_identity.favorite_color"))
    assert unsupported.status == ProducerStatus.UNSUPPORTED


def test_manifest_keeps_unsupported_queries_and_review_gates_explicit() -> None:
    assert set(QUERY_CAPABILITIES) == {kind.value for kind in QueryKind}
    installed = {item.authority_class for item in QUERY_CAPABILITIES.values() if item.availability == Availability.INSTALLED}
    assert installed == {AuthorityClass.APPLICATION_IDENTITY, AuthorityClass.CAPTURED_RUNTIME_FACT}
    registry = ProducerRegistry([ApplicationIdentityProducer()])
    unsupported = registry.resolve(_query("reviewed_knowledge.stock_price"))
    assert (unsupported.status, unsupported.reason) == (ProducerStatus.UNSUPPORTED, ProducerReason.QUERY_UNSUPPORTED)
    gated = registry.resolve(_query(QueryKind.NUTRIENT_AMOUNT.value, arguments={"food": "egg", "nutrient": "protein"}))
    assert (gated.status, gated.reason) == (ProducerStatus.UNAVAILABLE, ProducerReason.REQUIRES_REVIEW)
    missing = registry.resolve(_query(QueryKind.NUTRIENT_AMOUNT.value, arguments={"food": "egg"}))
    assert missing.reason == ProducerReason.MISSING_QUERY_ARGUMENT
    not_installed = ProducerRegistry([]).resolve(_query(QueryKind.ASSISTANT_NAME.value))
    assert not_installed.reason == ProducerReason.PRODUCER_NOT_INSTALLED
    # Even with records, a review-gated class stays off without an explicit opt-in.
    factory = CLASSES["reviewed_knowledge"][0]
    off = ProducerRegistry([factory([_record("reviewed_knowledge", "r1", "six grams")], turn_time=TURN_TIME)])
    assert off.resolve(_query(QueryKind.NUTRIENT_AMOUNT.value, arguments={"food": "egg", "nutrient": "protein"})
                       ).reason == ProducerReason.REQUIRES_REVIEW


# ---------------------------------------------------------------------------
# Projection: known content permits an answer; a gap is only "no grounded answer"
# ---------------------------------------------------------------------------

def _answer_request(objects) -> ActionSelectionRequest:
    """The reviewed answer case with its authored question and answer replaced by producer objects."""

    import json

    base = request_for("action-selection-prior-failure")
    question = next(item for item in objects if item.object_type.value == "question")
    payload = base.model_dump(mode="json")
    payload["grounded_objects"] = [
        item for item in payload["grounded_objects"] if item["object_id"] not in {"prop-result-10", "question-percentage"}
    ]
    # Every other record that cited the authored question now cites the projected one.
    payload = json.loads(json.dumps(payload).replace('"question-percentage"', json.dumps(question.object_id)))
    payload["grounded_objects"] = [item.model_dump(mode="json") for item in objects] + payload["grounded_objects"]
    return ActionSelectionRequest.model_validate(payload)


def _answer_move(result):
    return next(move for move in result.plan.moves if move.definition_ref.action_key == "provide_information.answer")


def test_known_producer_content_permits_a_grounded_answer() -> None:
    base = request_for("action-selection-prior-failure")
    query = _query(QueryKind.ASSISTANT_NAME.value, turn=base.cognitive_turn_id)
    result = ProducerRegistry([ApplicationIdentityProducer()]).resolve(query)
    objects = project_action_objects(query, result)
    selection = ActionSelectionEngine().select(_answer_request(objects))
    move = _answer_move(selection)
    assert move.move_id == selection.plan.primary_move_ref
    assert [target.ref for target in move.target_refs] == [f"producer-question:{query.query_id}"]
    [content] = project_realization_content(query, result)
    assert isinstance(content, GroundedProposition) and content.represents_refs == (objects[1].object_id,)
    assert content.source_kind == ContentSourceKind.REVIEWED_SYSTEM_KNOWLEDGE and content.authorized_for_realization


def test_unavailable_content_yields_a_bounded_knowledge_gap() -> None:
    base = request_for("action-selection-prior-failure")
    query = _query(QueryKind.NUTRIENT_AMOUNT.value, turn=base.cognitive_turn_id,
                   arguments={"food": "egg", "nutrient": "protein"})
    result = ProducerRegistry([ApplicationIdentityProducer()]).resolve(query)  # review-gated: unavailable
    gap = knowledge_gap_for(query, result)
    objects = project_action_objects(query, result, gap)
    assert [item.object_type.value for item in objects] == ["question", "evidence_record"]
    selection = ActionSelectionEngine().select(_answer_request(objects))
    move = _answer_move(selection)
    [content] = project_realization_content(query, result, gap)
    assert isinstance(content, KnowledgeGap) and content.represents_refs == (objects[1].object_id,)
    # The reviewed case's other moves keep their authored reference content.
    earlier_request = reference("content-earlier-request", ("interpretation-prior-request",), ("your earlier request",),
                                kind="request", source="current_user_statement", attribution="user",
                                status="unresolved")
    request = RealizationCompilerV0().compile(
        action_result=selection, inventory=inventory_for(selection, [earlier_request, content.model_dump(mode="json")]),
        personality=base.personality, state=base.dynamic_state, language_tag="en",
        current_user_utterance="How much protein is in an egg?", safety_basis=selection.safety,
        configured_maximum_characters=1200,
    )
    assert not isinstance(request, RealizationCompileSkip), request
    assert {"gap_yields_no_grounded_answer_only", "no_gap_answer"} <= {item.code for item in request.constraints}
    invented = validate_text(request, "Eggs have six grams of protein.", None, None)
    codes = {item.code.value for item in invented.violations}
    assert invented.status == "rejected" and {"knowledge_gap_answered", "unsupported_claim_detected"} <= codes
    assert move.target_refs[0].ref == f"producer-question:{query.query_id}"


def test_producers_import_no_provider_database_or_agent() -> None:
    root = pathlib.Path(__file__).resolve().parents[1] / "app/ai/Cognitive/system/v0/producers_v0"
    forbidden = re.compile(r"\b(openai|langchain|anthropic|sqlalchemy|app\.services|app\.db|fixture)\b")
    for path in root.glob("*.py"):
        imports = "\n".join(line for line in path.read_text().splitlines() if line.startswith(("import ", "from ")))
        assert not forbidden.search(imports), path.name
