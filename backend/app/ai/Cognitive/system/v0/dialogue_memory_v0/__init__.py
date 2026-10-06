"""Dialogue Working Memory V0: short-term, conversation-scoped dialogue state.

ChatMessage rows are the authoritative record; a dialogue snapshot is an
immutable, reproducible semantic projection of the ones actually exposed to
the user.  This package is pure: it imports no SQLAlchemy, Redis, provider
SDK, chat agent, or wall clock.  PostgreSQL ownership, locking, persistence,
and job scheduling live in ``app.services.dialogue_memory``.

V0 is shadow-only: it can change what the cognitive shadow observes and what
shadow Realization may word, never the live response, tools, long-term
memory, or learning.
"""

from .bootstrap import BootstrapWindow, bootstrap, select_window
from .cache_protocol import DialogueMemoryCache, NullDialogueMemoryCache
from .content_projector import DialogueRealizationContentProjectorV0
from .digests import dialogue_state_digest, fingerprint_of, prefix_digest_for
from .eligibility import resolve_exposure, source_event
from .errors import DialogueMemoryError
from .models import DeliveryFact, DialogueMemoryLimits, DialogueMessageRecord, SourceFingerprint
from .projector import (
    ActionDialogueProjection,
    action_projection,
    analyze_focus,
    compile_turn_context,
    interpretation_dialogue_context,
    perception_working_context,
    verify_turn_context,
)
from .updater import DialogueMemoryUpdaterV0

__all__ = [
    "ActionDialogueProjection", "BootstrapWindow", "DeliveryFact", "DialogueMemoryCache", "DialogueMemoryError",
    "DialogueRealizationContentProjectorV0",
    "DialogueMemoryLimits", "DialogueMemoryUpdaterV0", "DialogueMessageRecord", "NullDialogueMemoryCache",
    "SourceFingerprint", "action_projection", "analyze_focus", "bootstrap", "compile_turn_context",
    "dialogue_state_digest", "fingerprint_of", "interpretation_dialogue_context", "perception_working_context",
    "prefix_digest_for", "resolve_exposure", "select_window", "source_event", "verify_turn_context",
]
