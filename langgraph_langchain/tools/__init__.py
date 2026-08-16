"""Tool package for the data analysis agent.

Importing this module triggers registration of all tools via the registry.
Each tool file self-registers at import time, following the hermes-agent pattern.
"""

import importlib
import logging

from langgraph_langchain.tools.registry import registry

logger = logging.getLogger(__name__)

# All tool modules — import triggers self-registration
_TOOL_MODULES = [
    "langgraph_langchain.tools.tool_load_data",
    "langgraph_langchain.tools.tool_python_repl",
    "langgraph_langchain.tools.tool_eda_profile",
    "langgraph_langchain.tools.tool_analysis_methods",
    "langgraph_langchain.tools.tool_record_finding",
    "langgraph_langchain.tools.tool_declare_metric",
    "langgraph_langchain.tools.tool_declare_assumption",
    "langgraph_langchain.tools.tool_finish_report",
    "langgraph_langchain.tools.tool_skills",
    # Memory tool added after memory module is created
    "langgraph_langchain.tools.tool_memory",
    "langgraph_langchain.tools.tool_delegate",
]


def _discover_tools():
    """Import all tool modules to trigger registration."""
    for mod_name in _TOOL_MODULES:
        try:
            importlib.import_module(mod_name)
        except Exception as e:
            logger.warning("Could not import %s: %s", mod_name, e)


_discover_tools()


def get_tools_for_session(session) -> list:
    """Build all registered tools bound to the given session.

    Drop-in replacement for ``_make_tools(session)`` in langgraph_agent.py.
    """
    return registry.get_tools_for_session(session)
