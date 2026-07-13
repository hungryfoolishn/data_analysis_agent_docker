"""Centralised configuration for the LangGraph analysis agent.

All runtime knobs live here so they can be tuned via environment variables
without touching business logic.
"""

from __future__ import annotations

import os
from pathlib import Path

# ── LLM / API ────────────────────────────────────────────────────────────────
DEEPSEEK_API_KEY: str = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL_ID: str = os.environ.get("DEEPSEEK_MODEL_ID", "deepseek-chat")
DEEPSEEK_API_BASE: str = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")

# ── Agent runtime limits ─────────────────────────────────────────────────────
MAX_OUTPUT_LEN: int = int(os.environ.get("MAX_OUTPUT_LEN", "3000"))
"""Max characters of python_repl output before truncation."""

CODE_TIMEOUT: int = int(os.environ.get("CODE_TIMEOUT", "60"))
"""Seconds allowed per python_repl execution."""

MAX_PYTHON_REPL_LINES: int = int(os.environ.get("MAX_PYTHON_REPL_LINES", "50"))
"""Max non-empty lines in a single python_repl step."""

MAX_AGENT_STEPS: int = int(os.environ.get("MAX_AGENT_STEPS", "48"))
"""Upper bound on tool invocations per analysis run."""

MAX_CONSECUTIVE_PYTHON_ERRORS: int = int(
    os.environ.get("MAX_CONSECUTIVE_PYTHON_ERRORS", "3")
)
"""After this many consecutive python_repl errors the agent stops."""

# ── Required step markers (bilingual) ────────────────────────────────────────
REQUIRED_STEP_MARKER_ALIASES: dict[str, tuple[str, ...]] = {
    "step objective": ("step objective", "步骤目标", "目标"),
    "method": ("method", "方法"),
    "key results": ("key results", "关键结果", "结果"),
    "suggested next step": ("suggested next step", "建议下一步", "下一步"),
}

# ── Server / session ─────────────────────────────────────────────────────────
MAX_CONCURRENT_AGENTS: int = int(os.environ.get("MAX_CONCURRENT_AGENTS", "3"))
SESSION_TTL_HOURS: float = float(os.environ.get("SESSION_TTL_HOURS", "24"))

# ── Workspace ────────────────────────────────────────────────────────────────
WORKSPACE_DIR: Path = Path(os.environ.get("WORKSPACE_DIR", "./workspace"))
MAX_WORKSPACE_SIZE_MB: int = int(os.environ.get("MAX_WORKSPACE_SIZE_MB", "1024"))
MAX_UPLOAD_SIZE_MB: int = int(os.environ.get("MAX_UPLOAD_SIZE_MB", "100"))

# ── Artifact retention ───────────────────────────────────────────────────────
MAX_RETAINED_ARTIFACTS: int = 50
"""Max artifact metadata entries kept in memory after session cleanup."""

# ── Memory system ────────────────────────────────────────────────────────────
MEMORY_NOTES_CHAR_LIMIT: int = int(os.environ.get("MEMORY_NOTES_CHAR_LIMIT", "3000"))
"""Max chars for session analysis_notes store."""

MEMORY_CONTEXT_CHAR_LIMIT: int = int(os.environ.get("MEMORY_CONTEXT_CHAR_LIMIT", "1500"))
"""Max chars for session session_context store."""

MEMORY_GLOBAL_CHAR_LIMIT: int = int(os.environ.get("MEMORY_GLOBAL_CHAR_LIMIT", "3000"))
"""Max chars for global MEMORY.md (agent notes)."""

MEMORY_USER_CHAR_LIMIT: int = int(os.environ.get("MEMORY_USER_CHAR_LIMIT", "1500"))
"""Max chars for global USER.md (user profile)."""
