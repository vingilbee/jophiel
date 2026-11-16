"""Her desktop's security records, read only (docs/DESKTOP_NETWORK_AWARENESS_PLAN.md): one direct orchestrator
tool, ``get_security_activity``. Like her memory tools it sits on the orchestrator itself (cheap, read only, and
the answer has to reach her mouth as it is, coverage included). Offered when the user has a paired desktop
(``services/security_activity.line_for``), on reply and follow-up turns alike; never on game-event turns."""
from app.ai.tools.security.tools import build_security_tools, parse_moment

__all__ = ["build_security_tools", "parse_moment"]
