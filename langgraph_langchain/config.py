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

LLM_REQUEST_TIMEOUT: int = int(os.environ.get("LLM_REQUEST_TIMEOUT", "600"))
"""Seconds for each LLM API request (covers slow streaming on laggy networks).
Passed as ``request_timeout`` to ChatOpenAI. Increase if the model is slow
to respond or the network has high latency (e.g. on-prem vLLM)."""

LLM_MAX_RETRIES: int = int(os.environ.get("LLM_MAX_RETRIES", "5"))
"""Max retries for transient LLM API errors (rate-limit, timeout, 5xx)."""

LLM_EXTRA_HEADERS: dict[str, str] = {}
"""Extra HTTP headers sent with every LLM API request.
Populate via the ``LLM_EXTRA_HEADERS`` env var as ``key1=val1,key2=val2``
(e.g. ``ucid=555123,x-custom=foo``).  Useful for internal API gateways
that require auth / routing headers beyond the standard Authorization."""

_raw_headers = os.environ.get("LLM_EXTRA_HEADERS", "")
if _raw_headers:
    LLM_EXTRA_HEADERS = {
        k.strip(): v.strip()
        for k, v in (pair.split("=", 1) for pair in _raw_headers.split(",") if "=" in pair)
    }

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

# ── Convergence nudges (anti-divergence) ─────────────────────────────────────
CONVERGENCE_NUDGE_MIN_FINDINGS: int = int(
    os.environ.get("CONVERGENCE_NUDGE_MIN_FINDINGS", "3")
)
"""Once this many findings are recorded, python_repl results nudge toward finish_report."""

CONVERGENCE_NUDGE_MIN_STEPS: int = int(
    os.environ.get("CONVERGENCE_NUDGE_MIN_STEPS", "15")
)
"""Even with fewer findings, start nudging toward finish_report after this many tool steps."""

SYNTHESIZED_REPORT_MIN_FINDINGS: int = int(
    os.environ.get("SYNTHESIZED_REPORT_MIN_FINDINGS", "2")
)
"""Minimum recorded findings required to auto-synthesize a report when the agent
fails to call finish_report (max_steps / recursion limit / silent termination)."""

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
API_AUTH_TOKEN: str = os.environ.get("API_AUTH_TOKEN", "")
CORS_ALLOWED_ORIGINS: list[str] = [
    item.strip()
    for item in os.environ.get(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:18501,http://127.0.0.1:18501",
    ).split(",")
    if item.strip()
]

# ── Workspace ────────────────────────────────────────────────────────────────
WORKSPACE_DIR: Path = Path(os.environ.get("WORKSPACE_DIR", "./workspace"))
MAX_WORKSPACE_SIZE_MB: int = int(os.environ.get("MAX_WORKSPACE_SIZE_MB", "1024"))
MAX_UPLOAD_SIZE_MB: int = int(os.environ.get("MAX_UPLOAD_SIZE_MB", "100"))
MAX_IN_MEMORY_ROWS: int = int(os.environ.get("MAX_IN_MEMORY_ROWS", "500000"))
"""Rows above which file loading uses a disclosed deterministic sample."""

DATA_SAMPLE_ROWS: int = int(os.environ.get("DATA_SAMPLE_ROWS", "100000"))
"""Default sample size for sources larger than ``MAX_IN_MEMORY_ROWS``."""

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
