"""
Tests for P1.3 lineage tracking system.
"""

import pytest
from pathlib import Path
from langgraph_langchain.lineage_tracker import (
    LineageNode,
    LineageGraph,
    LineageTracker,
    build_lineage_from_session,
)
from langgraph_langchain.schemas import Finding, EvidenceItem


class TestLineageGraph:
    """Test lineage graph construction and queries."""

    def test_add_node(self):
        """Test adding nodes to graph."""
        graph = LineageGraph(session_id="test_session")

        node = LineageNode(
            node_id="data_source:test.csv",
            node_type="data_source",
            name="test.csv",
        )
        graph.add_node(node)

        assert "data_source:test.csv" in graph.nodes
        assert graph.nodes["data_source:test.csv"].name == "test.csv"

    def test_add_edge(self):
        """Test adding edges between nodes."""
        graph = LineageGraph(session_id="test_session")

        node1 = LineageNode(node_id="node1", node_type="data_source", name="source")
        node2 = LineageNode(node_id="node2", node_type="tool_call", name="tool")

        graph.add_node(node1)
        graph.add_node(node2)
        graph.add_edge("node1", "node2")

        assert "node2" in graph.nodes["node1"].children
        assert "node1" in graph.nodes["node2"].parents

    def test_get_ancestors(self):
        """Test getting all ancestors of a node."""
        graph = LineageGraph(session_id="test_session")

        # Create chain: data_source -> tool -> evidence -> finding
        nodes = [
            LineageNode(node_id="data", node_type="data_source", name="data"),
            LineageNode(node_id="tool", node_type="tool_call", name="tool"),
            LineageNode(node_id="evidence", node_type="evidence", name="evidence"),
            LineageNode(node_id="finding", node_type="finding", name="finding"),
        ]

        for node in nodes:
            graph.add_node(node)

        graph.add_edge("data", "tool")
        graph.add_edge("tool", "evidence")
        graph.add_edge("evidence", "finding")

        ancestors = graph.get_ancestors("finding")
        assert len(ancestors) == 3
        assert "data" in ancestors
        assert "tool" in ancestors
        assert "evidence" in ancestors

    def test_get_descendants(self):
        """Test getting all descendants of a node."""
        graph = LineageGraph(session_id="test_session")

        nodes = [
            LineageNode(node_id="data", node_type="data_source", name="data"),
            LineageNode(node_id="tool", node_type="tool_call", name="tool"),
            LineageNode(node_id="evidence", node_type="evidence", name="evidence"),
        ]

        for node in nodes:
            graph.add_node(node)

        graph.add_edge("data", "tool")
        graph.add_edge("tool", "evidence")

        descendants = graph.get_descendants("data")
        assert len(descendants) == 2
        assert "tool" in descendants
        assert "evidence" in descendants

    def test_get_lineage_path(self):
        """Test getting paths from finding to data sources."""
        graph = LineageGraph(session_id="test_session")

        # Create simple chain
        nodes = [
            LineageNode(node_id="data", node_type="data_source", name="data"),
            LineageNode(node_id="tool", node_type="tool_call", name="tool"),
            LineageNode(node_id="evidence", node_type="evidence", name="evidence"),
            LineageNode(node_id="finding", node_type="finding", name="finding"),
        ]

        for node in nodes:
            graph.add_node(node)

        graph.add_edge("data", "tool")
        graph.add_edge("tool", "evidence")
        graph.add_edge("evidence", "finding")

        paths = graph.get_lineage_path("finding")
        assert len(paths) > 0
        # Path should go from finding back to data
        assert "finding" in paths[0]
        assert "data" in paths[0]


