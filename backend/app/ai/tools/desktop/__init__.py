"""Jophiel's game tools (desktop agent, stage D2a).

Like the eye, these sit on the orchestrator directly rather than behind a
sub-agent: there are few of them, they are cheap, and "pause!" must not wait
for an extra model hop. Built per turn for one user, and offered only when
that user has a paired game VM.
"""
from app.ai.tools.desktop.tools import build_desktop_tools

__all__ = ["build_desktop_tools"]
