"""Her hands on her own Windows desktop (general desktop control, docs/GENERAL_DESKTOP_CONTROL_DESIGN.md).

Six direct orchestrator tools: ``screenshot``, ``move_mouse``, ``click``, ``drag``, ``scroll``, ``keyboard``.
Like the eye and the game tools they sit on the orchestrator itself rather than behind a sub-agent: she
looks, acts, and looks again in one loop, and an extra model hop per action would make that slow. Built per
turn for one user, offered only when that user has a paired desktop (``services/desktop_control.access_for``).

Screenshots reach her as pictures (``screenshot_hook``), not as a caption from another model, so she can
find controls and check what an action did.
"""
from app.ai.tools.desktop_control.tools import build_desktop_control_tools, screenshot_hook

__all__ = ["build_desktop_control_tools", "screenshot_hook"]
