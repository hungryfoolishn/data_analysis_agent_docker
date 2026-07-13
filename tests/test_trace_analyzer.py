"""
Tests for P1.3 trace analyzer.
"""

import pytest
from datetime import datetime, timedelta
from langgraph_langchain.tracing import TraceContext, Span
from langgraph_langchain.trace_analyzer import (
    TraceAnalyzer,
    PerformanceMetrics,
    ErrorAnalysis,
    compare_traces,
)


class TestTraceAnalyzer:
    """Test trace analyzer functionality."""

    def test_get_performance_metrics(self):
        """Test calculating performance metrics."""
        trace = TraceContext(session_id="test_session", instruction="Test analysis")

        # Add some tool spans
        span1 = trace.start_span("load_data", attributes={"file": "test.csv"})
        trace.end_current_span(status="completed")

        span2 = trace.start_span("python_repl", attributes={"code": "df.describe()"})
        trace.end_current_span(status="completed")

        span3 = trace.start_span("record_finding", attributes={"statement": "Test"})
        trace.end_current_span(status="completed")

        trace.end_trace()

        analyzer = TraceAnalyzer(trace)
        metrics = analyzer.get_performance_metrics()

        assert metrics.tool_call_count == 3
        assert metrics.error_count == 0
        assert "load_data" in metrics.tool_usage_counts
        assert "python_repl" in metrics.tool_usage_counts
        assert "record_finding" in metrics.tool_usage_counts

    def test_get_error_analysis(self):
        """Test error analysis."""
        trace = TraceContext(session_id="test_session", instruction="Test analysis")

        # Add successful span
        span1 = trace.start_span("load_data")
        trace.end_current_span(status="completed")

        # Add failed span
        span2 = trace.start_span("python_repl")
        trace.end_current_span(status="failed", error_message="SyntaxError: invalid syntax")

        # Add another failed span
        span3 = trace.start_span("python_repl")
        trace.end_current_span(status="failed", error_message="NameError: name 'x' is not defined")

        analyzer = TraceAnalyzer(trace)
        errors = analyzer.get_error_analysis()

        assert errors.total_errors == 2
        assert "python_repl" in errors.error_by_tool
        assert errors.error_by_tool["python_repl"] == 2
        assert errors.consecutive_errors == 2
        assert errors.error_rate == 2/3  # 2 errors out of 3 tool calls

    def test_get_tool_timeline(self):
        """Test getting tool timeline."""
        trace = TraceContext(session_id="test_session", instruction="Test analysis")

        span1 = trace.start_span("load_data")
        trace.end_current_span(status="completed")

        span2 = trace.start_span("python_repl")
        trace.end_current_span(status="completed")

        analyzer = TraceAnalyzer(trace)
        timeline = analyzer.get_tool_timeline()

        assert len(timeline) == 2
        assert timeline[0]["tool"] == "load_data"
        assert timeline[1]["tool"] == "python_repl"
        assert "start" in timeline[0]
        assert "end" in timeline[0]
        assert "duration_ms" in timeline[0]

    def test_diagnose_issues_high_error_rate(self):
        """Test diagnosing high error rate."""
        trace = TraceContext(session_id="test_session", instruction="Test analysis")

        # Add 2 successful and 3 failed spans (60% error rate)
        for i in range(2):
            span = trace.start_span("python_repl")
            trace.end_current_span(status="completed")

        for i in range(3):
            span = trace.start_span("python_repl")
            trace.end_current_span(status="failed", error_message="Error")

        analyzer = TraceAnalyzer(trace)
        issues = analyzer.diagnose_issues()

        assert len(issues) > 0
        assert any("error rate" in issue.lower() for issue in issues)

    def test_diagnose_issues_consecutive_errors(self):
        """Test diagnosing consecutive errors."""
        trace = TraceContext(session_id="test_session", instruction="Test analysis")

        # Add 3 consecutive failed spans
        for i in range(3):
            span = trace.start_span("python_repl")
            trace.end_current_span(status="failed", error_message="Error")

        analyzer = TraceAnalyzer(trace)
        issues = analyzer.diagnose_issues()

        assert len(issues) > 0
        assert any("consecutive" in issue.lower() for issue in issues)

    def test_diagnose_issues_slow_tool(self):
        """Test diagnosing slow tools."""
        trace = TraceContext(session_id="test_session", instruction="Test analysis")

        # Create a slow span (simulate by setting times manually)
        span = Span(
            span_id="span_1",
            trace_id=trace.trace_id,
            name="python_repl",
            start_time=datetime.now(),
        )
        span.end_time = span.start_time + timedelta(seconds=35)  # 35 seconds
        span.status = "completed"
        trace.spans.append(span)

        analyzer = TraceAnalyzer(trace)
        issues = analyzer.diagnose_issues()

        assert len(issues) > 0
        assert any("slow" in issue.lower() for issue in issues)

    def test_diagnose_issues_excessive_tool_calls(self):
        """Test diagnosing excessive tool calls."""
        trace = TraceContext(session_id="test_session", instruction="Test analysis")

        # Add 60 tool calls
        for i in range(60):
            span = trace.start_span("python_repl")
            trace.end_current_span(status="completed")

        analyzer = TraceAnalyzer(trace)
        issues = analyzer.diagnose_issues()

        assert len(issues) > 0
        assert any("tool usage" in issue.lower() or "tool calls" in issue.lower() for issue in issues)

    def test_generate_summary_report(self):
        """Test generating summary report."""
        trace = TraceContext(session_id="test_session", instruction="Test analysis")

        span1 = trace.start_span("load_data")
        trace.end_current_span(status="completed")

        span2 = trace.start_span("python_repl")
        trace.end_current_span(status="completed")

        trace.end_trace()

        analyzer = TraceAnalyzer(trace)
        report = analyzer.generate_summary_report()

        assert "Trace Analysis Summary" in report
        assert "test_session" in report
        assert "Performance Metrics" in report
        assert "Tool Usage" in report

    def test_export_for_dashboard(self):
        """Test exporting for dashboard."""
        trace = TraceContext(session_id="test_session", instruction="Test analysis")

        span1 = trace.start_span("load_data")
        trace.end_current_span(status="completed")

        span2 = trace.start_span("python_repl")
        trace.end_current_span(status="failed", error_message="Error")

        trace.end_trace()

        analyzer = TraceAnalyzer(trace)
        dashboard_data = analyzer.export_for_dashboard()

        assert "trace_id" in dashboard_data
        assert "session_id" in dashboard_data
        assert "metrics" in dashboard_data
        assert "tool_usage" in dashboard_data
        assert "errors" in dashboard_data
        assert "issues" in dashboard_data

        assert dashboard_data["metrics"]["tool_call_count"] == 2
        assert dashboard_data["metrics"]["error_count"] == 1


