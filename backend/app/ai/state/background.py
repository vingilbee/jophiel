"""Replaceable infrastructure contracts. No cognition or learning algorithm."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AdapterPolicy:
    component: str
    event_types: tuple[str, ...]
    input_versions: tuple[str, ...]
    scope: str
    concurrency_key: str
    timeout_seconds: int
    max_attempts: int
    result_type: str
    cadence_seconds: float | None = None


POLICIES = {
    "mind": AdapterPolicy("mind", ("thinking_requested",), ("evidence", "conversation"),
                          "conversation/activity", "mind:user", 180, 3, "private_thought"),
    "recall": AdapterPolicy("recall", ("recall_requested",), ("evidence",),
                            "conversation", "recall:user", 180, 3, "memory_result"),
    "memory": AdapterPolicy("memory", ("source_report", "maintenance_due"), ("due", "receipt"),
                            "user", "memory:user", 180, 3, "maintenance_receipt", 1),
    "observation": AdapterPolicy("observation", ("exchange_settled", "experience", "delivery_changed"),
                                 ("source", "delivery"), "conversation/activity", "observation:user",
                                 180, 3, "learning_preparation"),
}


@dataclass(frozen=True)
class PreparationInput:
    event_id: str
    user_id: str
    conversation_id: str | None
    activity_id: str | None
    source_refs: tuple[str, ...]
    input_versions: dict[str, str]
    delivery: dict  # recorded/partial/cancelled and heard extent, never assumed delivered
    correlation_id: str
    created_at: str
    schema_version: int = 1


@dataclass(frozen=True)
class PreparationResult:
    output_refs: tuple[str, ...] = ()
    processing_version: str = "unavailable"
    coverage: tuple[str, ...] = ()
    unavailable: str | None = None
    schema_version: int = 1


class PreparationAdapter(Protocol):
    def __call__(self, evidence: PreparationInput) -> PreparationResult: ...


@dataclass(frozen=True)
class CognitionEvent:
    event_id: str
    user_id: str
    conversation_id: str | None
    activity_id: str | None
    kind: str
    source_refs: tuple[str, ...]
    input_versions: dict[str, str]
    causation_id: str
    correlation_id: str
    created_at: str
    schema_version: int = 1
    requires_relevance_check: bool = True


@dataclass(frozen=True)
class SinkReceipt:
    status: str  # consumed / blocked; never a speech command
    reason: str | None = None
    schema_version: int = 1


class CognitionSink(Protocol):
    def __call__(self, event: CognitionEvent) -> SinkReceipt: ...
