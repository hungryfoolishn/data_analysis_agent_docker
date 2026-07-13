"""Memory package for the data analysis agent.

Provides dual-layer memory:
  - Short-term (session): per-session observations stored in workspace/.agent_memory/
  - Long-term (global): cross-session user profile & agent notes stored in project root/.agent_memory/
"""

from langgraph_langchain.memory.memory_store import MemoryStore

__all__ = ["MemoryStore"]
