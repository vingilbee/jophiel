"""The eye: letting the orchestrator actually look at the user's screen.

Unlike Gmail and MacroVision this is **not** an MCP toolset behind a sub-agent.
It is a single tool handed to the orchestrator directly, and that is a
deliberate exception to the delegation rule in CLAUDE.md:

  * There is one tool, not fifteen, so it costs almost no context.
  * It carries no instructions that would crowd another toolset's.
  * "What's this error on my screen?" is latency-sensitive in a way mail is
    not. Routing it through a specialist would add an LLM hop between the
    question and the answer, which is exactly the thing that would make an
    always-on eye feel sluggish instead of alive.

It is built per turn (it closes over one user and one frame id) and injected
only when the user actually has screen sharing on, so everyone else pays
nothing — the same shape as the per-user Gmail tools.
"""
from app.ai.tools.screen.tool import build_look_at_screen

__all__ = ["build_look_at_screen"]
