"""Integration tests for evidence binding in record_finding tool.

These tests focus on the evidence binding validation logic by mocking
the stage validator to bypass state machine checks.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from langgraph_langchain.langgraph_agent import _make_tools, _Session
from langgraph_langchain.state_machine import AnalysisStateMachine


@pytest.fixture
def mock_stage_validator():
    """Mock ToolStageValidator to always allow tool calls."""
    with patch('langgraph_langchain.tool_validators.ToolStageValidator.validate_tool_call') as mock:
        mock.return_value = (True, None)
        yield mock


class TestEvidenceBindingIntegration:
    """Test evidence binding validation in the record_finding tool."""

    @pytest.fixture
    def session(self):
        """Create a test session."""
        workspace = Path(tempfile.mkdtemp())
        source_file = workspace / "test_data.csv"
        pd.DataFrame({"col1": [1, 2, 3]}).to_csv(source_file, index=False)

        session = _Session(
            workspace_dir=str(workspace),
            source_path=str(source_file),
            session_id="test_evidence_binding",
        )

        if not hasattr(session, 'state_machine'):
            session.state_machine = AnalysisStateMachine()

        return session

    @pytest.fixture
    def tools(self, session, mock_stage_validator):
        """Create agent tools with test session and mocked validator."""
        return _make_tools(session)

    def test_record_finding_without_evidence_fails(self, tools, session):
        """Recording finding without evidence should fail."""
        record_finding = next(t for t in tools if t.name == "record_finding")

        result = record_finding.invoke({
            "statement": "Revenue increased",
            "evidence_text": "",
        })

        assert "[ERROR]" in result
        assert "evidence_text is empty" in result.lower()

    def test_record_finding_with_valid_evidence_succeeds(self, tools, session):
        """Recording finding with proper evidence should succeed."""
        record_finding = next(t for t in tools if t.name == "record_finding")

        result = record_finding.invoke({
            "statement": "Revenue increased by 20%",
            "evidence_text": "Q3 revenue was $1.2M compared to Q2 revenue of $1.0M",
            "source_fields": ["revenue", "quarter"],
            "stats": {"q3_revenue": 1200000, "q2_revenue": 1000000},
        })

        assert "[ERROR]" not in result
        assert "F001" in result
        assert len(session.findings) == 1

    def test_record_finding_with_unknown_artifact_fails(self, tools, session):
        """Recording finding referencing non-existent artifact should fail."""
        record_finding = next(t for t in tools if t.name == "record_finding")

        result = record_finding.invoke({
            "statement": "Revenue trend shows growth",
            "evidence_text": "See revenue chart for details",
            "source_artifacts": ["nonexistent_chart.png"],
        })

        assert "[ERROR]" in result
        assert "unknown artifact" in result.lower()

    def test_record_finding_with_valid_artifact_succeeds(self, tools, session):
        """Recording finding with valid artifact reference should succeed."""
        # Register an artifact
        artifact = {
            "artifact_id": "revenue_chart.png",
            "artifact_type": "chart",
            "file_path": str(session.workspace_dir / "revenue_chart.png"),
        }
        session.new_artifacts.append(artifact)

        record_finding = next(t for t in tools if t.name == "record_finding")

        result = record_finding.invoke({
            "statement": "Revenue trend shows growth",
            "evidence_text": "Revenue increased from $1M to $1.2M as shown in the chart",
            "source_artifacts": ["revenue_chart.png"],
            "source_fields": ["revenue", "date"],
        })

        assert "[ERROR]" not in result
        assert "F001" in result
        assert len(session.findings) == 1

    def test_high_confidence_finding_requires_strong_evidence(self, tools, session):
        """High confidence finding with weak evidence should fail."""
        record_finding = next(t for t in tools if t.name == "record_finding")

        result = record_finding.invoke({
            "statement": "Revenue will definitely increase",
            "evidence_text": "Revenue looks good",
            "confidence_level": "high",
        })

        assert "[ERROR]" in result
        assert "high confidence but weak evidence" in result.lower()

    def test_causal_claim_requires_strong_evidence(self, tools, session):
        """Causal claim (level C) with weak evidence should fail."""
        record_finding = next(t for t in tools if t.name == "record_finding")

        result = record_finding.invoke({
            "statement": "Price increase caused revenue drop",
            "evidence_text": "Revenue dropped after price increase",
            "evidence_level": "C",
        })

        assert "[ERROR]" in result
        assert "causal claim" in result.lower()

    def test_completeness_warnings_for_incomplete_evidence(self, tools, session):
        """Should provide helpful warnings for incomplete evidence."""
        record_finding = next(t for t in tools if t.name == "record_finding")

        result = record_finding.invoke({
            "statement": "Revenue increased by 20%",
            "evidence_text": "Revenue increased significantly in Q3",
        })

        # Should succeed but with warnings
        assert "[ERROR]" not in result
        assert "F001" in result
        # Check for completeness feedback
        assert "[INFO]" in result or "completeness" in result.lower()
