"""Build Runtime findings only from verified evidence.

This module enforces the V7 provenance contract:

    Finding -> Evidence -> VerificationResult -> ExecutionResult -> Task -> Asset

It is intentionally LLM-free and can be reused by tools, workflows, and tests.
"""

from __future__ import annotations

from typing import Any, Optional

from langgraph_langchain.runtime.models import ExecutionResult
from langgraph_langchain.schemas import AnalysisTask, EvidenceItem, Finding, VerificationResult


class FindingProvenanceError(ValueError):
    """Raised when a finding cannot be traced to verified execution evidence."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        detail = "; ".join(self.errors)
        super().__init__(f"Finding provenance validation failed: {detail}")


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


class FindingBuilder:
    """Create and validate findings from Runtime-owned trusted evidence."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    @property
    def _evidence_by_id(self) -> dict[str, EvidenceItem]:
        return {
            str(item.evidence_id): item
            for item in (self.runtime.evidence or [])
        }

    @property
    def _verification_by_id(self) -> dict[str, VerificationResult]:
        return {
            str(item.verification_id): item
            for item in (self.runtime.verifications or [])
        }

    @property
    def _execution_by_id(self) -> dict[str, ExecutionResult]:
        return {
            str(item.execution_id): item
            for item in (self.runtime.executions or [])
        }

    def validate_finding(self, finding: Finding) -> list[str]:
        """Return all provenance violations for a finding."""
        errors: list[str] = []
        evidence_by_id = self._evidence_by_id
        verification_by_id = self._verification_by_id
        execution_by_id = self._execution_by_id

        if not finding.supported_by:
            errors.append(f"finding {finding.finding_id} has no supported_by evidence")
            return errors

        if not finding.evidence:
            errors.append(f"finding {finding.finding_id} has no embedded evidence")

        embedded_ids = {item.evidence_id for item in finding.evidence}
        if embedded_ids != set(finding.supported_by):
            errors.append(
                f"finding {finding.finding_id} supported_by and embedded evidence IDs differ"
            )

        for evidence_id in finding.supported_by:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                errors.append(f"evidence {evidence_id} is not recorded in Runtime")
                continue

            if evidence.verification_status != "verified":
                errors.append(f"evidence {evidence_id} is not verified")
            verification_id = evidence.verification_result_id
            if not verification_id:
                errors.append(f"evidence {evidence_id} has no verification_result_id")
                continue

            verification = verification_by_id.get(verification_id)
            if verification is None:
                errors.append(f"verification {verification_id} is not recorded in Runtime")
                continue
            if verification.status != "passed" or verification.passed is not True:
                errors.append(f"verification {verification_id} did not pass")
            if verification.evidence_id and verification.evidence_id != evidence_id:
                errors.append(f"verification {verification_id} points to another evidence item")

            execution_id = verification.execution_id
            if not execution_id:
                errors.append(f"verification {verification_id} has no execution_id")
                continue

            execution = execution_by_id.get(execution_id)
            if execution is None:
                errors.append(f"execution {execution_id} is not recorded in Runtime")
                continue
            if execution.status != "succeeded":
                errors.append(f"execution {execution_id} did not succeed")
            if evidence.source_execution_ids and execution_id not in evidence.source_execution_ids:
                errors.append(
                    f"evidence {evidence_id} does not cite execution {execution_id}"
                )

            task_id = verification.task_id or execution.task_id
            if not task_id:
                errors.append(f"execution {execution_id} cannot be traced to a task")
                continue
            if self.runtime.scheduler is None:
                errors.append("Runtime has no scheduler")
            else:
                try:
                    task = self.runtime.scheduler.get_task(task_id)
                    if task.status != "succeeded":
                        errors.append(f"task {task_id} did not succeed")
                except KeyError:
                    errors.append(f"task {task_id} is not scheduled in Runtime")

            asset_ids = list(evidence.source_asset_ids or [])
            if not asset_ids:
                errors.append(f"evidence {evidence_id} has no source_asset_ids")
            for asset_id in asset_ids:
                if asset_id not in self.runtime.assets:
                    errors.append(f"evidence {evidence_id} references unknown asset {asset_id}")

            artifact_ids = list(evidence.source_artifact_ids or [])
            for artifact_id in artifact_ids:
                artifact = self.runtime.artifacts.get(artifact_id)
                if artifact is None:
                    errors.append(f"evidence {evidence_id} references unknown artifact {artifact_id}")

        return errors

    def build_from_verified_evidence(
        self,
        *,
        task: AnalysisTask,
        execution: ExecutionResult,
        evidence: EvidenceItem,
        statement: str,
        finding_id: str,
        confidence_level: str = "medium",
        evidence_level: str = "B",
        hypothesis_flag: bool = False,
        category: Optional[str] = None,
        finding_type: Optional[str] = None,
        depends_on: Optional[list[str]] = None,
        recorded_by_execution_id: Optional[str] = None,
        recorded_by_step_id: Optional[str] = None,
    ) -> Finding:
        """Build one finding after checking its complete Runtime provenance."""
        candidate = Finding(
            finding_id=finding_id,
            statement=statement,
            evidence=[evidence],
            confidence_level=confidence_level,  # type: ignore[arg-type]
            evidence_level=evidence_level,  # type: ignore[arg-type]
            hypothesis_flag=hypothesis_flag,
            category=category,
            finding_type=finding_type or task.task_type,  # type: ignore[arg-type]
            supported_by=[evidence.evidence_id],
            depends_on=list(depends_on or []),
            stats=evidence.stats,
            calculation_method=evidence.calculation_method,
            run_id=self.runtime.run.run_id,
            recorded_by_execution_id=recorded_by_execution_id or execution.execution_id,
            recorded_by_step_id=recorded_by_step_id or execution.step_id,
        )
        errors = self.validate_finding(candidate)
        if errors:
            raise FindingProvenanceError(errors)
        return candidate
