"""Structured logging for analysis operations."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from langgraph_langchain.tracing import TraceContext

from langgraph_langchain.schemas import (
    AnalysisStage,
    FailureCode,
    StructuredLogEntry,
    RunMetrics,
)


class StructuredLogger:
    """Structured logger that writes both human-readable and machine-readable logs."""

    def __init__(
        self,
        workspace_dir: Path,
        session_id: str,
        request_id: Optional[str] = None,
        run_id: Optional[str] = None,
        trace_context: Optional["TraceContext"] = None,
    ):
        self.workspace_dir = workspace_dir
        self.session_id = session_id
        self.request_id = request_id or session_id
        self.run_id = run_id or f"run_{int(time.time() * 1000)}"
        self.trace_context = trace_context

        # Human-readable logger
        self.logger = self._create_human_logger()

        # Structured log file
        self.structured_log_path = workspace_dir / f"structured_log_{session_id}.jsonl"

        # Metrics tracking
        self.start_time = datetime.now(timezone.utc).isoformat()
        self.stage_start_times: Dict[str, float] = {}
        self.tool_call_counts: Dict[str, int] = {}
        self.total_tokens = 0
        self.total_cost = 0.0
        self.failure_count = 0
        self.retry_count = 0

    def _create_human_logger(self) -> logging.Logger:
        """Create a human-readable logger."""
        logger = logging.getLogger(f"agent.{self.session_id}")
        if logger.handlers:
            return logger
        logger.setLevel(logging.DEBUG)
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        # Console handler
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)

        # File handler
        log_path = self.workspace_dir / f"agent_{self.session_id}.log"
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        logger.propagate = False
        return logger

    def _write_structured_log(self, entry: StructuredLogEntry) -> None:
        """Write a structured log entry to JSONL file."""
        try:
            with open(self.structured_log_path, "a", encoding="utf-8") as f:
                f.write(entry.model_dump_json() + "\n")
        except Exception as e:
            self.logger.error(f"Failed to write structured log: {e}")

    def log(
        self,
        level: str,
        event_type: str,
        message: str,
        *,
        stage: Optional[AnalysisStage] = None,
        tool_name: Optional[str] = None,
        failure_code: Optional[FailureCode] = None,
        retry_count: Optional[int] = None,
        duration_ms: Optional[float] = None,
        token_count: Optional[int] = None,
        cost_usd: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a structured event."""
        # Write human-readable log
        log_method = getattr(self.logger, level.lower(), self.logger.info)
        log_method(message)

        # Get trace_id and span_id from trace_context
        trace_id = None
        span_id = None
        if self.trace_context:
            trace_id = self.trace_context.trace_id
            if self.trace_context.current_span:
                span_id = self.trace_context.current_span.span_id

        # Write structured log
        entry = StructuredLogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            session_id=self.session_id,
            request_id=self.request_id,
            run_id=self.run_id,
            trace_id=trace_id,
            span_id=span_id,
            level=level.upper(),
            event_type=event_type,
            stage=stage,
            tool_name=tool_name,
            failure_code=failure_code,
            retry_count=retry_count,
            duration_ms=duration_ms,
            token_count=token_count,
            cost_usd=cost_usd,
            message=message,
            metadata=metadata or {},
        )
        self._write_structured_log(entry)

        # Update metrics
        if token_count:
            self.total_tokens += token_count
        if cost_usd:
            self.total_cost += cost_usd
        if failure_code:
            self.failure_count += 1
        if retry_count:
            self.retry_count += retry_count

    def log_stage_start(self, stage: AnalysisStage) -> None:
        """Log stage start."""
        self.stage_start_times[stage] = time.time()
        self.log(
            "INFO",
            "stage_start",
            f"Stage started: {stage}",
            stage=stage,
        )

    def log_stage_complete(self, stage: AnalysisStage) -> None:
        """Log stage completion."""
        duration_ms = None
        if stage in self.stage_start_times:
            duration_ms = (time.time() - self.stage_start_times[stage]) * 1000

        self.log(
            "INFO",
            "stage_complete",
            f"Stage completed: {stage}",
            stage=stage,
            duration_ms=duration_ms,
        )

    def log_stage_fail(
        self,
        stage: AnalysisStage,
        failure_code: FailureCode,
        message: str,
        retryable: bool = False,
    ) -> None:
        """Log stage failure."""
        duration_ms = None
        if stage in self.stage_start_times:
            duration_ms = (time.time() - self.stage_start_times[stage]) * 1000

        self.log(
            "ERROR",
            "stage_fail",
            f"Stage failed: {stage} - {message}",
            stage=stage,
            failure_code=failure_code,
            duration_ms=duration_ms,
            metadata={"retryable": retryable},
        )

    def log_tool_call(
        self,
        tool_name: str,
        stage: Optional[AnalysisStage] = None,
        duration_ms: Optional[float] = None,
        success: bool = True,
        error: Optional[str] = None,
    ) -> None:
        """Log tool call."""
        self.tool_call_counts[tool_name] = self.tool_call_counts.get(tool_name, 0) + 1

        level = "INFO" if success else "ERROR"
        event_type = "tool_call_success" if success else "tool_call_error"
        message = f"Tool call: {tool_name}"
        if error:
            message += f" - Error: {error}"

        self.log(
            level,
            event_type,
            message,
            stage=stage,
            tool_name=tool_name,
            duration_ms=duration_ms,
            metadata={"success": success, "error": error} if error else {"success": success},
        )

    def log_report_rejection(
        self,
        reason: str,
        stage: Optional[AnalysisStage] = None,
    ) -> None:
        """Log report rejection."""
        self.log(
            "WARNING",
            "report_rejected",
            f"Report rejected: {reason}",
            stage=stage,
            failure_code="report_rejected",
            metadata={"rejection_reason": reason},
        )

    def get_run_metrics(
        self,
        final_status: str,
        total_steps: int,
        failure_code: Optional[FailureCode] = None,
    ) -> RunMetrics:
        """Get run-level metrics."""
        end_time = datetime.now(timezone.utc).isoformat()
        start_dt = datetime.fromisoformat(self.start_time)
        end_dt = datetime.fromisoformat(end_time)
        duration_seconds = (end_dt - start_dt).total_seconds()

        # Calculate stage durations
        stage_durations = {}
        for stage, start_time in self.stage_start_times.items():
            # If stage is still running, use current time
            stage_durations[stage] = time.time() - start_time

        return RunMetrics(
            session_id=self.session_id,
            request_id=self.request_id,
            run_id=self.run_id,
            start_time=self.start_time,
            end_time=end_time,
            duration_seconds=duration_seconds,
            total_steps=total_steps,
            total_tokens=self.total_tokens if self.total_tokens > 0 else None,
            total_cost_usd=self.total_cost if self.total_cost > 0 else None,
            stage_durations=stage_durations,
            tool_call_counts=self.tool_call_counts,
            failure_count=self.failure_count,
            retry_count=self.retry_count,
            final_status=final_status,
            failure_code=failure_code,
        )

    def save_run_metrics(
        self,
        final_status: str,
        total_steps: int,
        failure_code: Optional[FailureCode] = None,
    ) -> None:
        """Save run metrics to file."""
        metrics = self.get_run_metrics(final_status, total_steps, failure_code)
        metrics_path = self.workspace_dir / f"run_metrics_{self.session_id}.json"

        try:
            with open(metrics_path, "w", encoding="utf-8") as f:
                f.write(metrics.model_dump_json(indent=2))
            self.logger.info(f"Run metrics saved to {metrics_path}")
        except Exception as e:
            self.logger.error(f"Failed to save run metrics: {e}")

    def debug(self, message: str, **kwargs) -> None:
        """Log debug message."""
        self.log("DEBUG", "debug", message, **kwargs)

    def info(self, message: str, **kwargs) -> None:
        """Log info message."""
        self.log("INFO", "info", message, **kwargs)

    def warning(self, message: str, **kwargs) -> None:
        """Log warning message."""
        self.log("WARNING", "warning", message, **kwargs)

    def error(self, message: str, **kwargs) -> None:
        """Log error message."""
        self.log("ERROR", "error", message, **kwargs)

    def set_trace_context(self, trace_context: "TraceContext") -> None:
        """Set the trace context for automatic trace_id/span_id association."""
        self.trace_context = trace_context

    def log_span_start(self, span_name: str, **attributes) -> None:
        """Log span start event."""
        self.log(
            "DEBUG",
            "span_start",
            f"Span started: {span_name}",
            metadata={"span_name": span_name, **attributes},
        )

    def log_span_end(self, span_id: str, status: str, **attributes) -> None:
        """Log span end event."""
        self.log(
            "DEBUG",
            "span_end",
            f"Span ended: {span_id} ({status})",
            metadata={"span_id": span_id, "status": status, **attributes},
        )
