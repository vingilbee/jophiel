"""Durable Live-Shadow Trace V0: observational contracts, compiler, and digests.

A live-shadow trace records what the accepted deterministic cognitive pipeline
would have done on an exact real turn.  It observes; it does not speak, act,
remember, reward, learn, or silently become truth.
"""

from .compiler import (
    CompileError,
    CompileSkip,
    PredecessorRecord,
    ShadowLineage,
    compile_request,
    resolve_lineage,
)
from .digests import payload_digest, semantic_digest, semantic_projection
from .models import (
    CAPTURE_SCHEMA_VERSION,
    COMPILER_VERSION,
    SEMANTIC_DIGEST_VERSION,
    TERMINAL_SCHEMA_VERSION,
    WORKER_VERSION,
    CognitiveShadowCapture,
    CognitiveShadowTerminalRecord,
    SegmentResetReason,
    ShadowFailure,
    SkipReason,
    TerminalStatus,
)
from .service import ObservationContext, ReplayReport, ReplayStatus, load_capture, observe, replay
from .realization import realize_record, replay_realization

__all__ = [
    "CAPTURE_SCHEMA_VERSION", "COMPILER_VERSION", "SEMANTIC_DIGEST_VERSION", "TERMINAL_SCHEMA_VERSION",
    "WORKER_VERSION", "CognitiveShadowCapture", "CognitiveShadowTerminalRecord", "CompileError", "CompileSkip",
    "ObservationContext", "PredecessorRecord", "ReplayReport", "ReplayStatus", "SegmentResetReason",
    "ShadowFailure", "ShadowLineage", "SkipReason", "TerminalStatus", "compile_request", "load_capture",
    "observe", "payload_digest", "realize_record", "replay", "replay_realization", "resolve_lineage",
    "semantic_digest", "semantic_projection",
]
