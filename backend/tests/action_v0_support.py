"""Shared, cached access to the schema-v2 action design inputs for tests."""

from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any

from app.ai.Cognitive.system.v0.action_selection_v0 import fixture_adapter as adapter
from app.ai.Cognitive.system.v0.action_selection_v0.reference_index import ActionReferenceIndex
from app.ai.Cognitive.system.v0.contracts.actions import ActionSelectionRequest, ActionSelectionResult


@lru_cache(maxsize=1)
def documents() -> tuple[dict, dict]:
    return adapter.load_documents()


def canonical_case(case_id: str) -> dict[str, Any]:
    return copy.deepcopy(next(item for item in documents()[1]["cases"] if item["case_id"] == case_id))


def fixture_case(case_id: str) -> dict[str, Any]:
    return copy.deepcopy(next(item for item in documents()[0]["cases"] if item["case_id"] == case_id))


@lru_cache(maxsize=None)
def request_for(case_id: str) -> ActionSelectionRequest:
    return adapter.build_request(canonical_case(case_id))


@lru_cache(maxsize=None)
def result_for(case_id: str) -> ActionSelectionResult:
    return adapter.run_case(canonical_case(case_id))


def index_for(case_id: str) -> ActionReferenceIndex:
    return ActionReferenceIndex(request_for(case_id))


def action_keys(result: ActionSelectionResult) -> list[str]:
    return [move.definition_ref.action_key for move in result.plan.moves]


def candidate_by_key(step, action_key: str):
    return [item for item in step.constructed_candidates if item.definition_ref.action_key == action_key]


def gate_for(step, candidate_id: str):
    return next(item for item in step.gate_results if item.candidate_ref == candidate_id)


def scored_for(step, candidate_id: str):
    return next(item for item in step.scored_candidates if item.candidate_ref == candidate_id)


def mutate_inputs(case_id: str, path: tuple, value: Any) -> dict[str, Any]:
    case = canonical_case(case_id)
    target = case["canonical_inputs"]
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    return case
