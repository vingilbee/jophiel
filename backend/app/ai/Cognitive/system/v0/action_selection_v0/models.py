"""Action-stage internal value types.

These are engine-internal views over the canonical request.  Cross-stage
contracts live exclusively in ``contracts/actions.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from ..contracts.actions import ActionTargetType, ObjectStatus, ReferenceSourceKind


def frozen_mapping(values: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    return MappingProxyType(dict(values or {}))


@dataclass(frozen=True)
class IndexEntry:
    """One typed, resolvable object in the action reference index.

    ``attrs`` holds structural facts copied from canonical objects; it is never
    derived from identifier spelling, rationales, or expected plans.
    """

    target_type: ActionTargetType
    ref: str
    source_kind: ReferenceSourceKind
    cognitive_turn_ref: str
    revision_ref: str | None = None
    status: ObjectStatus = ObjectStatus.CURRENT
    authorized: bool = True
    about_refs: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    focus_contribution: bool = False
    attrs: Mapping[str, Any] = field(default_factory=frozen_mapping)

    @property
    def key(self) -> tuple[str, str]:
        return (self.target_type.value, self.ref)

    @property
    def current(self) -> bool:
        return self.status == ObjectStatus.CURRENT and self.authorized

    def attr(self, name: str, default: Any = None) -> Any:
        return self.attrs.get(name, default)


@dataclass(frozen=True)
class TurnContext:
    """The immutable planning context: a digest plus the resolved reference index."""

    context_ref: str
    request_digest: str
    cognitive_turn_id: str
    focus_group: str
    index: Any
