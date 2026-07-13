"""Tests for lineage tracking."""

import json
import tempfile
from pathlib import Path

import pytest

from langgraph_langchain.lineage import LineageTracker
from langgraph_langchain.schemas import (
    ArtifactRef,
    EvidenceItem,
    Finding,
)


class TestLineageTracker:
    """Test lineage tracking functionality."""

    def test_tracker_initialization(self):
        """Test tracker initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            tracker = LineageTracker(
                "test_session",
                workspace,
                request_id="req_123",
                run_id="run_456",
                user_question="Analyze sales data",
                data_source="sales.csv"
            )

            assert tracker.session_id == "test_session"
            assert tracker.request_id == "req_123"
            assert tracker.run_id == "run_456"
            assert tracker.user_question == "Analyze sales data"
            assert tracker.data_source == "sales.csv"

    def test_register_artifact(self):
        """Test artifact registration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            tracker = LineageTracker("test_session", workspace)
            tracker.set_current_context(step=1, tool_name="python_repl")

            artifact = tracker.register_artifact(
                "chart_001",
                "chart",
                file_path="sales_chart.png",
                description="Sales by region chart",
                span_id="span_123"
            )

            assert artifact.artifact_id == "chart_001"
            assert artifact.artifact_type == "chart"
            assert artifact.file_path == "sales_chart.png"
            assert artifact.created_by_tool == "python_repl"
            assert artifact.created_by_step == 1
            assert artifact.span_id == "span_123"
            assert artifact.created_at is not None

    def test_register_finding(self):
        """Test finding registration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            tracker = LineageTracker("test_session", workspace)

            # Register artifact first
            tracker.register_artifact("chart_001", "chart", file_path="chart.png")

            # Create finding with evidence referencing the artifact
            finding = Finding(
                finding_id="F001",
                statement="North region has highest sales",
                evidence=[
                    EvidenceItem(
                        evidence_text="North region: $420K (42% of total)",
                        source_fields=["region", "revenue"],
                        source_artifacts=["chart_001"],
                    )
                ],
            )

            tracker.register_finding(finding)

            assert "F001" in tracker.findings
            assert "chart_001" in tracker.artifacts
            assert "F001" in tracker.artifacts["chart_001"].referenced_by_findings

    def test_build_conclusion_trace(self):
        """Test building conclusion trace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            tracker = LineageTracker("test_session", workspace)
            tracker.set_current_context(step=5, tool_name="record_finding")

            # Register artifacts
            tracker.register_artifact("chart_001", "chart", file_path="chart1.png")
            tracker.register_artifact("table_001", "table", description="Summary table")

            # Register finding
            finding = Finding(
                finding_id="F001",
                statement="Sales increased by 25%",
                evidence=[
                    EvidenceItem(
                        evidence_text="Q3 sales: $1.25M vs Q2: $1.0M",
                        source_artifacts=["chart_001", "table_001"],
                    )
                ],
                source_tool="record_finding",
                source_step=5,
            )
            tracker.register_finding(finding)

            # Build trace
            trace = tracker.build_conclusion_trace("F001")

            assert trace is not None
            assert trace.finding_id == "F001"
            assert trace.finding_statement == "Sales increased by 25%"
            assert len(trace.evidence_items) == 1
            assert len(trace.artifacts) == 2
            assert 5 in trace.source_steps
            assert "Finding F001" in trace.trace_path

    def test_get_session_lineage(self):
        """Test getting complete session lineage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            tracker = LineageTracker(
                "test_session",
                workspace,
                user_question="Analyze data",
                data_source="data.csv"
            )

            # Register artifacts
            tracker.register_artifact("chart_001", "chart")
            tracker.register_artifact("chart_002", "chart")

            # Register findings
            finding1 = Finding(
                finding_id="F001",
                statement="Finding 1",
                evidence=[EvidenceItem(evidence_text="Evidence 1", source_artifacts=["chart_001"])],
            )
            finding2 = Finding(
                finding_id="F002",
                statement="Finding 2",
                evidence=[EvidenceItem(evidence_text="Evidence 2", source_artifacts=["chart_002"])],
            )
            tracker.register_finding(finding1)
            tracker.register_finding(finding2)

            # Get lineage
            lineage = tracker.get_session_lineage(
                final_report="# Analysis Report\n...",
                report_references_findings=["F001", "F002"]
            )

            assert lineage.session_id == "test_session"
            assert lineage.user_question == "Analyze data"
            assert lineage.data_source == "data.csv"
            assert len(lineage.artifacts) == 2
            assert len(lineage.findings) == 2
            assert len(lineage.conclusion_traces) == 2
            assert lineage.final_report == "# Analysis Report\n..."
            assert lineage.report_references_findings == ["F001", "F002"]

    def test_save_and_load_lineage(self):
        """Test saving and loading lineage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            tracker = LineageTracker("test_session", workspace)

            # Register some data
            tracker.register_artifact("chart_001", "chart")
            finding = Finding(
                finding_id="F001",
                statement="Test finding",
                evidence=[EvidenceItem(evidence_text="Test evidence")],
            )
            tracker.register_finding(finding)

            # Save lineage
            lineage_path = tracker.save_lineage(final_report="Test report")

            assert lineage_path.exists()

            # Load lineage
            loaded_lineage = LineageTracker.load_lineage(lineage_path)

            assert loaded_lineage.session_id == "test_session"
            assert len(loaded_lineage.artifacts) == 1
            assert len(loaded_lineage.findings) == 1
            assert loaded_lineage.final_report == "Test report"

    def test_get_artifacts_for_finding(self):
        """Test getting artifacts for a finding."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            tracker = LineageTracker("test_session", workspace)

            # Register artifacts
            tracker.register_artifact("chart_001", "chart")
            tracker.register_artifact("chart_002", "chart")
            tracker.register_artifact("chart_003", "chart")

            # Register finding referencing some artifacts
            finding = Finding(
                finding_id="F001",
                statement="Test",
                evidence=[
                    EvidenceItem(
                        evidence_text="Evidence",
                        source_artifacts=["chart_001", "chart_002"],
                    )
                ],
            )
            tracker.register_finding(finding)

            # Get artifacts
            artifacts = tracker.get_artifacts_for_finding("F001")

            assert len(artifacts) == 2
            assert any(a.artifact_id == "chart_001" for a in artifacts)
            assert any(a.artifact_id == "chart_002" for a in artifacts)

    def test_get_findings_for_artifact(self):
        """Test getting findings for an artifact."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            tracker = LineageTracker("test_session", workspace)

            # Register artifact
            tracker.register_artifact("chart_001", "chart")

            # Register findings referencing the artifact
            finding1 = Finding(
                finding_id="F001",
                statement="Finding 1",
                evidence=[EvidenceItem(evidence_text="E1", source_artifacts=["chart_001"])],
            )
            finding2 = Finding(
                finding_id="F002",
                statement="Finding 2",
                evidence=[EvidenceItem(evidence_text="E2", source_artifacts=["chart_001"])],
            )
            tracker.register_finding(finding1)
            tracker.register_finding(finding2)

            # Get findings
            findings = tracker.get_findings_for_artifact("chart_001")

            assert len(findings) == 2
            assert any(f.finding_id == "F001" for f in findings)
            assert any(f.finding_id == "F002" for f in findings)

    def test_get_lineage_summary(self):
        """Test getting lineage summary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            tracker = LineageTracker("test_session", workspace)

            # Register various artifacts
            tracker.register_artifact("chart_001", "chart")
            tracker.register_artifact("chart_002", "chart")
            tracker.register_artifact("table_001", "table")

            # Register findings
            finding1 = Finding(
                finding_id="F001",
                statement="Finding 1",
                category="trend",
                evidence=[EvidenceItem(evidence_text="E1", source_artifacts=["chart_001"])],
            )
            finding2 = Finding(
                finding_id="F002",
                statement="Finding 2",
                category="anomaly",
                evidence=[EvidenceItem(evidence_text="E2")],  # No artifacts
            )
            tracker.register_finding(finding1)
            tracker.register_finding(finding2)

            # Get summary
            summary = tracker.get_lineage_summary()

            assert summary["session_id"] == "test_session"
            assert summary["total_artifacts"] == 3
            assert summary["total_findings"] == 2
            assert summary["artifacts_by_type"]["chart"] == 2
            assert summary["artifacts_by_type"]["table"] == 1
            assert summary["findings_by_category"]["trend"] == 1
            assert summary["findings_by_category"]["anomaly"] == 1
            assert summary["artifacts_with_references"] == 1  # Only chart_001 is referenced
            assert summary["findings_with_artifacts"] == 1  # Only F001 has artifacts

    def test_set_current_context(self):
        """Test setting current execution context."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            tracker = LineageTracker("test_session", workspace)

            tracker.set_current_context(step=3, tool_name="python_repl")
            assert tracker.current_step == 3
            assert tracker.current_tool == "python_repl"

            # Register artifact with current context
            artifact = tracker.register_artifact("test_artifact", "chart")
            assert artifact.created_by_step == 3
            assert artifact.created_by_tool == "python_repl"
