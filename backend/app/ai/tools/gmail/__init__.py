"""Gmail tools, exposed to the agent over MCP.

Three layers, so each can be reasoned about (and reused) alone:

    gmail_api.py  — async httpx wrapper over the Gmail REST API. Knows nothing
                    about MCP or our users; takes an access token.
    server.py     — an MCP server wrapping those calls as tools. Standalone:
                    runnable by any MCP client, reads one access token from the
                    environment, never touches the database.
    client.py     — spawns that server over stdio and adapts its tools into
                    LangChain tools for the LangGraph agent.

Tokens come from the Google integration (``app.ai.integrations.google``), which
owns the OAuth flow and refresh.

Public surface:
    - gmail_tools          : context manager yielding live LangChain tools
    - gmail_tools_or_none  : same, but degrades to [] instead of raising
"""
from app.ai.tools.gmail.client import gmail_tools, gmail_tools_or_none

__all__ = ["gmail_tools", "gmail_tools_or_none"]
