"""
Observability: Tracing module for tracking analysis execution.

This module provides distributed tracing capabilities to track:
- Which tool generated which finding
- The execution path of the analysis
- Timing information for each step
- Relationships between findings and evidence
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
import json
import uuid


@dataclass
class Span:
    """Represents a single unit of work in the trace."""

    span_id: str
    trace_id: str
    name: str
    start_time: datetime
    end_time: Optional[datetime] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    parent_span_id: Optional[str] = None
    status: str = "running"  # running, completed, failed
    error_message: Optional[str] = None

    def end(self, status: str = "completed", error_message: Optional[str] = None):
        """End the span."""
        self.end_time = datetime.now()
        self.status = status
        self.error_message = error_message

    def set_attribute(self, key: str, value: Any):
        """Set an attribute on the span."""
        self.attributes[key] = value

    def duration_ms(self) -> float:
        """Get the duration in milliseconds."""
        if self.end_time is None:
            return 0.0
        delta = self.end_time - self.start_time
        return delta.total_seconds() * 1000

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "name": self.name,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_ms": self.duration_ms(),
            "attributes": self.attributes,
            "parent_span_id": self.parent_span_id,
            "status": self.status,
            "error_message": self.error_message,
        }


class TraceContext:
    """Context for tracking a complete analysis trace."""

    def __init__(self, session_id: str, instruction: str):
        self.trace_id = str(uuid.uuid4())
        self.session_id = session_id
        self.instruction = instruction
        self.spans: List[Span] = []
        self.current_span: Optional[Span] = None
        self.start_time = datetime.now()
        self.end_time: Optional[datetime] = None

    def create_span(
        self,
        name: str,
        attributes: Optional[Dict[str, Any]] = None,
        parent_span_id: Optional[str] = None
    ) -> Span:
        """Create a new span."""
        span = Span(
            span_id=str(uuid.uuid4()),
            trace_id=self.trace_id,
            name=name,
            start_time=datetime.now(),
            attributes=attributes or {},
            parent_span_id=parent_span_id or (self.current_span.span_id if self.current_span else None)
        )
        self.spans.append(span)
        return span

    def start_span(
        self,
        name: str,
        attributes: Optional[Dict[str, Any]] = None
    ) -> Span:
        """Start a new span and set it as current."""
        span = self.create_span(name, attributes)
        self.current_span = span
        return span

    def end_current_span(self, status: str = "completed", error_message: Optional[str] = None):
        """End the current span."""
        if self.current_span:
            self.current_span.end(status, error_message)
            self.current_span = None

    def end_span(self, span_id: str, status: str = "completed", **kwargs):
        """End a specific span by ID."""
        span = self.get_span_by_id(span_id)
        if span:
            span.end(status, kwargs.get("error_message"))
            # Add other kwargs as attributes
            for key, value in kwargs.items():
                if key != "error_message":
                    span.set_attribute(key, value)

    def end_trace(self):
        """End the trace."""
        self.end_time = datetime.now()
        # End any open spans
        for span in self.spans:
            if span.end_time is None:
                span.end()

    def get_span_by_id(self, span_id: str) -> Optional[Span]:
        """Get a span by its ID."""
        for span in self.spans:
            if span.span_id == span_id:
                return span
        return None

    def get_spans_by_name(self, name: str) -> List[Span]:
        """Get all spans with a given name."""
        return [span for span in self.spans if span.name == name]

    def get_root_spans(self) -> List[Span]:
        """Get all root spans (no parent)."""
        return [span for span in self.spans if span.parent_span_id is None]

    def get_child_spans(self, parent_span_id: str) -> List[Span]:
        """Get all child spans of a parent."""
        return [span for span in self.spans if span.parent_span_id == parent_span_id]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "instruction": self.instruction,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_ms": (self.end_time - self.start_time).total_seconds() * 1000 if self.end_time else 0,
            "spans": [span.to_dict() for span in self.spans],
            "total_spans": len(self.spans),
        }

    def save_to_file(self, workspace_dir: Path):
        """Save trace to a JSON file."""
        trace_file = workspace_dir / f".trace_{self.session_id}.json"
        with open(trace_file, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load_from_file(cls, workspace_dir: Path, session_id: str) -> Optional["TraceContext"]:
        """Load trace from a JSON file."""
        trace_file = workspace_dir / f".trace_{session_id}.json"
        if not trace_file.exists():
            return None

        with open(trace_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Reconstruct trace context
        trace = cls(session_id=data["session_id"], instruction=data["instruction"])
        trace.trace_id = data["trace_id"]
        trace.start_time = datetime.fromisoformat(data["start_time"])
        if data["end_time"]:
            trace.end_time = datetime.fromisoformat(data["end_time"])

        # Reconstruct spans
        trace.spans = []
        for span_data in data["spans"]:
            span = Span(
                span_id=span_data["span_id"],
                trace_id=span_data["trace_id"],
                name=span_data["name"],
                start_time=datetime.fromisoformat(span_data["start_time"]),
                end_time=datetime.fromisoformat(span_data["end_time"]) if span_data["end_time"] else None,
                attributes=span_data["attributes"],
                parent_span_id=span_data["parent_span_id"],
                status=span_data["status"],
                error_message=span_data["error_message"],
            )
            trace.spans.append(span)

        return trace


class SpanContext:
    """Context manager for automatic span lifecycle management."""

    def __init__(self, trace_context: TraceContext, name: str, attributes: Optional[Dict[str, Any]] = None):
        self.trace_context = trace_context
        self.name = name
        self.attributes = attributes or {}
        self.span: Optional[Span] = None

    def __enter__(self) -> Span:
        """Start the span."""
        self.span = self.trace_context.start_span(self.name, self.attributes)
        return self.span

    def __exit__(self, exc_type, exc_val, exc_tb):
        """End the span."""
        if exc_type is not None:
            # Exception occurred
            self.trace_context.end_current_span(
                status="failed",
                error_message=f"{exc_type.__name__}: {exc_val}"
            )
        else:
            self.trace_context.end_current_span(status="completed")
        return False  # Don't suppress exceptions


# Global trace context storage (per session)
_trace_contexts: Dict[str, TraceContext] = {}


def get_trace_context(session_id: str) -> Optional[TraceContext]:
    """Get the trace context for a session."""
    return _trace_contexts.get(session_id)


def create_trace_context(session_id: str, instruction: str) -> TraceContext:
    """Create a new trace context for a session."""
    trace_context = TraceContext(session_id, instruction)
    _trace_contexts[session_id] = trace_context
    return trace_context


def remove_trace_context(session_id: str):
    """Remove the trace context for a session."""
    if session_id in _trace_contexts:
        del _trace_contexts[session_id]