class TestLineageTracker:
    """Test lineage tracker functionality."""

    def test_track_data_load(self):
        """Test tracking data source loading."""
        tracker = LineageTracker(session_id="test_session")

        node_id = tracker.track_data_load(
            file_path="/data/test.csv",
            span_id="span_123"
        )

        assert node_id in tracker.graph.nodes
        assert tracker.graph.nodes[node_id].node_type == "data_source"
        assert tracker.graph.nodes[node_id].attributes["span_id"] == "span_123"

    def test_track_tool_call(self):
        """Test tracking tool calls."""
        tracker = LineageTracker(session_id="test_session")

        data_id = tracker.track_data_load("/data/test.csv")
        tool_id = tracker.track_tool_call(
            tool_name="python_repl",
            span_id="span_456",
            inputs={"code": "df.describe()"},
            outputs={"result": "stats"},
            parent_ids=[data_id]
        )

        assert tool_id in tracker.graph.nodes
        assert tracker.graph.nodes[tool_id].node_type == "tool_call"
        assert data_id in tracker.graph.nodes[tool_id].parents

    def test_track_artifact(self):
        """Test tracking artifact creation."""
        tracker = LineageTracker(session_id="test_session")

        tool_id = tracker.track_tool_call(
            tool_name="python_repl",
            span_id="span_456",
            inputs={},
            outputs={}
        )

        artifact_id = tracker.track_artifact(
            artifact_id="chart.png",
            artifact_type="chart",
            file_path="/workspace/chart.png",
            span_id="span_789",
            parent_tool_id=tool_id
        )

        assert artifact_id in tracker.graph.nodes
        assert tracker.graph.nodes[artifact_id].node_type == "artifact"
        assert tool_id in tracker.graph.nodes[artifact_id].parents

    def test_track_evidence(self):
        """Test tracking evidence items."""
        tracker = LineageTracker(session_id="test_session")

        artifact_id = tracker.track_artifact(
            artifact_id="chart.png",
            artifact_type="chart",
            file_path="/workspace/chart.png"
        )

        evidence = EvidenceItem(
            evidence_text="Revenue increased by 20%",
            source_fields=["revenue"],
            source_artifacts=["chart.png"],
            span_id="span_evidence"
        )

        evidence_id = tracker.track_evidence(
            evidence=evidence,
            artifact_ids=["chart.png"]
        )

        assert evidence_id in tracker.graph.nodes
        assert tracker.graph.nodes[evidence_id].node_type == "evidence"

    def test_track_finding(self):
        """Test tracking findings."""
        tracker = LineageTracker(session_id="test_session")

        evidence = EvidenceItem(
            evidence_text="Revenue increased by 20%",
            source_fields=["revenue"],
            span_id="span_evidence"
        )

        evidence_id = tracker.track_evidence(evidence)

        finding = Finding(
            finding_id="F001",
            statement="Revenue shows strong growth",
            evidence=[evidence],
            confidence_level="high",
            evidence_level="A"
        )

        finding_id = tracker.track_finding(finding, [evidence_id])

        assert finding_id in tracker.graph.nodes
        assert tracker.graph.nodes[finding_id].node_type == "finding"
        assert evidence_id in tracker.graph.nodes[finding_id].parents

    def test_validate_finding_lineage_complete(self):
        """Test validating complete finding lineage."""
        tracker = LineageTracker(session_id="test_session")

        # Build complete lineage chain
        data_id = tracker.track_data_load("/data/test.csv")
        tool_id = tracker.track_tool_call(
            tool_name="python_repl",
            span_id="span_tool",
            inputs={},
            outputs={},
            parent_ids=[data_id]
        )

        evidence = EvidenceItem(
            evidence_text="Test evidence",
            source_fields=["field1"],
            span_id="span_evidence"
        )
        evidence_id = tracker.track_evidence(evidence, parent_tool_id=tool_id)

        finding = Finding(
            finding_id="F001",
            statement="Test finding",
            evidence=[evidence],
            confidence_level="high",
            evidence_level="A"
        )
        tracker.track_finding(finding, [evidence_id])

        is_valid, errors = tracker.validate_finding_lineage("F001")
        assert is_valid
        assert len(errors) == 0

    def test_validate_finding_lineage_incomplete(self):
        """Test validating incomplete finding lineage."""
        tracker = LineageTracker(session_id="test_session")

        # Create finding without proper lineage
        evidence = EvidenceItem(
            evidence_text="Test evidence",
            source_fields=["field1"],
            span_id="span_evidence"
        )
        evidence_id = tracker.track_evidence(evidence)

        finding = Finding(
            finding_id="F001",
            statement="Test finding",
            evidence=[evidence],
            confidence_level="high",
            evidence_level="A"
        )
        tracker.track_finding(finding, [evidence_id])

        is_valid, errors = tracker.validate_finding_lineage("F001")
        assert not is_valid
        assert len(errors) > 0

    def test_get_finding_lineage_summary(self):
        """Test getting finding lineage summary."""
        tracker = LineageTracker(session_id="test_session")

        # Build complete lineage
        data_id = tracker.track_data_load("/data/test.csv")
        tool_id = tracker.track_tool_call(
            tool_name="python_repl",
            span_id="span_tool",
            inputs={},
            outputs={},
            parent_ids=[data_id]
        )

        evidence = EvidenceItem(
            evidence_text="Test evidence",
            source_fields=["field1"],
            span_id="span_evidence"
        )
        evidence_id = tracker.track_evidence(evidence, parent_tool_id=tool_id)

        finding = Finding(
            finding_id="F001",
            statement="Test finding",
            evidence=[evidence],
            confidence_level="high",
            evidence_level="A"
        )
        tracker.track_finding(finding, [evidence_id])

        summary = tracker.get_finding_lineage_summary("F001")

        assert summary["finding_id"] == "F001"
        assert summary["total_ancestors"] > 0
        assert "data_source" in summary["type_counts"]
        assert summary["is_complete"]

    def test_export_for_visualization(self):
        """Test exporting lineage for visualization."""
        tracker = LineageTracker(session_id="test_session")

        data_id = tracker.track_data_load("/data/test.csv")
        tool_id = tracker.track_tool_call(
            tool_name="python_repl",
            span_id="span_tool",
            inputs={},
            outputs={},
            parent_ids=[data_id]
        )

        export = tracker.export_for_visualization()

        assert "nodes" in export
        assert "edges" in export
        assert len(export["nodes"]) == 2
        assert len(export["edges"]) == 1


class TestBuildLineageFromSession:
    """Test building lineage from session data."""

    def test_build_lineage_from_findings(self):
        """Test building lineage graph from findings and artifacts."""
        artifacts = [
            {
                "artifact_id": "chart1.png",
                "artifact_type": "chart",
                "file_path": "/workspace/chart1.png",
                "span_id": "span_chart1"
            }
        ]

        evidence = EvidenceItem(
            evidence_text="Revenue increased by 20%",
            source_fields=["revenue"],
            source_artifacts=["chart1.png"],
            span_id="span_evidence"
        )

        finding = Finding(
            finding_id="F001",
            statement="Revenue shows growth",
            evidence=[evidence],
            confidence_level="high",
            evidence_level="A"
        )

        tracker = build_lineage_from_session(
            session_id="test_session",
            findings=[finding],
            artifacts=artifacts
        )

        assert "artifact:chart1.png" in tracker.graph.nodes
        assert "finding:F001" in tracker.graph.nodes

        # Check that artifact is linked to evidence
        finding_node = tracker.graph.nodes["finding:F001"]
        assert len(finding_node.parents) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
