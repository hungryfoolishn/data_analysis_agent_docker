"""Central registry for all analysis agent tools.

Each tool file calls ``registry.register()`` at module level to declare its
factory function, toolset membership, and metadata.  ``__init__.py`` triggers
discovery by importing all tool modules.

Adapted from hermes-agent's tools/registry.py, but simplified for LangChain
@tool compatibility: we store factory functions instead of raw OpenAI schemas.

Import chain (circular-import safe):
    tools/registry.py  (no imports from langgraph_agent or tool files)
           ^
    tools/*.py  (import from tools.registry at module level)
           ^
    tools/__init__.py  (imports all tool modules)
           ^
    langgraph_agent.py  (imports tools package)
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolEntry:
    """Metadata for a single registered tool."""
    name: str
    toolset: str
    factory: Callable          # (_Session) -> BaseTool
    description: str
    check_fn: Optional[Callable] = None
    emoji: str = ""


class ToolRegistry:
    """Singleton registry that collects tool factories from tool files.

    Unlike hermes-agent which stores raw OpenAI schemas + handlers, this
    registry stores *factory functions* that produce LangChain @tool-decorated
    objects bound to a session. This preserves the closure pattern where tools
    access session state (namespace, findings, logger, etc.).
    """

    def __init__(self):
        self._tools: Dict[str, ToolEntry] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        toolset: str,
        factory: Callable,
        description: str = "",
        check_fn: Optional[Callable] = None,
        emoji: str = "",
    ):
        """Register a tool factory. Called at module-import time by each tool file."""
        existing = self._tools.get(name)
        if existing and existing.toolset != toolset:
            logger.warning(
                "Tool name collision: '%s' (toolset '%s') is being "
                "overwritten by toolset '%s'",
                name, existing.toolset, toolset,
            )
        self._tools[name] = ToolEntry(
            name=name,
            toolset=toolset,
            factory=factory,
            description=description,
            check_fn=check_fn,
            emoji=emoji,
        )

    def deregister(self, name: str) -> None:
        """Remove a tool from the registry."""
        if name in self._tools:
            del self._tools[name]
            logger.debug("Deregistered tool: %s", name)

    # ------------------------------------------------------------------
    # Tool building
    # ------------------------------------------------------------------

    def get_tools_for_session(self, session) -> list:
        """Build and return all registered tools bound to the given session.

        Only tools whose ``check_fn()`` returns True (or have no check_fn)
        are included.
        """
        tools = []
        check_cache: Dict[Callable, bool] = {}
        for name in sorted(self._tools.keys()):
            entry = self._tools[name]
            # Check availability
            if entry.check_fn:
                if entry.check_fn not in check_cache:
                    try:
                        check_cache[entry.check_fn] = bool(entry.check_fn())
                    except Exception:
                        check_cache[entry.check_fn] = False
                        logger.debug("Tool %s check raised; skipping", name)
                if not check_cache[entry.check_fn]:
                    logger.debug("Tool %s unavailable (check failed)", name)
                    continue
            try:
                tool_obj = entry.factory(session)
                if isinstance(tool_obj, list):
                    tools.extend(tool_obj)
                else:
                    tools.append(tool_obj)
            except Exception as e:
                logger.warning("Tool %s factory failed: %s", name, e)
        return tools

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_tool_names(self) -> List[str]:
        """Return sorted list of all registered tool names."""
        return sorted(self._tools.keys())

    def get_toolset_for_tool(self, name: str) -> Optional[str]:
        """Return the toolset a tool belongs to, or None."""
        entry = self._tools.get(name)
        return entry.toolset if entry else None

    def get_emoji(self, name: str, default: str = "⚡") -> str:
        """Return the emoji for a tool, or *default* if unset."""
        entry = self._tools.get(name)
        return (entry.emoji if entry and entry.emoji else default)

    def get_all_toolsets(self) -> Dict[str, List[str]]:
        """Return {toolset_name: [tool_names]} for every registered toolset."""
        result: Dict[str, List[str]] = {}
        for entry in self._tools.values():
            result.setdefault(entry.toolset, []).append(entry.name)
        return result


# Module-level singleton
registry = ToolRegistry()


# ---------------------------------------------------------------------------
# Helpers for consistent tool responses (adapted from hermes-agent)
# ---------------------------------------------------------------------------

def tool_error(message: str, **extra) -> str:
    """Return a JSON error string for tool handlers."""
    result = {"error": str(message)}
    if extra:
        result.update(extra)
    return json.dumps(result, ensure_ascii=False)


def tool_result(data=None, **kwargs) -> str:
    """Return a JSON result string for tool handlers."""
    if data is not None:
        return json.dumps(data, ensure_ascii=False)
    return json.dumps(kwargs, ensure_ascii=False)
