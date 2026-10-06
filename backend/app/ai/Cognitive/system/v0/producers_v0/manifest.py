"""The query-to-producer capability manifest (``query-producer-manifest-v0.1``).

Every semantic query kind a producer may answer is declared here with its
authority class, its installation status, and the arguments it requires.  A
query kind absent from this manifest is *unsupported* and stays explicit;
nothing falls back to model knowledge.

``REQUIRES_REVIEW`` classes have a working producer protocol but no source is
approved for live use: each needs privacy and authorization review before a
source is installed (SH-EVAL-005).  They can be exercised offline only by an
explicit opt-in on the registry.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from .contracts import AuthorityClass

QUERY_MANIFEST_VERSION = "query-producer-manifest-v0.1"


class Availability(StrEnum):
    INSTALLED = "installed"
    REQUIRES_REVIEW = "requires_review"


class QueryKind(StrEnum):
    ASSISTANT_NAME = "application_identity.assistant_name"
    CURRENT_DATE = "runtime.current_date"
    NUTRIENT_AMOUNT = "reviewed_knowledge.nutrient_amount"
    DELIVERED_CLAIM = "dialogue.delivered_claim"
    TOOL_OUTCOME = "tool.finalized_outcome"


@dataclass(frozen=True)
class QueryCapability:
    query_kind: QueryKind
    authority_class: AuthorityClass
    availability: Availability
    required_arguments: tuple[str, ...] = ()


QUERY_CAPABILITIES: Mapping[str, QueryCapability] = {
    item.query_kind.value: item for item in (
        QueryCapability(QueryKind.ASSISTANT_NAME, AuthorityClass.APPLICATION_IDENTITY, Availability.INSTALLED),
        QueryCapability(QueryKind.CURRENT_DATE, AuthorityClass.CAPTURED_RUNTIME_FACT, Availability.INSTALLED),
        QueryCapability(QueryKind.NUTRIENT_AMOUNT, AuthorityClass.REVIEWED_KNOWLEDGE, Availability.REQUIRES_REVIEW,
                        ("food", "nutrient")),
        QueryCapability(QueryKind.DELIVERED_CLAIM, AuthorityClass.DELIVERED_DIALOGUE_CLAIM,
                        Availability.REQUIRES_REVIEW, ("claim_ref",)),
        QueryCapability(QueryKind.TOOL_OUTCOME, AuthorityClass.AUTHORIZED_TOOL_RESULT, Availability.REQUIRES_REVIEW,
                        ("tool",)),
    )
}
if len(QUERY_CAPABILITIES) != len(QueryKind):
    raise RuntimeError("every query kind has exactly one declared capability")


def capability_for(query_kind: str) -> QueryCapability | None:
    return QUERY_CAPABILITIES.get(query_kind)


__all__ = ["Availability", "QUERY_CAPABILITIES", "QUERY_MANIFEST_VERSION", "QueryCapability", "QueryKind",
           "capability_for"]
