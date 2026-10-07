"""Desktop agent controller: the game-agnostic action contract and its enforcement.

See docs/DESKTOP_AGENT_IMPLEMENTATION_PLAN.md §7. Public surface:

- ``protocol``: ``Decision``, ``StepOutput``, ``Rejection`` wire models;
- ``profile``: ``load_profile`` and the profile model;
- ``validate``: ``validate_decision`` against a profile and context;
- ``scheduler``: the input-state scheduler (controller core);
- ``fake``: a manual clock and recording backend for tests.
"""

from .profile import Profile, ProfileError, load_profile
from .protocol import Decision, Rejection, RejectionCode, StepOutput
from .validate import ValidatedDecision, validate_decision

__all__ = [
    "Decision",
    "Profile",
    "ProfileError",
    "Rejection",
    "RejectionCode",
    "StepOutput",
    "ValidatedDecision",
    "load_profile",
    "validate_decision",
]
