"""Local perception and interpretation for the cognitive system.

This package deliberately has no online model calls and is not wired into chat yet.
"""

from .interpretation import IntentInterpreter
from .perception import PerceptionEngine
from .pipeline import CognitiveInputPipeline
from .schemas import Intent, Interpretation, Perception, PerceptionSignal

__all__ = [
    "CognitiveInputPipeline",
    "Intent",
    "IntentInterpreter",
    "Interpretation",
    "Perception",
    "PerceptionEngine",
    "PerceptionSignal",
]
