#!/usr/bin/env python
# coding=utf-8
"""
LangGraph ReAct Data Analysis API Server.

Endpoints (compatible with the Streamlit webui):
  POST /v1/chat/completions  — streaming (SSE) or non-streaming chat
  POST /v1/files             — OpenAI-compatible file upload
  POST /workspace/upload     — session-aware file upload
  GET  /workspace/files      — list session files & artifacts
  GET  /workspace/files/{filename:path} — download file
  DELETE /sessions/{session_id}         — delete session
  POST   /sessions/{session_id}/cancel   — cancel running analysis
  GET  /health
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

import sys
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langgraph_langchain.langgraph_agent import run_analysis_stream
from langgraph_langchain.schemas import AnalysisStage
from langgraph_langchain.recovery import get_recovery_executor
from langgraph_langchain.stability_metrics import get_metrics_tracker
from langgraph_langchain.error_messages import format_user_friendly_error
from langgraph_langchain.tracing import TraceContext
from langgraph_langchain.user_friendly_response import create_transformer
from langgraph_langchain.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_MODEL_ID,
    DEEPSEEK_API_BASE,
    MAX_CONCURRENT_AGENTS,
    SESSION_TTL_HOURS,
    WORKSPACE_DIR,
    MAX_UPLOAD_SIZE_MB,
)

load_dotenv()

WORKSPACE_DIR.mkdir(exist_ok=True)

_SESSIONS_FILE = WORKSPACE_DIR / ".sessions.json"
_SESSION_TTL = timedelta(hours=max(SESSION_TTL_HOURS, 0.0))
_agent_semaphore: asyncio.Semaphore | None = None

# ── Metrics response cache (TTL = 10 s) ──────────────────────────────────────
_METRICS_CACHE_TTL = 10.0  # seconds
_metrics_cache: Dict[str, tuple[float, Any]] = {}


def _get_cached(key: str) -> Optional[Any]:
    """Return cached value if still fresh, else None."""
    ts, val = _metrics_cache.get(key, (0.0, None))
    if (datetime.now().timestamp() - ts) < _METRICS_CACHE_TTL:
        return val
    return None


def _set_cache(key: str, value: Any) -> None:
    _metrics_cache[key] = (datetime.now().timestamp(), value)


# ── Simple per-IP rate limiter ────────────────────────────────────────────────
from collections import defaultdict

_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX_REQUESTS = 30  # per window per IP
_rate_limit_counters: Dict[str, tuple[float, int]] = defaultdict(lambda: (0.0, 0))


def _check_rate_limit(client_ip: str) -> None:
    """Raise 429 if client exceeded rate limit."""
    now = datetime.now().timestamp()
    window_start, count = _rate_limit_counters[client_ip]
    if now - window_start > _RATE_LIMIT_WINDOW:
        _rate_limit_counters[client_ip] = (now, 1)
        return
    count += 1
    _rate_limit_counters[client_ip] = (window_start, count)
    if count > _RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({_RATE_LIMIT_MAX_REQUESTS} requests/{_RATE_LIMIT_WINDOW}s). Retry later.",
        )

# session_id -> {files:[], artifacts:[], workspace:str, created_at:str}
SESSIONS: Dict[str, Dict[str, Any]] = {}

# session_id -> asyncio.Event (set to request cancellation of a running agent)
_ACTIVE_CANCELS: Dict[str, asyncio.Event] = {}

# Lock to prevent race conditions on SESSIONS / _ACTIVE_CANCELS mutations
_sessions_lock = asyncio.Lock()


class AnalysisFailureError(Exception):
    """Raised when analysis stops with a structured failure payload."""

    def __init__(self, detail: Dict[str, Any]):
        self.detail = detail
        super().__init__(detail.get("message") or "Analysis failed")


def _failure_policy(code: str) -> Dict[str, Any]:
    matrix: Dict[str, Dict[str, Any]] = {
        "cancelled": {
            "retryable": True,
            "hint": "Restart the analysis when ready.",
            "recovery_action": "retry_same_scope",
        },
        "python_execution_error": {
            "retryable": True,
            "hint": "Use smaller validated analysis steps before retrying.",
            "recovery_action": "retry_narrower_scope",
        },
        "max_steps_exceeded": {
            "retryable": True,
            "hint": "Narrow the task scope or summarize the strongest validated findings in fewer steps.",
            "recovery_action": "retry_narrower_scope",
        },
        "report_rejected": {
            "retryable": True,
            "hint": "Revise the report to address the reported gate failures, then resubmit.",
            "recovery_action": "retry_narrower_scope",
        },
        "missing_data_file": {
            "retryable": True,
            "hint": "Upload a CSV/Excel file to the session, or pass file_path explicitly.",
            "recovery_action": "user_action_required",
        },
        "session_not_found": {
            "retryable": False,
            "hint": "Create a new session or use an existing valid session_id.",
            "recovery_action": "user_action_required",
        },
        "session_workspace_missing": {
            "retryable": False,
            "hint": "Re-upload the source file or create a new session.",
            "recovery_action": "user_action_required",
        },
        "session_expired": {
            "retryable": False,
            "hint": "Create a new session and upload the data file again.",
            "recovery_action": "user_action_required",
        },
        "schema_understanding_failed": {
            "retryable": True,
            "hint": "Data schema could not be understood. Check if the file format is valid and columns are properly named.",
            "recovery_action": "user_action_required",
        },
        "field_semantic_unclear": {
            "retryable": True,
            "hint": "Field meanings are ambiguous. Provide explicit field definitions or rename columns to be more descriptive.",
            "recovery_action": "user_action_required",
        },
        "tool_execution_failed": {
            "retryable": True,
            "hint": "A tool failed to execute. Check tool parameters and data format.",
            "recovery_action": "retry_narrower_scope",
        },
        "reasoning_drift": {
            "retryable": True,
            "hint": "Analysis went off track. Restart with a more focused question or narrower scope.",
            "recovery_action": "retry_narrower_scope",
        },
        "report_generation_failed": {
            "retryable": True,
            "hint": "Report generation failed. Ensure sufficient findings were recorded and all required sections are present.",
            "recovery_action": "retry_narrower_scope",
        },
        "timeout": {
            "retryable": True,
            "hint": "Analysis timed out. Try a simpler question or smaller dataset.",
            "recovery_action": "retry_narrower_scope",
        },
        "session_interrupted": {
            "retryable": True,
            "hint": "Session was interrupted. Restart the analysis from the beginning.",
            "recovery_action": "retry_same_scope",
        },
        "disk_space_exhausted": {
            "retryable": True,
            "hint": "Workspace ran out of disk space. Reduce chart generation or clean up files.",
            "recovery_action": "retry_with_sampled_data",
        },
        "memory_limit_exceeded": {
            "retryable": True,
            "hint": "Analysis exceeded memory limits. Use sampled data for the next attempt.",
            "recovery_action": "retry_with_sampled_data",
        },
        "network_timeout": {
            "retryable": True,
            "hint": "Network timeout — likely transient. Retry the same analysis.",
            "recovery_action": "retry_same_scope",
        },
        "corrupted_data_file": {
            "retryable": True,
            "hint": "Data file appears corrupted. Try re-exporting with proper encoding.",
            "recovery_action": "retry_narrower_scope",
        },
        "permission_denied": {
            "retryable": False,
            "hint": "File system permission denied. Contact admin to check workspace permissions.",
            "recovery_action": "user_action_required",
        },
        "missing_api_key": {
            "retryable": False,
            "hint": "DEEPSEEK_API_KEY not configured. Set it in .env or environment variables.",
            "recovery_action": "user_action_required",
        },
    }
    return dict(matrix.get(code, {}))


def _failure_detail(
    code: str,
    message: str,
    *,
    status_code: int,
    retryable: bool = False,
    hint: Optional[str] = None,
    stage: Optional[AnalysisStage] = None,
    error_type: str = "analysis_error",
    **extra: Any,
) -> Dict[str, Any]:
    policy = _failure_policy(code)
    if hint is None:
        hint = policy.get("hint")
    retryable = bool(policy.get("retryable", retryable))
    recovery_action = policy.get("recovery_action")

    # 使用用户友好的错误消息
    user_friendly = format_user_friendly_error(code, message)

    detail: Dict[str, Any] = {
        "type": error_type,
        "code": code,
        "message": user_friendly["message"],  # 使用用户友好的消息
        "title": user_friendly["title"],  # 添加中文标题
        "suggestions": user_friendly["suggestions"],  # 添加建议列表
        "retryable": retryable,
        "technical_message": message,  # 保留原始技术消息供调试
    }
    if hint:
        detail["hint"] = hint
    if recovery_action:
        detail["recovery_action"] = recovery_action
    if stage:
        detail["stage"] = stage
    if status_code:
        detail["status_code"] = status_code
    detail.update(extra)
    return detail


def _is_report_rejected(output: str) -> bool:
    normalized = (output or "").lstrip()
    return normalized.startswith("REPORT REJECTED") or normalized.startswith("[REPORT REJECTED]")


def _workspace_file_response(filename: str) -> FileResponse:
    requested = WORKSPACE_DIR / filename
    try:
        resolved = requested.resolve(strict=True)
        workspace_root = WORKSPACE_DIR.resolve(strict=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail="Invalid file path") from exc

    if workspace_root not in resolved.parents and resolved != workspace_root:
        raise HTTPException(status_code=404, detail="File not found")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(resolved, filename=resolved.name)


def _serialize_session(session: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "files": session.get("files", []),
        "artifacts": session.get("artifacts", []),
        "workspace": str(session.get("workspace", "")),
        "created_at": session.get("created_at", ""),
        "last_accessed_at": session.get("last_accessed_at", session.get("created_at", "")),
    }


def _parse_iso_timestamp(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _mark_session_accessed(session: Dict[str, Any]) -> None:
    session["last_accessed_at"] = datetime.now().isoformat()


def _is_session_stale(session: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    if _SESSION_TTL <= timedelta(0):
        return False
    now = now or datetime.now()
    last_seen = _parse_iso_timestamp(session.get("last_accessed_at")) or _parse_iso_timestamp(session.get("created_at"))
    if last_seen is None:
        return False
    return now - last_seen > _SESSION_TTL


def _prune_stale_sessions(now: Optional[datetime] = None) -> List[str]:
    now = now or datetime.now()
    removed: List[str] = []
    for sid, session in list(SESSIONS.items()):
        if not _is_session_stale(session, now=now):
            continue
        workspace = Path(session.get("workspace", ""))
        _ACTIVE_CANCELS.pop(sid, None)
        SESSIONS.pop(sid, None)
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)
        removed.append(sid)
    if removed:
        _save_sessions()
    return removed


def _load_sessions() -> None:
    """Reload persisted sessions from disk, skip entries whose workspace is gone or stale."""
    SESSIONS.clear()
    if not _SESSIONS_FILE.exists():
        return
    try:
        data = json.loads(_SESSIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return
    now = datetime.now()
    changed = False
    for sid, s in data.items():
        ws = Path(s.get("workspace", ""))
        if not ws.exists():
            changed = True
            continue
        session = _serialize_session(s)
        if _is_session_stale(session, now=now):
            shutil.rmtree(ws, ignore_errors=True)
            changed = True
            continue
        SESSIONS[sid] = session
    if changed:
        _save_sessions()


def _save_sessions() -> None:
    """Persist current SESSIONS to disk (best-effort)."""
    try:
        payload = {sid: _serialize_session(session) for sid, session in SESSIONS.items()}
        _SESSIONS_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def get_session_workspace(session_id: str) -> Path:
    p = WORKSPACE_DIR / session_id
    p.mkdir(exist_ok=True, parents=True)
    return p


def _get_session_steps(session_id: str) -> int:
    """Read total steps from session metadata file."""
    try:
        workspace = get_session_workspace(session_id)
        metadata_path = workspace / "session_metadata.json"
        if metadata_path.exists():
            import json
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            return metadata.get("total_steps", 0)
    except Exception:
        pass
    return 0


def _ensure_session_consistency(session_id: str) -> Dict[str, Any]:
    """Validate session exists and is healthy. Mutations handled by caller under lock."""
    if session_id not in SESSIONS:
        raise HTTPException(
            status_code=404,
            detail=_failure_detail(
                "session_not_found",
                "Session not found",
                status_code=404,
                error_type="session_error",
            ),
        )
    session = SESSIONS[session_id]
    workspace = Path(session.get("workspace", ""))
    if not workspace.exists():
        raise HTTPException(
            status_code=404,
            detail=_failure_detail(
                "session_workspace_missing",
                "Session workspace not found",
                status_code=404,
                error_type="session_error",
            ),
        )
    if _is_session_stale(session):
        raise HTTPException(
            status_code=404,
            detail=_failure_detail(
                "session_expired",
                "Session expired",
                status_code=404,
                error_type="session_error",
            ),
        )
    return session


def _cleanup_session(session_id: str, remove_workspace: bool = False) -> None:
    """Remove session from SESSIONS and _ACTIVE_CANCELS. Caller must hold _sessions_lock."""
    session = SESSIONS.get(session_id)
    if session and remove_workspace:
        ws = Path(session.get("workspace", ""))
        if ws.exists():
            shutil.rmtree(ws, ignore_errors=True)
    SESSIONS.pop(session_id, None)
    _ACTIVE_CANCELS.pop(session_id, None)
    _save_sessions()


async def _check_session(session_id: str) -> Dict[str, Any]:
    """Validate session and clean up stale entries. Thread-safe."""
    async with _sessions_lock:
        try:
            session = _ensure_session_consistency(session_id)
        except HTTPException:
            _cleanup_session(session_id, remove_workspace=True)
            raise
        _mark_session_accessed(session)
        return session



async def get_or_create_session(session_id: Optional[str]) -> tuple[str, Dict[str, Any]]:
    async with _sessions_lock:
        if session_id and session_id in SESSIONS:
            try:
                session = _ensure_session_consistency(session_id)
            except HTTPException:
                _cleanup_session(session_id, remove_workspace=True)
                raise
            _mark_session_accessed(session)
            _save_sessions()
            return session_id, session
        sid = session_id or str(uuid.uuid4())
        workspace = get_session_workspace(sid)
        now = datetime.now().isoformat()
        SESSIONS[sid] = {
            "files": [],
            "artifacts": [],
            "created_at": now,
            "last_accessed_at": now,
            "workspace": str(workspace),
        }
        _save_sessions()
        return sid, SESSIONS[sid]


def _resolve_data_file(session: Dict[str, Any], requested_path: Optional[str]) -> str:
    file_path_to_use = requested_path or (session["files"][-1]["path"] if session.get("files") else None)
    if not file_path_to_use:
        raise HTTPException(
            status_code=400,
            detail=_failure_detail(
                "missing_data_file",
                "No data file found — upload a file first or pass file_path",
                status_code=400,
                error_type="request_error",
            ),
        )
    path = Path(file_path_to_use)
    if not path.exists() or not path.is_file():
        raise HTTPException(
            status_code=400,
            detail=_failure_detail(
                "missing_data_file",
                "Data file does not exist or is not readable",
                status_code=400,
                hint="Check that file_path points to an existing readable file.",
                error_type="request_error",
                file_path=str(path),
            ),
        )
    # Return an absolute, resolved path so the agent receives an unambiguous
    # "Data file: ..." in its prompt. The upload API and session store record
    # CWD-relative paths ("workspace/<session>/file"); returning them verbatim
    # caused load_data to mis-resolve on the agent's first step.
    return str(path.resolve())


def _extract_stage(output: str) -> Optional[AnalysisStage]:
    lowered = (output or "").lower()
    for stage in ("final_report", "synthesis", "deep_dive", "eda", "schema_understanding"):
        if stage in lowered:
            return stage  # type: ignore[return-value]
    return None


def _structured_failure_from_output(output: str) -> Optional[Dict[str, Any]]:
    normalized = (output or "").strip()
    lowered = normalized.lower()
    if not normalized:
        return None
    if _is_report_rejected(normalized):
        return _failure_detail(
            "report_rejected",
            normalized,
            status_code=422,
            retryable=True,
            hint="Revise the report to address the reported gate failures, then resubmit.",
            stage=_extract_stage(normalized) or "final_report",
            error_type="analysis_error",
        )
    if "repeated python_repl errors without recovery" in lowered:
        return _failure_detail(
            "python_execution_error",
            "Repeated python execution errors without recovery",
            status_code=422,
            retryable=True,
            hint="Use smaller validated analysis steps before retrying.",
            stage="deep_dive",
            error_type="analysis_error",
        )
    if "exceeded the maximum tool-step budget" in lowered:
        return _failure_detail(
            "max_steps_exceeded",
            "Exceeded the maximum tool-step budget",
            status_code=422,
            retryable=True,
            hint="Narrow the task scope or summarize the strongest validated findings in fewer steps.",
            stage=_extract_stage(normalized),
            error_type="analysis_error",
        )
    if "analysis cancelled" in lowered:
        return _failure_detail(
            "cancelled",
            "Analysis cancelled",
            status_code=499,
            retryable=True,
            hint="Restart the analysis when ready.",
            stage=_extract_stage(normalized),
            error_type="analysis_error",
        )
    return None


async def _run_analysis_stream(
    session_id: str,
    session: Dict[str, Any],
    user_message: str,
    file_path_to_use: str,
    retry_count: int = 0,
):
    """Streaming variant of :func:`_run_analysis`.

    Yields ``(text, artifacts)`` chunks as the agent produces them so SSE
    clients see incremental output (each tool step, LLM tokens, artifacts)
    instead of waiting for the whole analysis to finish. Preserves the same
    semaphore / cancel-event / metrics / artifact / failure-recovery semantics
    as the non-streaming path; recovery retries re-yield recursively.
    """
    session_workspace = get_session_workspace(session_id)
    cancel_event = asyncio.Event()
    async with _sessions_lock:
        _ACTIVE_CANCELS[session_id] = cancel_event
    parts: List[str] = []
    gen_files: List[Dict[str, Any]] = []

    # Store original instruction for recovery
    original_instruction = session.get("original_instruction", user_message)
    if retry_count == 0:
        session["original_instruction"] = user_message
        # Track session start
        metrics_tracker = get_metrics_tracker()
        metrics_tracker.record_session_start(session_id, user_message)

    try:
        if _agent_semaphore is None:
            raise RuntimeError("Agent semaphore not initialized")
        async with _agent_semaphore:
            async for text, artifacts in run_analysis_stream(
                instruction=user_message,
                source_path=file_path_to_use,
                workspace_dir=str(session_workspace),
                api_key=DEEPSEEK_API_KEY,
                model_id=DEEPSEEK_MODEL_ID,
                api_base=DEEPSEEK_API_BASE,
                session_id=session_id,
                cancel_event=cancel_event,
            ):
                parts.append(text)
                if artifacts:
                    gen_files.extend(artifacts)
                    async with _sessions_lock:
                        session["artifacts"].extend(artifacts)
                        _save_sessions()
                yield text, artifacts
    finally:
        async with _sessions_lock:
            _ACTIVE_CANCELS.pop(session_id, None)
            _save_sessions()
        _save_sessions()

    output = "".join(parts) or "No output."
    failure = _structured_failure_from_output(output)

    if failure:
        # Track failure
        metrics_tracker = get_metrics_tracker()
        failure_code = failure.get("code")
        failure_message = failure.get("message")

        # Attempt automatic recovery
        recovery_executor = get_recovery_executor()
        recovery_result = await recovery_executor.attempt_recovery(
            session_id=session_id,
            failure_detail=failure,
            original_instruction=original_instruction,
            retry_count=retry_count,
        )

        if recovery_result and recovery_result.get("strategy") != "user_action_required":
            # Check if we should retry
            max_retries = recovery_result.get("max_retries", 0)
            current_retry = recovery_result.get("retry_count", 0)

            if current_retry <= max_retries:
                # Track recovery attempt
                metrics_tracker.record_recovery_attempt(session_id, success=False)

                # Automatic retry with modified instruction
                modified_instruction = recovery_result.get("modified_instruction", user_message)

                # Add recovery metadata to failure detail for logging
                failure["recovery_attempted"] = True
                failure["recovery_strategy"] = recovery_result.get("strategy")
                failure["retry_count"] = current_retry

                # Recursively retry, re-yielding its stream so the client sees
                # the recovery run live as well.
                try:
                    async for text, artifacts in _run_analysis_stream(
                        session_id=session_id,
                        session=session,
                        user_message=modified_instruction,
                        file_path_to_use=file_path_to_use,
                        retry_count=current_retry,
                    ):
                        yield text, artifacts
                    # Recovery succeeded
                    metrics_tracker.record_recovery_attempt(session_id, success=True)
                    return
                except Exception:
                    # Recovery failed, will raise below
                    pass

        # Track session end with failure
        metrics_tracker.record_session_end(
            session_id=session_id,
            status="failed",
            steps=_get_session_steps(session_id),
            failure_code=failure_code,
            failure_message=failure_message,
        )

        # If no recovery or recovery failed, raise the error
        raise AnalysisFailureError(failure)

    # Track successful session end
    metrics_tracker = get_metrics_tracker()
    metrics_tracker.record_session_end(
        session_id=session_id,
        status="success",
        steps=_get_session_steps(session_id),
    )
    # Streaming output has already been yielded above.


async def _run_analysis(session_id: str, session: Dict[str, Any], user_message: str, file_path_to_use: str, retry_count: int = 0):
    """Non-streaming wrapper that collects :func:`_run_analysis_stream` chunks.

    Returns ``(output, gen_files)`` and raises ``AnalysisFailureError`` on
    unrecoverable failure - identical to the historical behaviour, so the
    non-streaming chat-completions path and existing tests are unaffected.
    """
    output_parts: List[str] = []
    gen_files: List[Dict[str, Any]] = []
    async for text, artifacts in _run_analysis_stream(
        session_id=session_id,
        session=session,
        user_message=user_message,
        file_path_to_use=file_path_to_use,
        retry_count=retry_count,
    ):
        output_parts.append(text)
        if artifacts:
            gen_files.extend(artifacts)
    return "".join(output_parts) or "No output.", gen_files



# ── Pydantic models ───────────────────────────────────────────────────────────
class Message(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[Message]
    temperature: Optional[float] = 0.7
    stream: Optional[bool] = False
    session_id: Optional[str] = None
    file_path: Optional[str] = None
    user_friendly: Optional[bool] = False  # Enable user-friendly response format


class FileUploadResponse(BaseModel):
    id: str
    filename: str
    purpose: str
    bytes: int


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent_semaphore
    _agent_semaphore = asyncio.Semaphore(MAX_CONCURRENT_AGENTS)
    _load_sessions()
    _prune_stale_sessions()
    yield


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="LangGraph ReAct Data Analysis API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def rate_limit_middleware(request, call_next):
    """Per-IP rate limiter applied to all routes."""
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)
    return await call_next(request)


async def _startup():
    """Backward-compatible helper for tests."""
    global _agent_semaphore
    _agent_semaphore = asyncio.Semaphore(MAX_CONCURRENT_AGENTS)
    _load_sessions()
    _prune_stale_sessions()


@app.get("/health")
async def health_check():
    active = 0 if _agent_semaphore is None else MAX_CONCURRENT_AGENTS - _agent_semaphore._value
    return {
        "status": "ok",
        "sessions": len(SESSIONS),
        "concurrent_agents": active,
        "max_concurrent_agents": MAX_CONCURRENT_AGENTS,
    }


@app.post("/v1/files", response_model=FileUploadResponse)
async def upload_file_openai(file: UploadFile = File(...), purpose: str = "file-extract"):
    content = await file.read()
    max_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(content) / (1024*1024):.1f}MB). Limit: {MAX_UPLOAD_SIZE_MB}MB.",
        )
    file_id = str(uuid.uuid4())
    file_path = WORKSPACE_DIR / f"{file_id}_{file.filename}"
    file_path.write_bytes(content)
    return FileUploadResponse(id=file_id, filename=file.filename, purpose=purpose, bytes=len(content))


@app.post("/workspace/upload")
async def workspace_upload(file: UploadFile = File(...), session_id: Optional[str] = Form(None)):
    # session_id is sent by the frontend as a multipart form field (requests
    # `data={"session_id": ...}` alongside `files=`). Without Form() FastAPI
    # treats a bare `str` param as a query parameter, so the field was silently
    # ignored, the backend generated a UUID session, and the analysis request
    # (which sends the same session_id) ended up in a *different* session -
    # causing load_data to reject the uploaded file as cross-session.
    session_id, session = await get_or_create_session(session_id)
    session_workspace = get_session_workspace(session_id)
    content = await file.read()
    max_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(content) / (1024*1024):.1f}MB). Limit: {MAX_UPLOAD_SIZE_MB}MB.",
        )
    file_path = session_workspace / file.filename
    file_path.write_bytes(content)
    session.setdefault("files", []).append({
        "filename": file.filename,
        "path": str(file_path),
        "relative_path": f"{session_id}/{file.filename}",
        "size": len(content),
        "uploaded_at": datetime.now().isoformat(),
    })
    _save_sessions()
    return {
        "success": True,
        "filename": file.filename,
        "session_id": session_id,
        "file_path": str(file_path),
        "size": len(content),
    }


@app.get("/workspace/files")
async def list_workspace_files(session_id: Optional[str] = None):
    if session_id:
        s = await _check_session(session_id)
        return {"files": s.get("files", []), "artifacts": s.get("artifacts", [])}
    files = [
        {
            "filename": f.name,
            "size": f.stat().st_size,
            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
        }
        for f in WORKSPACE_DIR.glob("*")
        if f.is_file()
    ]
    return {"files": files}


@app.get("/workspace/files/{filename:path}")
async def download_workspace_file(filename: str):
    return _workspace_file_response(filename)


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str, purge: bool = False):
    # Validate existence first (outside lock — read-only check)
    if session_id not in SESSIONS:
        raise HTTPException(
            status_code=404,
            detail=_failure_detail("session_not_found", "Session not found", status_code=404, error_type="session_error"),
        )
    async with _sessions_lock:
        _cleanup_session(session_id, remove_workspace=purge)

    # Clear recovery history for this session
    recovery_executor = get_recovery_executor()
    recovery_executor.clear_history(session_id)

    return {"success": True, "message": f"Session {session_id} deleted", "purged": purge}


@app.get("/sessions/{session_id}/recovery_history")
async def get_recovery_history(session_id: str):
    """Get recovery history for a session."""
    await _check_session(session_id)
    recovery_executor = get_recovery_executor()
    history = recovery_executor.get_history(session_id)
    return {
        "session_id": session_id,
        "recovery_attempts": len(history),
        "history": history,
    }


@app.get("/metrics/stability")
async def get_stability_metrics():
    """Get aggregated stability metrics."""
    metrics_tracker = get_metrics_tracker()
    return metrics_tracker.get_aggregated_metrics()


@app.get("/metrics/failures")
async def get_failure_breakdown():
    """Get breakdown of failures by code."""
    metrics_tracker = get_metrics_tracker()
    return metrics_tracker.get_failure_breakdown()


@app.get("/metrics/stages")
async def get_stage_stats():
    """Get stage completion statistics."""
    metrics_tracker = get_metrics_tracker()
    return metrics_tracker.get_stage_completion_stats()


@app.get("/metrics/recent_sessions")
async def get_recent_sessions(limit: int = 10):
    """Get recent session metrics."""
    metrics_tracker = get_metrics_tracker()
    return {
        "sessions": metrics_tracker.get_recent_sessions(limit)
    }


@app.get("/sessions/{session_id}/metrics")
async def get_session_metrics(session_id: str):
    """Get metrics for a specific session."""
    await _check_session(session_id)
    metrics_tracker = get_metrics_tracker()
    session_metrics = metrics_tracker.get_session_metrics(session_id)
    if not session_metrics:
        raise HTTPException(status_code=404, detail="Metrics not found for this session")
    return session_metrics


@app.get("/metrics/dashboard")
async def get_dashboard_metrics():
    """Get comprehensive dashboard metrics for monitoring (cached 10s)."""
    cached = _get_cached("dashboard")
    if cached is not None:
        return cached

    metrics_tracker = get_metrics_tracker()

    # Gather all metrics
    aggregated = metrics_tracker.get_aggregated_metrics()
    stage_stats = metrics_tracker.get_stage_completion_stats()
    failure_breakdown = metrics_tracker.get_failure_breakdown()
    recent_sessions = metrics_tracker.get_recent_sessions(limit=10)

    result = {
        "overview": {
            "total_sessions": aggregated["total_sessions"],
            "success_rate": aggregated["success_rate"],
            "failure_rate": aggregated["failure_rate"],
            "avg_steps": aggregated["avg_steps_per_session"],
            "avg_duration": aggregated["avg_duration_seconds"],
            "recovery_success_rate": aggregated["recovery_success_rate"],
        },
        "detailed_metrics": aggregated,
        "stage_stats": stage_stats,
        "failure_breakdown": failure_breakdown,
        "recent_sessions": recent_sessions,
        "timestamp": datetime.now().isoformat(),
    }
    _set_cache("dashboard", result)
    return result


@app.get("/traces/{session_id}")
async def get_trace(session_id: str):
    """Get trace information for a session."""
    workspace_dir = WORKSPACE_DIR / session_id
    if not workspace_dir.exists():
        raise HTTPException(status_code=404, detail="Session not found")

    # Load trace from file
    trace = TraceContext.load_from_file(workspace_dir, session_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found for this session")

    return trace.to_dict()


@app.get("/traces/{session_id}/spans")
async def get_trace_spans(session_id: str, name: Optional[str] = None):
    """Get spans from a trace, optionally filtered by name."""
    workspace_dir = WORKSPACE_DIR / session_id
    if not workspace_dir.exists():
        raise HTTPException(status_code=404, detail="Session not found")

    trace = TraceContext.load_from_file(workspace_dir, session_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found for this session")

    if name:
        spans = trace.get_spans_by_name(name)
    else:
        spans = trace.spans

    return {
        "session_id": session_id,
        "trace_id": trace.trace_id,
        "total_spans": len(spans),
        "spans": [span.to_dict() for span in spans]
    }


@app.post("/sessions/{session_id}/cancel")
async def cancel_session(session_id: str):
    """Signal a running analysis to stop gracefully."""
    async with _sessions_lock:
        try:
            _ensure_session_consistency(session_id)
        except HTTPException:
            _cleanup_session(session_id, remove_workspace=True)
            raise
        event = _ACTIVE_CANCELS.get(session_id)
    if event is None:
        return {"success": False, "message": "No active analysis for this session", "error_type": "no_active_analysis"}
    event.set()
    async with _sessions_lock:
        _save_sessions()
    return {"success": True, "message": f"Cancellation requested for session {session_id}", "error_type": None}


@app.post("/sessions/{session_id}/resume")
async def resume_session(session_id: str):
    """Resume an interrupted analysis from the last saved state.

    The backend restores session state (findings, stage, step count) and
    re-launches the agent with a continuation prompt. The response streams
    back via SSE (same format as ``POST /v1/chat/completions`` with ``stream=true``).
    """
    if not DEEPSEEK_API_KEY:
        raise HTTPException(
            status_code=500,
            detail=_failure_detail(
                "missing_api_key",
                "DEEPSEEK_API_KEY not set",
                status_code=500,
                retryable=False,
                error_type="request_error",
            ),
        )

    # Validate session exists
    async with _sessions_lock:
        try:
            session = _ensure_session_consistency(session_id)
        except HTTPException:
            _cleanup_session(session_id, remove_workspace=True)
            raise

    workspace_dir = get_session_workspace(session_id)

    # Load saved state
    from langgraph_langchain.session_persistence import get_session_persistence
    persistence = get_session_persistence()
    restore_state = persistence.restore(workspace_dir)
    if restore_state is None:
        raise HTTPException(
            status_code=404,
            detail=_failure_detail(
                "session_not_found",
                "No resumable state found for this session",
                status_code=404,
                hint="Start a new analysis instead.",
                error_type="session_error",
            ),
        )

    # Resolve source file
    source_path = restore_state.get("source_path", "")
    if not source_path or not Path(source_path).exists():
        # Fallback to session's uploaded files
        try:
            source_path = _resolve_data_file(session, None)
        except HTTPException:
            raise HTTPException(
                status_code=400,
                detail=_failure_detail(
                    "missing_data_file",
                    "Source data file no longer exists",
                    status_code=400,
                    error_type="request_error",
                ),
            )

    instruction = restore_state.get("user_question", "Continue analysis")
    cancel_event = asyncio.Event()
    async with _sessions_lock:
        _ACTIVE_CANCELS[session_id] = cancel_event

    parts: List[str] = []
    gen_files: List[Dict[str, Any]] = []
    start_time = datetime.now()

    async def sse_resume_generator():
        try:
            if _agent_semaphore is None:
                raise RuntimeError("Agent semaphore not initialized")
            async with _agent_semaphore:
                async for text, artifacts in run_analysis_stream(
                    instruction=instruction,
                    source_path=source_path,
                    workspace_dir=str(workspace_dir),
                    api_key=DEEPSEEK_API_KEY,
                    model_id=DEEPSEEK_MODEL_ID,
                    api_base=DEEPSEEK_API_BASE,
                    session_id=session_id,
                    cancel_event=cancel_event,
                    restore_state=restore_state,
                ):
                    parts.append(text)
                    if artifacts:
                        gen_files.extend(artifacts)
                        async with _sessions_lock:
                            session["artifacts"].extend(artifacts)
                            _save_sessions()

                    payload = json.dumps(
                        {
                            "choices": [
                                {
                                    "delta": {"content": text},
                                    "index": 0,
                                    "finish_reason": None,
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                    yield f"data: {payload}\n\n"

            # Clear persisted state after successful resume
            persistence.clear(workspace_dir)

            # Final chunk
            yield f"data: {json.dumps({'choices': [{'delta': {'content': ''}, 'index': 0, 'finish_reason': 'stop'}]})}\n\n"
            yield "data: [DONE]\n\n"

        except AnalysisFailureError as exc:
            err_payload = json.dumps(
                {"error": {"message": str(exc.detail), "type": "analysis_error"}},
                ensure_ascii=False,
            )
            yield f"data: {err_payload}\n\n"
        except Exception as exc:
            err_payload = json.dumps(
                {"error": {"message": str(exc), "type": "internal_error"}},
                ensure_ascii=False,
            )
            yield f"data: {err_payload}\n\n"
        finally:
            async with _sessions_lock:
                _ACTIVE_CANCELS.pop(session_id, None)
                _save_sessions()

    return StreamingResponse(
        sse_resume_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Session-Id": session_id,
        },
    )


@app.get("/sessions/{session_id}/resumable")
async def check_resumable(session_id: str):
    """Check whether a session has resumable state."""
    workspace_dir = WORKSPACE_DIR / session_id
    if not workspace_dir.exists():
        raise HTTPException(status_code=404, detail="Session workspace not found")

    from langgraph_langchain.session_persistence import get_session_persistence
    persistence = get_session_persistence()
    can = persistence.can_resume(workspace_dir)
    state = persistence.restore(workspace_dir) if can else None

    return {
        "session_id": session_id,
        "resumable": can,
        "current_stage": state.get("current_stage") if state else None,
        "total_steps": state.get("total_steps") if state else None,
        "findings_count": len(state.get("findings", [])) if state else 0,
        "saved_at": state.get("saved_at") if state else None,
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    if not DEEPSEEK_API_KEY:
        raise HTTPException(
            status_code=500,
            detail=_failure_detail(
                "missing_api_key",
                "DEEPSEEK_API_KEY not set",
                status_code=500,
                retryable=False,
                error_type="request_error",
            ),
        )

    session_id, session = await get_or_create_session(request.session_id)
    session = await _check_session(session_id)

    user_message = next(
        (m.content for m in reversed(request.messages) if m.role == "user"), None
    )
    if not user_message:
        raise HTTPException(
            status_code=400,
            detail=_failure_detail(
                "missing_user_message",
                "No user message found",
                status_code=400,
                retryable=True,
                hint="Include at least one user-role message in the request.",
                error_type="request_error",
            ),
        )

    file_path_to_use = _resolve_data_file(session, request.file_path)

    # Create response transformer if user_friendly mode is enabled
    transformer = create_transformer(technical_mode=not request.user_friendly) if request.user_friendly else None
    start_time = datetime.now()

    if request.stream:
        async def sse_generator():
            # Signal cancellation when client disconnects mid-stream
            cancel_event = _ACTIVE_CANCELS.get(session_id)
            try:
                if transformer:
                    # user_friendly mode needs the full output before transforming
                    output, gen_files = await _run_analysis(session_id, session, user_message, file_path_to_use)
                    execution_time = (datetime.now() - start_time).total_seconds()
                    friendly_response = transformer.transform_analysis_response(
                        raw_output=output,
                        generated_files=gen_files,
                        session_id=session_id,
                        execution_time_seconds=execution_time,
                    )
                    payload: Dict[str, Any] = {
                        "choices": [{"delta": {"content": json.dumps(friendly_response, ensure_ascii=False)}}],
                    }
                    if gen_files:
                        payload["generated_files"] = gen_files
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                else:
                    # Stream chunks as they arrive so the client sees live
                    # progress (each tool step, LLM tokens, artifacts) instead
                    # of waiting for the whole analysis to finish before any
                    # output appears.
                    async for text, artifacts in _run_analysis_stream(
                        session_id, session, user_message, file_path_to_use
                    ):
                        payload: Dict[str, Any] = {
                            "choices": [{"delta": {"content": text}}],
                        }
                        if artifacts:
                            payload["generated_files"] = artifacts
                        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            except AnalysisFailureError as exc:
                if transformer:
                    friendly_error = transformer.transform_error_response(
                        error_detail=exc.detail,
                        session_id=session_id,
                    )
                    payload = {"error": friendly_error}
                else:
                    payload = {"error": exc.detail}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else _failure_detail(
                    "http_error",
                    str(exc.detail),
                    status_code=exc.status_code,
                    retryable=False,
                    error_type="request_error",
                )
                if transformer:
                    friendly_error = transformer.transform_error_response(
                        error_detail=detail,
                        session_id=session_id,
                    )
                    payload = {"error": friendly_error}
                else:
                    payload = {"error": detail}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            except asyncio.CancelledError:
                # Client disconnected — signal the running agent to stop
                if cancel_event:
                    cancel_event.set()
                cancel_detail = _failure_detail(
                    "cancelled",
                    "Analysis cancelled — client disconnected",
                    status_code=499,
                    retryable=True,
                    hint="Restart the analysis when ready.",
                    error_type="analysis_error",
                )
                # Don't yield — client is gone; just clean up
            except Exception as exc:
                error_detail = _failure_detail(
                    "agent_execution_error",
                    str(exc),
                    status_code=500,
                    retryable=False,
                    error_type="analysis_error",
                )
                if transformer:
                    friendly_error = transformer.transform_error_response(
                        error_detail=error_detail,
                        session_id=session_id,
                    )
                    payload = {"error": friendly_error}
                else:
                    payload = {"error": error_detail}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            finally:
                # Guarantee cleanup of cancel event regardless of exit path
                async with _sessions_lock:
                    _ACTIVE_CANCELS.pop(session_id, None)
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            sse_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        output, gen_files = await _run_analysis(session_id, session, user_message, file_path_to_use)
    except AnalysisFailureError as exc:
        if transformer:
            friendly_error = transformer.transform_error_response(
                error_detail=exc.detail,
                session_id=session_id,
            )
            raise HTTPException(status_code=exc.detail.get("status_code", 422), detail=friendly_error) from exc
        raise HTTPException(status_code=exc.detail.get("status_code", 422), detail=exc.detail) from exc
    except HTTPException:
        raise
    except Exception as exc:
        error_detail = _failure_detail(
            "agent_execution_error",
            str(exc),
            status_code=500,
            retryable=False,
            error_type="analysis_error",
        )
        if transformer:
            friendly_error = transformer.transform_error_response(
                error_detail=error_detail,
                session_id=session_id,
            )
            raise HTTPException(status_code=500, detail=friendly_error) from exc
        raise HTTPException(status_code=500, detail=error_detail) from exc

    # Transform response if user_friendly mode
    if transformer:
        execution_time = (datetime.now() - start_time).total_seconds()
        friendly_response = transformer.transform_analysis_response(
            raw_output=output,
            generated_files=gen_files,
            session_id=session_id,
            execution_time_seconds=execution_time,
        )
        return JSONResponse({
            "id": f"chatcmpl-{uuid.uuid4()}",
            "object": "chat.completion",
            "created": int(datetime.now().timestamp()),
            "model": request.model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(friendly_response, ensure_ascii=False)},
                "finish_reason": "stop",
            }],
        })

    return JSONResponse({
        "id": f"chatcmpl-{uuid.uuid4()}",
        "object": "chat.completion",
        "created": int(datetime.now().timestamp()),
        "model": request.model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": output},
            "finish_reason": "stop",
        }],
        "session_id": session_id,
        "generated_files": gen_files,
    })


if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("LangGraph ReAct Data Analysis API")
    print(f"Workspace : {WORKSPACE_DIR.absolute()}")
    print(f"Model     : {DEEPSEEK_MODEL_ID}")
    print(f"API Base  : {DEEPSEEK_API_BASE}")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8888)
