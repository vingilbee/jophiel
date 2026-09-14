"""AI layer: the nutrition chatbot agent (LangChain + OpenAI).

Public surface:
    - AgentRuntimeContext : per-request context (user_id + future integrations)
    - stream_chat         : async generator of response text chunks
    - complete_chat       : await the full response as a single string
"""
from app.ai.chat import complete_chat, stream_chat
from app.ai.context import AgentRuntimeContext

__all__ = ["AgentRuntimeContext", "stream_chat", "complete_chat"]
