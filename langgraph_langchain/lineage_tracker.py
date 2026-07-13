"""
P1.3: Full lineage tracking system for data analysis workflow.

This module provides comprehensive lineage tracking from findings back to:
- Evidence items
- Tool calls that generated the evidence
- Data sources and transformations
- Artifacts (charts, tables, etc.)

Key capabilities:
1. Build complete lineage graph for any finding
2. Validate lineage completeness
3. Query and analyze lineage relationships
4. Export lineage for visualization
"""

from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from pathlib import Path
import json

from langgraph_langchain.schemas import Finding, EvidenceItem
from langgraph_langchain.tracing import TraceContext


@dataclass
class LineageNode:
    """Represents a node in the lineage graph."""

    node_id: str
    node_type: str  # finding, evidence, tool_call, artifact, data_source
    name: str
    attributes: Dict = field(default_factory=dict)
    children: List[str] = field(default_factory=list)  # IDs of child nodes
    parents: List[str] = field(default_factory=list)  # IDs of parent nodes


@dataclass
class LineageGraph:
    """Complete lineage graph for an analysis session."""

    session_id: str
    nodes: Dict[str, LineageNode] = field(default_factory=dict)

    def add_node(self, node: LineageNode):
        """Add a node to the graph."""
        self.nodes[node.node_id] = node

    def add_edge(self, parent_id: str, child_id: str):
        """Add an edge between two nodes."""
        if parent_id in self.nodes and child_id in self.nodes:
            if child_id not in self.nodes[parent_id].children:
                self.nodes[parent_id].children.append(child_id)
            if parent_id not in self.nodes[child_id].parents:
                self.nodes[child_id].parents.append(parent_id)

    def get_ancestors(self, node_id: str) -> Set[str]:
        """Get all ancestor nodes (recursive)."""
        ancestors = set()
        if node_id not in self.nodes:
            return ancestors

        to_visit = [node_id]
        visited = set()

        while to_visit:
            current = to_visit.pop()
            if current in visited:
                continue
            visited.add(current)

            node = self.nodes.get(current)
            if node:
                for parent_id in node.parents:
                    ancestors.add(parent_id)
                    to_visit.append(parent_id)

        return ancestors

    def get_descendants(self, node_id: str) -> Set[str]:
        """Get all descendant nodes (recursive)."""
        descendants = set()
        if node_id not in self.nodes:
            return descendants

        to_visit = [node_id]
        visited = set()

        while to_visit:
            current = to_visit.pop()
            if current in visited:
                continue
            visited.add(current)

            node = self.nodes.get(current)
            if node:
                for child_id in node.children:
                    descendants.add(child_id)
                    to_visit.append(child_id)

        return descendants

    def get_lineage_path(self, finding_id: str) -> List[List[str]]:
        """Get all paths from finding to data sources."""
        paths = []

        def dfs(node_id: str, current_path: List[str]):
            current_path.append(node_id)
            node = self.nodes.get(node_id)

            if not node or not node.parents:
                # Reached a root node (data source)
                paths.append(current_path.copy())
            else:
                for parent_id in node.parents:
                    dfs(parent_id, current_path.copy())

        if finding_id in self.nodes:
            dfs(finding_id, [])

        return paths

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return {
            "session_id": self.session_id,
            "nodes": {
                node_id: {
                    "node_id": node.node_id,
                    "node_type": node.node_type,
                    "name": node.name,
                    "attributes": node.attributes,
                    "children": node.children,
                    "parents": node.parents,
                }
                for node_id, node in self.nodes.items()
            }
        }

    def save_to_file(self, workspace_dir: Path):
        """Save lineage graph to JSON file."""
        lineage_file = workspace_dir / f".lineage_{self.session_id}.json"
        with open(lineage_file, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)


