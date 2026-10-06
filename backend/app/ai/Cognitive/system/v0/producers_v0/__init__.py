"""Trusted answer-content producers (SH-EVAL-005): contracts, manifest, producers, projections.

Pure and independently testable.  Nothing here imports a model provider, the
database, the chat agent, or a fixture.  Classes whose manifest availability
is ``requires_review`` need privacy and authorization review before any
source is installed for live use.
"""

from .contracts import (
    PRODUCER_CONTRACT_VERSION, AuthorityClass, KnowledgeGapRecord, ProducedFact, ProducerReason, ProducerResult,
    ProducerStatus, SemanticQuery,
)
from .manifest import QUERY_CAPABILITIES, QUERY_MANIFEST_VERSION, Availability, QueryCapability, QueryKind, capability_for
from .producers import (
    APPLICATION_IDENTITY_VERSION, ApplicationIdentityProducer, ProducerRecord, ProducerRegistry, RecordBackedProducer,
    TrustedProducer, captured_runtime_producer, delivered_claim_producer, knowledge_gap_for,
    reviewed_knowledge_producer, tool_result_producer,
)
from .projection import project_action_objects, project_realization_content

__all__ = [
    "APPLICATION_IDENTITY_VERSION", "ApplicationIdentityProducer", "AuthorityClass", "Availability",
    "KnowledgeGapRecord", "PRODUCER_CONTRACT_VERSION", "ProducedFact", "ProducerReason", "ProducerRecord",
    "ProducerRegistry", "ProducerResult", "ProducerStatus", "QUERY_CAPABILITIES", "QUERY_MANIFEST_VERSION",
    "QueryCapability", "QueryKind", "RecordBackedProducer", "SemanticQuery", "TrustedProducer", "capability_for",
    "captured_runtime_producer", "delivered_claim_producer", "knowledge_gap_for", "project_action_objects",
    "project_realization_content", "reviewed_knowledge_producer", "tool_result_producer",
]
