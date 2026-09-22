"""Tests for structured logging."""

import json
import tempfile
from pathlib import Path

import pytest

from langgraph_langchain.structured_logging import StructuredLogger


class TestStructuredLogger:
    """Test structured logging functionality."""

    def test_logger_initialization(self):
        """Test logger initialization."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            workspace = Path(tmpdir)
            logger = StructuredLogger(workspace, "test_session", "test_request", "test_run")

            assert logger.session_id == "test_session"
            assert logger.request_id == "test_request"
            assert logger.run_id == "test_run"
            assert logger.workspace_dir == workspace

    def test_basic_logging(self):
        """Test basic logging functionality."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            workspace = Path(tmpdir)
            logger = StructuredLogger(workspace, "test_session")

            logger.info("Test message", stage="schema_understanding", tool_name="load_data")

            # Check structured log file exists
            log_file = workspace / "structured_log_test_session.jsonl"
            assert log_file.exists()

            # Read and verify log entry
            with open(log_file, "r") as f:
                line = f.readline()
                entry = json.loads(line)

                assert entry["session_id"] == "test_session"
                assert entry["level"] == "INFO"
                assert entry["event_type"] == "info"
                assert entry["message"] == "Test message"
                assert entry["stage"] == "schema_understanding"
                assert entry["tool_name"] == "load_data"

    def test_stage_logging(self):
        """Test stage start/complete/fail logging."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            workspace = Path(tmpdir)
            logger = StructuredLogger(workspace, "test_session")

            # Log stage start
            logger.log_stage_start("schema_understanding")
            assert "schema_understanding" in logger.stage_start_times

            # Log stage complete
            logger.log_stage_complete("schema_understanding")

            # Log stage fail
            logger.log_stage_fail("deep_dive", "python_execution_error", "Test error")

            # Verify structured logs
            log_file = workspace / "structured_log_test_session.jsonl"
            with open(log_file, "r") as f:
                lines = f.readlines()
                assert len(lines) == 3

                # Check stage_start
                entry1 = json.loads(lines[0])
                assert entry1["event_type"] == "stage_start"
                assert entry1["stage"] == "schema_understanding"

                # Check stage_complete
                entry2 = json.loads(lines[1])
                assert entry2["event_type"] == "stage_complete"
                assert entry2["stage"] == "schema_understanding"
                assert entry2["duration_ms"] is not None

                # Check stage_fail
                entry3 = json.loads(lines[2])
                assert entry3["event_type"] == "stage_fail"
                assert entry3["stage"] == "deep_dive"
                assert entry3["failure_code"] == "python_execution_error"

    def test_tool_call_logging(self):
        """Test tool call logging."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            workspace = Path(tmpdir)
            logger = StructuredLogger(workspace, "test_session")

            # Log successful tool call
            logger.log_tool_call("load_data", stage="schema_understanding", duration_ms=1500.0)
            assert logger.tool_call_counts["load_data"] == 1

            # Log failed tool call
            logger.log_tool_call(
                "python_repl",
                stage="deep_dive",
                duration_ms=500.0,
                success=False,
                error="Syntax error"
            )
            assert logger.tool_call_counts["python_repl"] == 1

            # Verify structured logs
            log_file = workspace / "structured_log_test_session.jsonl"
            with open(log_file, "r") as f:
                lines = f.readlines()
                assert len(lines) == 2

                # Check successful call
                entry1 = json.loads(lines[0])
                assert entry1["event_type"] == "tool_call_success"
                assert entry1["tool_name"] == "load_data"
                assert entry1["duration_ms"] == 1500.0

                # Check failed call
                entry2 = json.loads(lines[1])
                assert entry2["event_type"] == "tool_call_error"
                assert entry2["tool_name"] == "python_repl"
                assert entry2["metadata"]["error"] == "Syntax error"

    def test_report_rejection_logging(self):
        """Test report rejection logging."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            workspace = Path(tmpdir)
            logger = StructuredLogger(workspace, "test_session")

            logger.log_report_rejection("Missing findings", stage="report_generation")

            # Verify structured log
            log_file = workspace / "structured_log_test_session.jsonl"
            with open(log_file, "r") as f:
                entry = json.loads(f.readline())

                assert entry["event_type"] == "report_rejected"
                assert entry["failure_code"] == "report_rejected"
                assert entry["metadata"]["rejection_reason"] == "Missing findings"

    def test_metrics_tracking(self):
        """Test metrics tracking."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            workspace = Path(tmpdir)
            logger = StructuredLogger(workspace, "test_session")

            # Log some events with metrics
            logger.log("INFO", "llm_call", "LLM call", token_count=1000, cost_usd=0.01)
            logger.log("INFO", "llm_call", "LLM call", token_count=500, cost_usd=0.005)
            logger.log("ERROR", "error", "Error occurred", failure_code="python_execution_error")

            # Check accumulated metrics
            assert logger.total_tokens == 1500
            assert logger.total_cost == 0.015
            assert logger.failure_count == 1

    def test_run_metrics_generation(self):
        """Test run metrics generation."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            workspace = Path(tmpdir)
            logger = StructuredLogger(workspace, "test_session", "req_123", "run_456")

            # Simulate some activity
            logger.log_stage_start("schema_understanding")
            logger.log_tool_call("load_data", stage="schema_understanding")
            logger.log_tool_call("eda_profile", stage="schema_understanding")
            logger.log_stage_complete("schema_understanding")

            logger.log("INFO", "llm_call", "LLM call", token_count=2000, cost_usd=0.02)

            # Get run metrics
            metrics = logger.get_run_metrics("completed", total_steps=5)

            assert metrics.session_id == "test_session"
            assert metrics.request_id == "req_123"
            assert metrics.run_id == "run_456"
            assert metrics.total_steps == 5
            assert metrics.total_tokens == 2000
            assert metrics.total_cost_usd == 0.02
            assert metrics.tool_call_counts["load_data"] == 1
            assert metrics.tool_call_counts["eda_profile"] == 1
            assert metrics.final_status == "completed"
            assert "schema_understanding" in metrics.stage_durations

    def test_save_run_metrics(self):
        """Test saving run metrics to file."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            workspace = Path(tmpdir)
            logger = StructuredLogger(workspace, "test_session")

            logger.log_tool_call("load_data")
            logger.save_run_metrics("completed", total_steps=3)

            # Check metrics file exists
            metrics_file = workspace / "run_metrics_test_session.json"
            assert metrics_file.exists()

            # Verify metrics content
            with open(metrics_file, "r") as f:
                metrics = json.load(f)

                assert metrics["session_id"] == "test_session"
                assert metrics["total_steps"] == 3
                assert metrics["final_status"] == "completed"
                assert metrics["tool_call_counts"]["load_data"] == 1

    def test_convenience_methods(self):
        """Test convenience logging methods."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            workspace = Path(tmpdir)
            logger = StructuredLogger(workspace, "test_session")

            logger.debug("Debug message")
            logger.info("Info message")
            logger.warning("Warning message")
            logger.error("Error message")

            # Verify all messages logged
            log_file = workspace / "structured_log_test_session.jsonl"
            with open(log_file, "r") as f:
                lines = f.readlines()
                assert len(lines) == 4

                levels = [json.loads(line)["level"] for line in lines]
                assert levels == ["DEBUG", "INFO", "WARNING", "ERROR"]

    def test_metadata_logging(self):
        """Test logging with custom metadata."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            workspace = Path(tmpdir)
            logger = StructuredLogger(workspace, "test_session")

            metadata = {
                "user_id": "user_123",
                "data_size": 10000,
                "custom_field": "custom_value"
            }

            logger.info("Test with metadata", metadata=metadata)

            # Verify metadata in log
            log_file = workspace / "structured_log_test_session.jsonl"
            with open(log_file, "r") as f:
                entry = json.loads(f.readline())

                assert entry["metadata"]["user_id"] == "user_123"
                assert entry["metadata"]["data_size"] == 10000
                assert entry["metadata"]["custom_field"] == "custom_value"
