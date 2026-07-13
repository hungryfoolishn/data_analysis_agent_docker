"""Utility functions for the analysis agent."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

# Constants
MAX_OUTPUT_LEN = 3000  # Truncate long python_repl output to avoid wasting tokens
CODE_TIMEOUT = 60       # Max seconds per python_repl call
MAX_PYTHON_REPL_LINES = 50
MAX_AGENT_STEPS = 48
MAX_CONSECUTIVE_PYTHON_ERRORS = 3
REQUIRED_STEP_MARKER_ALIASES = {
    "step objective": ("step objective", "步骤目标", "目标"),
    "method": ("method", "方法"),
    "key results": ("key results", "关键结果", "结果"),
    "suggested next step": ("suggested next step", "建议下一步", "下一步"),
}


def format_preview_text(text: object) -> str:
    """Return text for frontend display while preserving full line structure."""
    return str(text or "").strip()


def extract_event_output_text(raw: object) -> str:
    """Extract the meaningful tool output text from LangGraph event payloads."""
    if raw is None:
        return ""
    content = getattr(raw, "content", raw)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                piece = item.get("text") or item.get("content") or ""
            else:
                piece = getattr(item, "text", None) or str(item)
            if piece:
                parts.append(str(piece))
        return "\n".join(parts).strip()
    return str(content).strip()


def validate_python_repl_step(code: str) -> Optional[str]:
    """Return a runtime guardrail error when a python_repl step is too large or unstructured."""
    stripped = code.strip()
    if not stripped:
        return "[ERROR] Empty python_repl step. Provide a focused analysis step."

    code_lines = [line for line in stripped.splitlines() if line.strip()]
    if len(code_lines) > MAX_PYTHON_REPL_LINES:
        return (
            f"[ERROR] python_repl step is too large ({len(code_lines)} non-empty lines; limit {MAX_PYTHON_REPL_LINES}). "
            "Break the analysis into smaller steps and execute one sub-goal at a time."
        )

    lowered = stripped.lower()
    missing_markers = [
        canonical
        for canonical, aliases in REQUIRED_STEP_MARKER_ALIASES.items()
        if not any(alias.lower() in lowered for alias in aliases)
    ]
    if missing_markers:
        return (
            "[ERROR] python_repl step is missing required printed markers: "
            f"{', '.join(missing_markers)}. "
            "At the start print step objective/步骤目标 and method/方法; "
            "at the end print key results/关键结果 and suggested next step/建议下一步."
        )

    return None


def contains_evidence_marker(text: str) -> bool:
    evidence_pattern = re.compile(
        r"(\d|%|同比|环比|排名|Top|top|chart|figure|table|表|图|季度|月份|月|周|天|列|组|样本|均值|中位数|占比|region|channel|segment|evidence level|证据等级|contributor|contribution|refund|duplicate)",
        flags=re.IGNORECASE,
    )
    return bool(evidence_pattern.search(text))


def contains_quantitative_evidence(text: str) -> bool:
    quantitative_pattern = re.compile(
        r"(\d|%|同比|环比|排名|Top|top|季度|q[1-4]|月份|月|周|天|样本|均值|中位数|占比)",
        flags=re.IGNORECASE,
    )
    return bool(quantitative_pattern.search(text))


def has_time_window_reference(text: str) -> bool:
    time_pattern = re.compile(
        r"(\b20\d{2}\b|q[1-4]|季度|月份|月度|周度|日度|本周|上周|本月|上月|本季度|上季度|本年|去年|最近\d+[天周月年]?|最近一期|本期|上期|环比|同比|daily|weekly|monthly|quarterly|yearly|period|last month|last quarter|year over year|month over month)",
        flags=re.IGNORECASE,
    )
    return bool(time_pattern.search(text))


def has_group_reference(text: str) -> bool:
    group_pattern = re.compile(
        r"(维度|分组|组别|类别|区域|渠道|产品|客户|segment|group|category|region|channel)",
        flags=re.IGNORECASE,
    )
    return bool(group_pattern.search(text))


def extract_section(markdown: str, section_title: str) -> str:
    pattern = re.compile(
        rf"^##+\s+.*{re.escape(section_title)}.*$",
        flags=re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(markdown)
    if not match:
        return ""
    start = match.end()
    rest = markdown[start:]
    next_heading = re.search(r"^##+\s+", rest, flags=re.MULTILINE)
    if next_heading:
        return rest[:next_heading.start()].strip()
    return rest.strip()


def make_session_logger(workspace_dir: Path, session_id: str) -> logging.Logger:
    """Create a logger that writes to both stderr and a per-session log file."""
    logger = logging.getLogger(f"agent.{session_id}")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    log_path = workspace_dir / f"agent_{session_id}.log"
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.propagate = False
    return logger
