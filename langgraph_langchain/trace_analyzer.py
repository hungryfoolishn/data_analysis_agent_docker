"""
P1.3: Trace analysis and diagnostics for performance and debugging.

This module provides tools to analyze trace data for:
- Performance bottlenecks
- Error patterns
- Tool usage statistics
- Stage progression analysis
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import statistics

from langgraph_langchain.tracing import TraceContext, Span


@dataclass
class PerformanceMetrics:
    """Performance metrics for a trace or span."""

    total_duration_ms: float
    tool_call_count: int
    error_count: int
    avg_tool_duration_ms: float
    slowest_tools: List[Tuple[str, float]]  # (tool_name, duration_ms)
    tool_usage_counts: Dict[str, int]
    stage_durations: Dict[str, float]


@dataclass
class ErrorAnalysis:
    """Error analysis for a trace."""

    total_errors: int
    error_by_tool: Dict[str, int]
    error_messages: List[Dict]  # {tool, message, timestamp}
    consecutive_errors: int
    error_rate: float  # errors / total_calls


class TraceAnalyzer:
    """Analyzes trace data for insights and diagnostics."""

    def __init__(self, trace_context: TraceContext):
        self.trace = trace_context

    def get_performance_metrics(self) -> PerformanceMetrics:
        """Calculate performance metrics from trace."""
        tool_spans = [s for s in self.trace.spans if s.name in [
            "load_data", "python_repl", "eda_profile", "record_finding", "finish_report"
        ]]

        total_duration = 0.0
        if self.trace.end_time and self.trace.start_time:
            delta = self.trace.end_time - self.trace.start_time
            total_duration = delta.total_seconds() * 1000

        tool_durations = [s.duration_ms() for s in tool_spans if s.end_time]
        avg_duration = statistics.mean(tool_durations) if tool_durations else 0.0

        # Find slowest tools
        tool_with_duration = [(s.name, s.duration_ms()) for s in tool_spans if s.end_time]
        slowest = sorted(tool_with_duration, key=lambda x: x[1], reverse=True)[:5]

        # Count tool usage
        tool_counts = {}
        for span in tool_spans:
            tool_counts[span.name] = tool_counts.get(span.name, 0) + 1

        # Calculate stage durations
        stage_durations = {}
        stage_spans = [s for s in self.trace.spans if "stage" in s.attributes]
        for span in stage_spans:
            stage = span.attributes.get("stage")
            if stage and span.end_time:
                stage_durations[stage] = stage_durations.get(stage, 0) + span.duration_ms()

        # Count errors
        error_count = len([s for s in self.trace.spans if s.status == "failed"])

        return PerformanceMetrics(
            total_duration_ms=total_duration,
            tool_call_count=len(tool_spans),
            error_count=error_count,
            avg_tool_duration_ms=avg_duration,
            slowest_tools=slowest,
            tool_usage_counts=tool_counts,
            stage_durations=stage_durations,
        )

    def get_error_analysis(self) -> ErrorAnalysis:
        """Analyze errors in the trace."""
        error_spans = [s for s in self.trace.spans if s.status == "failed"]

        error_by_tool = {}
        error_messages = []

        for span in error_spans:
            tool_name = span.name
            error_by_tool[tool_name] = error_by_tool.get(tool_name, 0) + 1

            error_messages.append({
                "tool": tool_name,
                "message": span.error_message or "Unknown error",
                "timestamp": span.end_time.isoformat() if span.end_time else None,
                "span_id": span.span_id,
            })

        # Calculate consecutive errors
        consecutive = 0
        max_consecutive = 0
        for span in sorted(self.trace.spans, key=lambda s: s.start_time):
            if span.status == "failed":
                consecutive += 1
                max_consecutive = max(max_consecutive, consecutive)
            else:
                consecutive = 0

        # Calculate error rate
        total_calls = len([s for s in self.trace.spans if s.name in [
            "load_data", "python_repl", "eda_profile", "record_finding", "finish_report"
        ]])
        error_rate = len(error_spans) / total_calls if total_calls > 0 else 0.0

        return ErrorAnalysis(
            total_errors=len(error_spans),
            error_by_tool=error_by_tool,
            error_messages=error_messages,
            consecutive_errors=max_consecutive,
            error_rate=error_rate,
        )

    def get_tool_timeline(self) -> List[Dict]:
        """Get chronological timeline of tool calls."""
        tool_spans = [s for s in self.trace.spans if s.name in [
            "load_data", "python_repl", "eda_profile", "record_finding", "finish_report"
        ]]

        timeline = []
        for span in sorted(tool_spans, key=lambda s: s.start_time):
            timeline.append({
                "tool": span.name,
                "start": span.start_time.isoformat(),
                "end": span.end_time.isoformat() if span.end_time else None,
                "duration_ms": span.duration_ms(),
                "status": span.status,
                "attributes": span.attributes,
            })

        return timeline

    def get_stage_progression(self) -> List[Dict]:
        """Get stage progression timeline."""
        stage_spans = [s for s in self.trace.spans if "stage" in s.attributes]

        progression = []
        for span in sorted(stage_spans, key=lambda s: s.start_time):
            progression.append({
                "stage": span.attributes.get("stage"),
                "start": span.start_time.isoformat(),
                "end": span.end_time.isoformat() if span.end_time else None,
                "duration_ms": span.duration_ms(),
            })

        return progression

    def diagnose_issues(self) -> List[str]:
        """Diagnose potential issues in the trace."""
        issues = []

        metrics = self.get_performance_metrics()
        errors = self.get_error_analysis()

        # Check for high error rate
        if errors.error_rate > 0.3:
            issues.append(f"High error rate: {errors.error_rate:.1%} of tool calls failed")

        # Check for consecutive errors
        if errors.consecutive_errors >= 3:
            issues.append(f"Consecutive errors detected: {errors.consecutive_errors} in a row")

        # Check for slow tools
        if metrics.slowest_tools:
            slowest_name, slowest_duration = metrics.slowest_tools[0]
            if slowest_duration > 30000:  # 30 seconds
                issues.append(f"Slow tool detected: {slowest_name} took {slowest_duration/1000:.1f}s")

        # Check for excessive tool calls
        if metrics.tool_call_count > 50:
            issues.append(f"High tool usage: {metrics.tool_call_count} tool calls (may indicate inefficiency)")

        # Check for python_repl dominance
        python_repl_count = metrics.tool_usage_counts.get("python_repl", 0)
        if python_repl_count > metrics.tool_call_count * 0.7:
            issues.append(f"Heavy python_repl usage: {python_repl_count} calls ({python_repl_count/metrics.tool_call_count:.1%})")

        return issues

    def generate_summary_report(self) -> str:
        """Generate a human-readable summary report."""
        metrics = self.get_performance_metrics()
        errors = self.get_error_analysis()
        issues = self.diagnose_issues()

        lines = [
            "=== Trace Analysis Summary ===",
            f"Trace ID: {self.trace.trace_id}",
            f"Session ID: {self.trace.session_id}",
            f"Instruction: {self.trace.instruction[:100]}...",
            "",
            "Performance Metrics:",
            f"  Total Duration: {metrics.total_duration_ms/1000:.2f}s",
            f"  Tool Calls: {metrics.tool_call_count}",
            f"  Avg Tool Duration: {metrics.avg_tool_duration_ms:.0f}ms",
            "",
            "Tool Usage:",
        ]

        for tool, count in sorted(metrics.tool_usage_counts.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  {tool}: {count} calls")

        lines.extend([
            "",
            "Slowest Tools:",
        ])

        for tool, duration in metrics.slowest_tools[:3]:
            lines.append(f"  {tool}: {duration/1000:.2f}s")

        if errors.total_errors > 0:
            lines.extend([
                "",
                "Error Analysis:",
                f"  Total Errors: {errors.total_errors}",
                f"  Error Rate: {errors.error_rate:.1%}",
                f"  Max Consecutive Errors: {errors.consecutive_errors}",
            ])

            if errors.error_by_tool:
                lines.append("  Errors by Tool:")
                for tool, count in sorted(errors.error_by_tool.items(), key=lambda x: x[1], reverse=True):
                    lines.append(f"    {tool}: {count}")

        if issues:
            lines.extend([
                "",
                "⚠️  Issues Detected:",
            ])
            for issue in issues:
                lines.append(f"  - {issue}")

        return "\n".join(lines)

    def export_for_dashboard(self) -> Dict:
        """Export trace data for monitoring dashboard."""
        metrics = self.get_performance_metrics()
        errors = self.get_error_analysis()

        return {
            "trace_id": self.trace.trace_id,
            "session_id": self.trace.session_id,
            "instruction": self.trace.instruction,
            "start_time": self.trace.start_time.isoformat(),
            "end_time": self.trace.end_time.isoformat() if self.trace.end_time else None,
            "metrics": {
                "total_duration_ms": metrics.total_duration_ms,
                "tool_call_count": metrics.tool_call_count,
                "error_count": metrics.error_count,
                "avg_tool_duration_ms": metrics.avg_tool_duration_ms,
                "error_rate": errors.error_rate,
            },
            "tool_usage": metrics.tool_usage_counts,
            "slowest_tools": [
                {"tool": tool, "duration_ms": duration}
                for tool, duration in metrics.slowest_tools
            ],
            "stage_durations": metrics.stage_durations,
            "errors": errors.error_messages,
            "issues": self.diagnose_issues(),
        }


def compare_traces(trace1: TraceContext, trace2: TraceContext) -> Dict:
    """Compare two traces to identify differences."""
    analyzer1 = TraceAnalyzer(trace1)
    analyzer2 = TraceAnalyzer(trace2)

    metrics1 = analyzer1.get_performance_metrics()
    metrics2 = analyzer2.get_performance_metrics()

    return {
        "trace1_id": trace1.trace_id,
        "trace2_id": trace2.trace_id,
        "duration_diff_ms": metrics2.total_duration_ms - metrics1.total_duration_ms,
        "duration_diff_pct": (
            (metrics2.total_duration_ms - metrics1.total_duration_ms) / metrics1.total_duration_ms * 100
            if metrics1.total_duration_ms > 0 else 0
        ),
        "tool_call_diff": metrics2.tool_call_count - metrics1.tool_call_count,
        "error_diff": metrics2.error_count - metrics1.error_count,
        "tool_usage_diff": {
            tool: metrics2.tool_usage_counts.get(tool, 0) - metrics1.tool_usage_counts.get(tool, 0)
            for tool in set(list(metrics1.tool_usage_counts.keys()) + list(metrics2.tool_usage_counts.keys()))
        },
    }
