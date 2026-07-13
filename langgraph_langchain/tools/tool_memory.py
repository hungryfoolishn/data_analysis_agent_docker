"""memory tool — persist key observations across sessions.

Dual-layer memory:
  - Short-term (session): 'analysis_notes' and 'session_context' — scoped to current workspace
  - Long-term (global): 'memory' and 'user' — persists across all sessions

Actions: add, replace, remove
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from langchain_core.tools import tool

from langgraph_langchain.tools.registry import registry
from langgraph_langchain.tools._shared import _validate_tool_stage_factory
from langgraph_langchain.memory.memory_store import MemoryStore
from langgraph_langchain.config import (
    MEMORY_NOTES_CHAR_LIMIT,
    MEMORY_CONTEXT_CHAR_LIMIT,
    MEMORY_GLOBAL_CHAR_LIMIT,
    MEMORY_USER_CHAR_LIMIT,
)

logger = logging.getLogger(__name__)

_VALID_TARGETS = ("analysis_notes", "session_context", "memory", "user")

_SESSION_TARGETS = ("analysis_notes", "session_context")
_GLOBAL_TARGETS = ("memory", "user")


def _factory(session):
    _validate = _validate_tool_stage_factory(session)

    # Determine global memory dir: project root
    project_root = Path(__file__).resolve().parents[2]

    store = MemoryStore(
        session_dir=session.workspace_dir,
        global_dir=project_root,
        notes_char_limit=MEMORY_NOTES_CHAR_LIMIT,
        context_char_limit=MEMORY_CONTEXT_CHAR_LIMIT,
        memory_char_limit=MEMORY_GLOBAL_CHAR_LIMIT,
        user_char_limit=MEMORY_USER_CHAR_LIMIT,
    )
    store.load_from_disk()

    # Attach store to session for system prompt integration
    session._memory_store = store

    @tool
    def memory(
        action: str,
        target: str = "analysis_notes",
        content: str = None,
        old_text: str = None,
    ) -> str:
        """Save and manage persistent notes across the analysis session and beyond.

        TWO MEMORY LAYERS:

        Short-term (session-scoped, cleaned up when session ends):
        - 'analysis_notes': observations about data patterns, anomalies, domain insights
        - 'session_context': user preferences for this analysis, scope, key decisions

        Long-term (global, persists across sessions):
        - 'memory': agent's learned conventions, common data patterns, analysis lessons
        - 'user': user profile — preferences, communication style, domain expertise, language

        WHEN TO SAVE (proactively, don't wait to be asked):
        - User shares a preference or correction ("我更喜欢中文报告" → save to 'user')
        - You discover a reusable pattern or lesson → save to 'memory'
        - You observe an important data insight mid-analysis → save to 'analysis_notes'
        - User specifies analysis scope or constraints → save to 'session_context'

        ACTIONS: add (new entry), replace (update existing — old_text identifies it),
        remove (delete — old_text identifies it).

        Args:
            action: "add", "replace", or "remove"
            target: "analysis_notes", "session_context", "memory", or "user"
            content: Entry content (required for add/replace)
            old_text: Substring to identify entry (required for replace/remove)
        """
        # Validate stage
        error_msg = _validate("memory")
        if error_msg:
            return f"[ERROR] {error_msg}"

        if target not in _VALID_TARGETS:
            return json.dumps({
                "error": f"Invalid target '{target}'. Use: {', '.join(_VALID_TARGETS)}",
            }, ensure_ascii=False)

        if action == "add":
            if not content or not content.strip():
                return json.dumps({"error": "Content is required for 'add' action."}, ensure_ascii=False)
            result = store.add(target, content)
        elif action == "replace":
            if not old_text or not old_text.strip():
                return json.dumps({"error": "old_text is required for 'replace' action."}, ensure_ascii=False)
            if not content or not content.strip():
                return json.dumps({"error": "content is required for 'replace' action."}, ensure_ascii=False)
            result = store.replace(target, old_text, content)
        elif action == "remove":
            if not old_text or not old_text.strip():
                return json.dumps({"error": "old_text is required for 'remove' action."}, ensure_ascii=False)
            result = store.remove(target, old_text)
        else:
            return json.dumps({
                "error": f"Unknown action '{action}'. Use: add, replace, remove",
            }, ensure_ascii=False)

        return json.dumps(result, ensure_ascii=False)

    return memory


registry.register(
    name="memory",
    toolset="memory",
    factory=_factory,
    description="Save and manage persistent notes — session observations and cross-session knowledge.",
    emoji="🧠",
)