class LineageTracker:
    """Tracks lineage relationships during analysis."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.graph = LineageGraph(session_id=session_id)

    def track_data_load(self, file_path: str, span_id: Optional[str] = None):
        """Track data source loading."""
        node_id = f"data_source:{file_path}"
        node = LineageNode(
            node_id=node_id,
            node_type="data_source",
            name=Path(file_path).name,
            attributes={
                "file_path": file_path,
                "span_id": span_id,
            }
        )
        self.graph.add_node(node)
        return node_id

    def track_tool_call(
        self,
        tool_name: str,
        span_id: str,
        inputs: Dict,
        outputs: Dict,
        parent_ids: List[str] = None
    ):
        """Track a tool call."""
        node_id = f"tool:{span_id}"
        node = LineageNode(
            node_id=node_id,
            node_type="tool_call",
            name=tool_name,
            attributes={
                "span_id": span_id,
                "inputs": inputs,
                "outputs": outputs,
            }
        )
        self.graph.add_node(node)

        # Link to parent nodes (e.g., data sources)
        if parent_ids:
            for parent_id in parent_ids:
                self.graph.add_edge(parent_id, node_id)

        return node_id

    def track_artifact(
        self,
        artifact_id: str,
        artifact_type: str,
        file_path: str,
        span_id: Optional[str] = None,
        parent_tool_id: Optional[str] = None
    ):
        """Track artifact creation."""
        node_id = f"artifact:{artifact_id}"
        node = LineageNode(
            node_id=node_id,
            node_type="artifact",
            name=artifact_id,
            attributes={
                "artifact_type": artifact_type,
                "file_path": file_path,
                "span_id": span_id,
            }
        )
        self.graph.add_node(node)

        # Link to parent tool
        if parent_tool_id:
            self.graph.add_edge(parent_tool_id, node_id)

        return node_id

    def track_evidence(
        self,
        evidence: EvidenceItem,
        parent_tool_id: Optional[str] = None,
        artifact_ids: List[str] = None
    ):
        """Track evidence item."""
        node_id = f"evidence:{evidence.span_id}" if evidence.span_id else f"evidence:{id(evidence)}"
        node = LineageNode(
            node_id=node_id,
            node_type="evidence",
            name=evidence.evidence_text[:50] + "..." if len(evidence.evidence_text) > 50 else evidence.evidence_text,
            attributes={
                "evidence_text": evidence.evidence_text,
                "source_fields": evidence.source_fields,
                "source_artifacts": evidence.source_artifacts,
                "span_id": evidence.span_id,
                "calculation_method": evidence.calculation_method,
            }
        )
        self.graph.add_node(node)

        # Link to parent tool
        if parent_tool_id:
            self.graph.add_edge(parent_tool_id, node_id)

        # Link to artifacts
        if artifact_ids:
            for artifact_id in artifact_ids:
                artifact_node_id = f"artifact:{artifact_id}"
                if artifact_node_id in self.graph.nodes:
                    self.graph.add_edge(artifact_node_id, node_id)

        return node_id

    def track_finding(
        self,
        finding: Finding,
        evidence_ids: List[str]
    ):
        """Track finding and link to evidence."""
        node_id = f"finding:{finding.finding_id}"
        node = LineageNode(
            node_id=node_id,
            node_type="finding",
            name=finding.statement,
            attributes={
                "finding_id": finding.finding_id,
                "statement": finding.statement,
                "confidence_level": finding.confidence_level,
                "evidence_level": finding.evidence_level,
                "trace_id": finding.trace_id,
            }
        )
        self.graph.add_node(node)

        # Link to evidence
        for evidence_id in evidence_ids:
            if evidence_id in self.graph.nodes:
                self.graph.add_edge(evidence_id, node_id)

        return node_id

    def validate_finding_lineage(self, finding_id: str) -> Tuple[bool, List[str]]:
        """Validate that a finding has complete lineage."""
        errors = []

        node_id = f"finding:{finding_id}"
        if node_id not in self.graph.nodes:
            errors.append(f"Finding {finding_id} not found in lineage graph")
            return False, errors

        # Check if finding has evidence
        finding_node = self.graph.nodes[node_id]
        if not finding_node.parents:
            errors.append(f"Finding {finding_id} has no evidence linked")

        # Check if evidence has sources
        for evidence_id in finding_node.parents:
            evidence_node = self.graph.nodes.get(evidence_id)
            if evidence_node and not evidence_node.parents:
                errors.append(f"Evidence {evidence_id} has no source (tool call or artifact)")

        # Check if we can trace back to data sources
        ancestors = self.graph.get_ancestors(node_id)
        has_data_source = any(
            self.graph.nodes[ancestor_id].node_type == "data_source"
            for ancestor_id in ancestors
            if ancestor_id in self.graph.nodes
        )

        if not has_data_source:
            errors.append(f"Finding {finding_id} cannot be traced back to any data source")

        return len(errors) == 0, errors

    def get_finding_lineage_summary(self, finding_id: str) -> Dict:
        """Get a summary of finding's lineage."""
        node_id = f"finding:{finding_id}"
        if node_id not in self.graph.nodes:
            return {"error": f"Finding {finding_id} not found"}

        ancestors = self.graph.get_ancestors(node_id)

        # Count by type
        type_counts = {}
        for ancestor_id in ancestors:
            node = self.graph.nodes.get(ancestor_id)
            if node:
                type_counts[node.node_type] = type_counts.get(node.node_type, 0) + 1

        # Get paths
        paths = self.graph.get_lineage_path(finding_id)

        return {
            "finding_id": finding_id,
            "total_ancestors": len(ancestors),
            "type_counts": type_counts,
            "num_paths": len(paths),
            "paths": [
                [self.graph.nodes[node_id].name for node_id in path if node_id in self.graph.nodes]
                for path in paths[:5]  # Limit to 5 paths for readability
            ],
            "is_complete": type_counts.get("data_source", 0) > 0,
        }

    def export_for_visualization(self) -> Dict:
        """Export lineage graph in a format suitable for visualization."""
        nodes = []
        edges = []

        for node_id, node in self.graph.nodes.items():
            nodes.append({
                "id": node_id,
                "label": node.name,
                "type": node.node_type,
                "attributes": node.attributes,
            })

            for child_id in node.children:
                edges.append({
                    "source": node_id,
                    "target": child_id,
                })

        return {
            "session_id": self.session_id,
            "nodes": nodes,
            "edges": edges,
        }

    def save(self, workspace_dir: Path):
        """Save lineage graph to file."""
        self.graph.save_to_file(workspace_dir)


def build_lineage_from_session(
    session_id: str,
    findings: List[Finding],
    artifacts: List[Dict],
    trace_context: Optional[TraceContext] = None
) -> LineageTracker:
    """Build lineage graph from session data."""
    tracker = LineageTracker(session_id)

    # Track artifacts
    artifact_map = {}
    for artifact in artifacts:
        artifact_id = artifact.get("artifact_id")
        if artifact_id:
            span_id = artifact.get("span_id")
            node_id = tracker.track_artifact(
                artifact_id=artifact_id,
                artifact_type=artifact.get("artifact_type", "unknown"),
                file_path=artifact.get("file_path", ""),
                span_id=span_id,
            )
            artifact_map[artifact_id] = node_id

    # Track findings and evidence
    for finding in findings:
        evidence_ids = []

        for evidence in finding.evidence:
            # Track evidence
            artifact_ids = [
                artifact_id
                for artifact_id in evidence.source_artifacts
                if artifact_id in artifact_map
            ]

            evidence_id = tracker.track_evidence(
                evidence=evidence,
                artifact_ids=artifact_ids,
            )
            evidence_ids.append(evidence_id)

        # Track finding
        tracker.track_finding(finding, evidence_ids)

    return tracker
