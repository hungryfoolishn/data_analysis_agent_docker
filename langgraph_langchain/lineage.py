"""Lineage tracking for analysis artifacts and conclusions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from langgraph_langchain.schemas import (
    ArtifactRef,
    ConclusionTrace,
    EvidenceItem,
    Finding,
    SessionLineage,
)


class LineageTracker:
    """Track lineage from conclusions to evidence to artifacts."""

    def __init__(
        self,
        session_id: str,
        workspace_dir: Path,
        request_id: Optional[str] = None,
        run_id: Optional[str] = None,
        user_question: Optional[str] = None,
        data_source: Optional[str] = None,
    ):
        self.session_id = session_id
        self.workspace_dir = workspace_dir
        self.request_id = request_id
        self.run_id = run_id
        self.user_question = user_question
        self.data_source = data_source

        # Storage
        self.artifacts: Dict[str, ArtifactRef] = {}
        self.findings: Dict[str, Finding] = {}
        self.current_step = 0
        self.current_tool: Optional[str] = None

    def register_artifact(
        self,
        artifact_id: str,
        artifact_type: str,
        file_path: Optional[str] = None,
        description: Optional[str] = None,
        span_id: Optional[str] = None,
    ) -> ArtifactRef:
        """Register a new artifact."""
        artifact = ArtifactRef(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            file_path=file_path,
            description=description,
            created_by_tool=self.current_tool,
            created_by_step=self.current_step,
            created_at=datetime.now(timezone.utc).isoformat(),
            span_id=span_id,
            referenced_by_findings=[],
        )
        self.artifacts[artifact_id] = artifact
        return artifact

    def register_finding(self, finding: Finding) -> None:
        """Register a finding and link to artifacts."""
        self.findings[finding.finding_id] = finding

        # Link artifacts mentioned in evidence
        for evidence in finding.evidence:
            for artifact_name in evidence.source_artifacts:
                if artifact_name in self.artifacts:
                    artifact = self.artifacts[artifact_name]
                    if finding.finding_id not in artifact.referenced_by_findings:
                        artifact.referenced_by_findings.append(finding.finding_id)

    def set_current_context(self, step: int, tool_name: Optional[str] = None) -> None:
        """Set current execution context."""
        self.current_step = step
        if tool_name:
            self.current_tool = tool_name

    def build_conclusion_trace(self, finding_id: str) -> Optional[ConclusionTrace]:
        """Build a complete trace for a finding."""
        if finding_id not in self.findings:
            return None

        finding = self.findings[finding_id]

        # Collect all artifacts referenced in evidence
        artifact_refs: List[ArtifactRef] = []
        for evidence in finding.evidence:
            for artifact_name in evidence.source_artifacts:
                if artifact_name in self.artifacts:
                    artifact_refs.append(self.artifacts[artifact_name])

        # Collect source steps
        source_steps = []
        if finding.source_step is not None:
            source_steps.append(finding.source_step)
        for artifact in artifact_refs:
            if artifact.created_by_step is not None and artifact.created_by_step not in source_steps:
                source_steps.append(artifact.created_by_step)

        # Build trace path description
        trace_path = self._build_trace_path(finding, artifact_refs)

        return ConclusionTrace(
            finding_id=finding_id,
            finding_statement=finding.statement,
            evidence_items=finding.evidence,
            artifacts=artifact_refs,
            source_steps=sorted(source_steps),
            trace_path=trace_path,
        )

    def _build_trace_path(self, finding: Finding, artifacts: List[ArtifactRef]) -> str:
        """Build a human-readable trace path."""
        parts = [f"Finding {finding.finding_id}: {finding.statement[:50]}..."]

        if finding.evidence:
            parts.append(f"  → {len(finding.evidence)} evidence items")

        if artifacts:
            artifact_summary = ", ".join(
                f"{a.artifact_type}:{a.artifact_id}" for a in artifacts[:3]
            )
            if len(artifacts) > 3:
                artifact_summary += f" (+{len(artifacts) - 3} more)"
            parts.append(f"  → Artifacts: {artifact_summary}")

        if finding.source_tool:
            parts.append(f"  → Created by: {finding.source_tool}")

        if finding.source_step is not None:
            parts.append(f"  → Step: {finding.source_step}")

        return "\n".join(parts)

    def get_session_lineage(
        self,
        final_report: Optional[str] = None,
        report_references_findings: Optional[List[str]] = None,
    ) -> SessionLineage:
        """Get complete session lineage."""
        # Build conclusion traces for all findings
        conclusion_traces = []
        for finding_id in self.findings:
            trace = self.build_conclusion_trace(finding_id)
            if trace:
                conclusion_traces.append(trace)

        return SessionLineage(
            session_id=self.session_id,
            request_id=self.request_id,
            run_id=self.run_id,
            user_question=self.user_question,
            data_source=self.data_source,
            artifacts=list(self.artifacts.values()),
            findings=list(self.findings.values()),
            conclusion_traces=conclusion_traces,
            final_report=final_report,
            report_references_findings=report_references_findings or [],
        )

    def save_lineage(
        self,
        final_report: Optional[str] = None,
        report_references_findings: Optional[List[str]] = None,
    ) -> Path:
        """Save lineage to file."""
        lineage = self.get_session_lineage(final_report, report_references_findings)
        lineage_path = self.workspace_dir / f"lineage_{self.session_id}.json"

        with open(lineage_path, "w", encoding="utf-8") as f:
            f.write(lineage.model_dump_json(indent=2))

        return lineage_path

    def get_artifact_by_id(self, artifact_id: str) -> Optional[ArtifactRef]:
        """Get artifact by ID."""
        return self.artifacts.get(artifact_id)

    def get_finding_by_id(self, finding_id: str) -> Optional[Finding]:
        """Get finding by ID."""
        return self.findings.get(finding_id)

    def get_artifacts_for_finding(self, finding_id: str) -> List[ArtifactRef]:
        """Get all artifacts referenced by a finding."""
        if finding_id not in self.findings:
            return []

        finding = self.findings[finding_id]
        artifact_refs = []

        for evidence in finding.evidence:
            for artifact_name in evidence.source_artifacts:
                if artifact_name in self.artifacts:
                    artifact_refs.append(self.artifacts[artifact_name])

        return artifact_refs

    def get_findings_for_artifact(self, artifact_id: str) -> List[Finding]:
        """Get all findings that reference an artifact."""
        if artifact_id not in self.artifacts:
            return []

        artifact = self.artifacts[artifact_id]
        return [
            self.findings[fid]
            for fid in artifact.referenced_by_findings
            if fid in self.findings
        ]

    def get_lineage_summary(self) -> Dict:
        """Get a summary of lineage statistics."""
        return {
            "session_id": self.session_id,
            "total_artifacts": len(self.artifacts),
            "total_findings": len(self.findings),
            "artifacts_by_type": self._count_by_type(),
            "findings_by_category": self._count_findings_by_category(),
            "artifacts_with_references": sum(
                1 for a in self.artifacts.values() if a.referenced_by_findings
            ),
            "findings_with_artifacts": sum(
                1 for f in self.findings.values()
                if any(e.source_artifacts for e in f.evidence)
            ),
        }

    def _count_by_type(self) -> Dict[str, int]:
        """Count artifacts by type."""
        counts = {}
        for artifact in self.artifacts.values():
            counts[artifact.artifact_type] = counts.get(artifact.artifact_type, 0) + 1
        return counts

    def _count_findings_by_category(self) -> Dict[str, int]:
        """Count findings by category."""
        counts = {}
        for finding in self.findings.values():
            category = finding.category or "uncategorized"
            counts[category] = counts.get(category, 0) + 1
        return counts

    @staticmethod
    def load_lineage(lineage_path: Path) -> SessionLineage:
        """Load lineage from file."""
        with open(lineage_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return SessionLineage(**data)
