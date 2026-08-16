"""Shared helpers used by multiple tool modules.

Extracted from langgraph_agent.py to avoid circular imports and code duplication.
This module only imports from leaf modules (config, tool_validators, state_machine)
— never from langgraph_agent.py or tool files.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from langgraph_langchain.config import (
    MAX_OUTPUT_LEN as _MAX_OUTPUT_LEN,
    MAX_PYTHON_REPL_LINES as _MAX_PYTHON_REPL_LINES,
)


# ── Pre-compiled regex patterns ─────────────────────────────────────────────

_RE_EVIDENCE_MARKER = re.compile(
    r"(\d|%|同比|环比|排名|Top|top|chart|figure|table|表|图|季度|月份|月|周|天|列|组|样本|均值|中位数|占比|region|channel|segment|evidence level|证据等级|contributor|contribution|refund|duplicate)",
    flags=re.IGNORECASE,
)
_RE_QUANTITATIVE_EVIDENCE = re.compile(
    r"(\d|%|同比|环比|排名|Top|top|季度|q[1-4]|月份|月|周|天|样本|均值|中位数|占比)",
    flags=re.IGNORECASE,
)
_RE_TIME_WINDOW = re.compile(
    r"(\b20\d{2}\b|q[1-4]|季度|月份|月度|周度|日度|本周|上周|本月|上月|本季度|上季度|本年|去年|最近\d+[天周月年]?|最近一期|本期|上期|环比|同比|daily|weekly|monthly|quarterly|yearly|period|last month|last quarter|year over year|month over month)",
    flags=re.IGNORECASE,
)
_RE_GROUP_REFERENCE = re.compile(
    r"(维度|分组|组别|类别|区域|渠道|产品|客户|segment|group|category|region|channel)",
    flags=re.IGNORECASE,
)
_RE_NEXT_HEADING = re.compile(r"^##+\s+", flags=re.MULTILINE)


# ── Path safety ──────────────────────────────────────────────────────────────

def _safe_workspace_path(base_dir: Path, filename: str) -> Path:
    """Resolve *filename* under *base_dir* and verify it stays inside.

    Prevents path-traversal (e.g. ``../../etc/passwd``) by resolving the
    joined path and checking it is still within *base_dir*.
    """
    base_resolved = base_dir.resolve()
    target = (base_resolved / filename).resolve()
    if not str(target).startswith(str(base_resolved) + os.sep) and target != base_resolved:
        raise ValueError(
            f"Path traversal detected: '{filename}' resolves outside workspace "
            f"'{base_resolved}'"
        )
    return target


def _resolve_load_path(base_dir: Path, file_path: str) -> Path:
    """Resolve a data-file path for the ``load_data`` tool.

    Unlike :func:`_safe_workspace_path` (which joins a bare filename under
    *base_dir*), this accepts the three path forms the agent actually passes:

    1. Absolute path, e.g. ``/app/workspace/<session>/data.csv``
    2. CWD-relative path, e.g. ``workspace/<session>/data.csv`` -- the form the
       upload API and session store record, and what the agent tends to pass
       after listing the workspace itself (CWD is the project root ``/app``)
    3. base_dir-relative path, e.g. ``<session>/data.csv``

    Each candidate is resolved and verified to stay within *base_dir* (same
    traversal guard as :func:`_safe_workspace_path`); the first that points to
    an existing file wins.

    Raises:
        ValueError: if every candidate resolves outside *base_dir*.
        FileNotFoundError: if no candidate resolves to an existing file inside
            *base_dir*.
    """
    base_resolved = base_dir.resolve()
    raw = Path(file_path)

    if raw.is_absolute():
        candidates = [raw]
    else:
        candidates = [
            Path.cwd() / raw,        # CWD-relative (upload / session-store form)
            base_resolved / raw,     # base_dir-relative (bare <session>/file)
        ]

    traversal_error: Optional[str] = None
    for cand in candidates:
        resolved = cand.resolve()
        if not (
            str(resolved).startswith(str(base_resolved) + os.sep)
            or resolved == base_resolved
        ):
            traversal_error = (
                f"Path traversal detected: '{file_path}' resolves outside "
                f"workspace '{base_resolved}'"
            )
            continue
        if resolved.is_file():
            return resolved

    if traversal_error:
        raise ValueError(traversal_error)
    raise FileNotFoundError(
        f"Data file not found in workspace: '{file_path}' "
        f"(looked under CWD '{Path.cwd()}' and workspace '{base_resolved}')"
    )


# ── Python REPL validation ──────────────────────────────────────────────────

def _validate_python_repl_step(code: str) -> Optional[str]:
    """Return a runtime guardrail error when a python_repl step is empty or too large."""
    stripped = code.strip()
    if not stripped:
        return "[ERROR] Empty python_repl step. Provide a focused analysis step."

    code_lines = [line for line in stripped.splitlines() if line.strip()]
    if len(code_lines) > _MAX_PYTHON_REPL_LINES:
        return (
            f"[ERROR] python_repl step is too large ({len(code_lines)} non-empty lines; limit {_MAX_PYTHON_REPL_LINES}). "
            "Break the analysis into smaller steps and execute one sub-goal at a time."
        )

    return None


def _is_rd_domain_session(session) -> bool:
    """Return whether EDA selected an R&D-specific analysis template."""
    namespace = getattr(session, "ns", {}) or {}
    return namespace.get("suggested_template") is not None


# ── Evidence / text helpers ─────────────────────────────────────────────────

def _contains_evidence_marker(text: str) -> bool:
    return bool(_RE_EVIDENCE_MARKER.search(text))


def _contains_quantitative_evidence(text: str) -> bool:
    return bool(_RE_QUANTITATIVE_EVIDENCE.search(text))


def _has_time_window_reference(text: str) -> bool:
    return bool(_RE_TIME_WINDOW.search(text))


def _has_group_reference(text: str) -> bool:
    return bool(_RE_GROUP_REFERENCE.search(text))


def _extract_section(markdown: str, section_title: str) -> str:
    """Extract a markdown section by heading title."""
    pattern = re.compile(
        rf"^##+\s+.*{re.escape(section_title)}.*$",
        flags=re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(markdown)
    if not match:
        return ""
    start = match.end()
    rest = markdown[start:]
    next_heading = _RE_NEXT_HEADING.search(rest)
    if next_heading:
        return rest[:next_heading.start()].strip()
    return rest.strip()


# ── Stage ranking ────────────────────────────────────────────────────────────

def _stage_rank(stage) -> int:
    """Return numeric rank of an analysis stage (higher = later in flow)."""
    order = [
        "schema_understanding",
        "data_quality_check",
        "basic_eda",
        "deep_dive",
        "conclusion_synthesis",
        "report_generation",
    ]
    from langgraph_langchain.schemas import AnalysisStage
    stage_value = stage.value if isinstance(stage, AnalysisStage) else str(stage)
    return order.index(stage_value)


# ── Tool stage validation factory ────────────────────────────────────────────

def _validate_tool_stage_factory(session):
    """Create a stage validation closure for the given session.

    This replaces the inline ``_validate_tool_stage`` that was defined
    inside ``_make_tools()`` in langgraph_agent.py.
    """
    from langgraph_langchain.tool_validators import ToolStageValidator

    def _validate_tool_stage(tool_name: str) -> Optional[str]:
        """Validate if tool can be called in current stage. Returns error message if invalid."""
        is_valid, error_msg = ToolStageValidator.validate_tool_call(
            tool_name=tool_name,
            current_stage=session.state_machine.current_stage,
            tools_used=session.state_machine.tools_used,
            findings_count=len(session.findings),
        )
        if not is_valid:
            session.logger.warning(
                "tool_validation_failed tool=%s stage=%s error=%s",
                tool_name, session.state_machine.current_stage.value, error_msg,
            )
        return error_msg if not is_valid else None

    return _validate_tool_stage
