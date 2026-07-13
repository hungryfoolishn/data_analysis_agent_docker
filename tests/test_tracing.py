"""
Test tracing functionality
"""
import pytest
import json
from pathlib import Path
from datetime import datetime
from langgraph_langchain.tracing import TraceContext, Span, get_trace_context, create_trace_context, remove_trace_context


def test_trace_span_creation():
    """Test creating a trace span"""
    span = Span(
        span_id="span_1",
        trace_id="trace_123",
        name="load_data",
        start_time=datetime.now(),
        parent_span_id=None,
        attributes={"file_path": "/data/test.csv"}
    )

    assert span.span_id == "span_1"
    assert span.parent_span_id is None
    assert span.name == "load_data"
    assert span.attributes["file_path"] == "/data/test.csv"
    assert span.start_time is not None
    assert span.end_time is None
    assert span.status == "running"


def test_trace_context_lifecycle():
    """Test trace context creation and span management"""
    trace_ctx = TraceContext(session_id="session_456", instruction="Analyze sales data")

    assert trace_ctx.session_id == "session_456"
    assert trace_ctx.instruction == "Analyze sales data"
    assert len(trace_ctx.spans) == 0

    # Start root span
    span1 = trace_ctx.start_span("analysis_session", attributes={"source": "test"})
    assert len(trace_ctx.spans) == 1
    assert trace_ctx.current_span == span1

    # Start child span
    span2 = trace_ctx.start_span("load_data", attributes={"file_path": "/data/sales.csv"})
    assert len(trace_ctx.spans) == 2

    # End current span
    trace_ctx.end_current_span(status="completed")
    assert span2.status == "completed"
    assert span2.end_time is not None


def test_trace_context_create_span():
    """Test creating spans with explicit parent"""
    trace_ctx = TraceContext(session_id="session_456", instruction="Test")

    root = trace_ctx.create_span("root")
    child1 = trace_ctx.create_span("child1", parent_span_id=root.span_id)
    child2 = trace_ctx.create_span("child2", parent_span_id=root.span_id)

    assert len(trace_ctx.spans) == 3
    assert child1.parent_span_id == root.span_id
    assert child2.parent_span_id == root.span_id


def test_trace_context_get_spans():
    """Test querying spans"""
    trace_ctx = TraceContext(session_id="session_456", instruction="Test")

    root = trace_ctx.create_span("root")
    child1 = trace_ctx.create_span("load_data", parent_span_id=root.span_id)
    child2 = trace_ctx.create_span("load_data", parent_span_id=root.span_id)

    # Get by name
    load_spans = trace_ctx.get_spans_by_name("load_data")
    assert len(load_spans) == 2

    # Get root spans
    roots = trace_ctx.get_root_spans()
    assert len(roots) == 1
    assert roots[0].span_id == root.span_id

    # Get child spans
    children = trace_ctx.get_child_spans(root.span_id)
    assert len(children) == 2


def test_trace_context_save_and_load(tmp_path):
    """Test saving and loading trace context"""
    trace_ctx = TraceContext(session_id="session_456", instruction="Analyze sales")

    root = trace_ctx.start_span("analysis_session")
    trace_ctx.end_current_span(status="completed")

    child = trace_ctx.start_span("load_data", attributes={"rows": 1000})
    trace_ctx.end_current_span(status="completed")

    trace_ctx.end_trace()

    # Save to file
    trace_ctx.save_to_file(tmp_path)

    # Check file exists
    trace_file = tmp_path / f".trace_{trace_ctx.session_id}.json"
    assert trace_file.exists()

    # Load and verify
    with open(trace_file, "r") as f:
        data = json.load(f)

    assert data["session_id"] == "session_456"
    assert data["instruction"] == "Analyze sales"
    assert len(data["spans"]) == 2
    assert data["spans"][0]["name"] == "analysis_session"
    assert data["spans"][1]["name"] == "load_data"


def test_global_trace_context_registry():
    """Test global trace context registry"""
    # Create and register trace context
    trace_ctx = create_trace_context(session_id="session_456", instruction="Test")

    # Retrieve it
    retrieved = get_trace_context("session_456")
    assert retrieved is not None
    assert retrieved.session_id == "session_456"

    # Remove it
    remove_trace_context("session_456")
    assert get_trace_context("session_456") is None


def test_span_with_error():
    """Test span error handling"""
    trace_ctx = TraceContext(session_id="session_456", instruction="Test")

    span = trace_ctx.start_span("python_repl", attributes={"code": "1/0"})
    trace_ctx.end_current_span(status="failed", error_message="ZeroDivisionError: division by zero")

    assert span.status == "failed"
    assert "ZeroDivisionError" in span.error_message


def test_trace_context_end_trace():
    """Test ending the entire trace"""
    trace_ctx = TraceContext(session_id="session_456", instruction="Test")

    span1 = trace_ctx.start_span("analysis_session")
    span2 = trace_ctx.start_span("load_data")
    # Don't end span2

    # End trace (should end all open spans)
    trace_ctx.end_trace()

    assert span1.end_time is not None
    assert span2.end_time is not None
    assert trace_ctx.end_time is not None


def test_span_duration():
    """Test span duration calculation"""
    span = Span(
        span_id="span_1",
        trace_id="trace_123",
        name="test",
        start_time=datetime.now()
    )

    # Duration should be 0 when not ended
    assert span.duration_ms() == 0.0

    # End span and check duration
    span.end()
    assert span.duration_ms() > 0.0


def test_span_set_attribute():
    """Test setting span attributes"""
    span = Span(
        span_id="span_1",
        trace_id="trace_123",
        name="test",
        start_time=datetime.now()
    )

    span.set_attribute("key1", "value1")
    span.set_attribute("key2", 123)

    assert span.attributes["key1"] == "value1"
    assert span.attributes["key2"] == 123


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