class TestCompareTraces:
    """Test trace comparison functionality."""

    def test_compare_traces_basic(self):
        """Test basic trace comparison."""
        trace1 = TraceContext(session_id="session1", instruction="Test 1")
        span1 = trace1.start_span("load_data")
        trace1.end_current_span(status="completed")
        span2 = trace1.start_span("python_repl")
        trace1.end_current_span(status="completed")
        trace1.end_trace()

        trace2 = TraceContext(session_id="session2", instruction="Test 2")
        span1 = trace2.start_span("load_data")
        trace2.end_current_span(status="completed")
        span2 = trace2.start_span("python_repl")
        trace2.end_current_span(status="completed")
        span3 = trace2.start_span("python_repl")
        trace2.end_current_span(status="completed")
        trace2.end_trace()

        comparison = compare_traces(trace1, trace2)

        assert "trace1_id" in comparison
        assert "trace2_id" in comparison
        assert "tool_call_diff" in comparison
        assert comparison["tool_call_diff"] == 1  # trace2 has 1 more tool call

    def test_compare_traces_with_errors(self):
        """Test comparing traces with different error counts."""
        trace1 = TraceContext(session_id="session1", instruction="Test 1")
        span1 = trace1.start_span("python_repl")
        trace1.end_current_span(status="completed")
        trace1.end_trace()

        trace2 = TraceContext(session_id="session2", instruction="Test 2")
        span1 = trace2.start_span("python_repl")
        trace2.end_current_span(status="failed", error_message="Error")
        trace2.end_trace()

        comparison = compare_traces(trace1, trace2)

        assert comparison["error_diff"] == 1  # trace2 has 1 more error


class TestPerformanceMetrics:
    """Test performance metrics data structure."""

    def test_performance_metrics_creation(self):
        """Test creating performance metrics."""
        metrics = PerformanceMetrics(
            total_duration_ms=5000.0,
            tool_call_count=10,
            error_count=2,
            avg_tool_duration_ms=500.0,
            slowest_tools=[("python_repl", 1500.0), ("load_data", 1000.0)],
            tool_usage_counts={"python_repl": 7, "load_data": 3},
            stage_durations={"deep_dive": 3000.0, "basic_eda": 2000.0}
        )

        assert metrics.total_duration_ms == 5000.0
        assert metrics.tool_call_count == 10
        assert metrics.error_count == 2
        assert len(metrics.slowest_tools) == 2
        assert metrics.tool_usage_counts["python_repl"] == 7


class TestErrorAnalysis:
    """Test error analysis data structure."""

    def test_error_analysis_creation(self):
        """Test creating error analysis."""
        analysis = ErrorAnalysis(
            total_errors=3,
            error_by_tool={"python_repl": 2, "load_data": 1},
            error_messages=[
                {"tool": "python_repl", "message": "SyntaxError", "timestamp": "2024-01-01T00:00:00"},
                {"tool": "python_repl", "message": "NameError", "timestamp": "2024-01-01T00:01:00"},
                {"tool": "load_data", "message": "FileNotFoundError", "timestamp": "2024-01-01T00:02:00"},
            ],
            consecutive_errors=2,
            error_rate=0.3
        )

        assert analysis.total_errors == 3
        assert analysis.error_by_tool["python_repl"] == 2
        assert len(analysis.error_messages) == 3
        assert analysis.consecutive_errors == 2
        assert analysis.error_rate == 0.3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
