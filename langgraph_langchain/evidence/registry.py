"""Registry for canonical evidence records."""

from __future__ import annotations

from typing import Callable, Iterable, Mapping

from langgraph_langchain.evidence.models import CanonicalEvidence


class EvidenceRegistryError(ValueError):
    """Raised when evidence cannot be registered or verified."""


class EvidenceRegistry:
    """Store canonical evidence and validate references, execution, and content."""

    def __init__(
        self,
        *,
        assets: Mapping[str, object] | None = None,
        artifacts: Mapping[str, object] | None = None,
        executions: (
            Mapping[str, object] | Callable[[str], object | None] | None
        ) = None,
    ) -> None:
        self._assets = assets if assets is not None else {}
        self._artifacts = artifacts if artifacts is not None else {}
        self._records: dict[str, CanonicalEvidence] = {}
        if executions is None:
            self._execution_provider: Callable[[str], object | None] = (
                lambda execution_id: None
            )
        elif callable(executions):
            self._execution_provider = executions
        else:
            execution_map = dict(executions)
            self._execution_provider = execution_map.get

    @staticmethod
    def _reference_validated(evidence: CanonicalEvidence) -> CanonicalEvidence:
        evidence.validation_status = "reference_validated"
        return evidence

    def register(
        self,
        evidence: CanonicalEvidence,
        *,
        replace: bool = False,
    ) -> CanonicalEvidence:
        if evidence.evidence_id in self._records and not replace:
            raise EvidenceRegistryError(
                f"Duplicate evidence_id: {evidence.evidence_id}"
            )
        errors = self.validate_references(evidence)
        if errors:
            raise EvidenceRegistryError(
                "Invalid evidence references: " + "; ".join(errors)
            )
        # Reference validation never preserves a caller-supplied verified state.
        evidence.validation_status = "reference_validated"
        self._records[evidence.evidence_id] = evidence
        return evidence

    def validate_references(self, evidence: CanonicalEvidence) -> list[str]:
        errors: list[str] = []
        if not evidence.execution_id:
            errors.append("execution_id is required")
        elif self._lookup_execution(evidence.execution_id) is None:
            errors.append(
                f"Unknown execution_id: {evidence.execution_id}"
            )
        if not evidence.tool_name:
            errors.append("tool_name is required")
        if not evidence.evidence_text.strip():
            errors.append("evidence_text is required")
        unknown_assets = sorted(
            set(evidence.input_asset_ids + evidence.output_asset_ids)
            - set(self._assets)
        )
        if unknown_assets:
            errors.append("Unknown asset IDs: " + ", ".join(unknown_assets))
        unknown_artifacts = sorted(
            set(evidence.artifact_ids) - set(self._artifacts)
        )
        if unknown_artifacts:
            errors.append("Unknown artifact IDs: " + ", ".join(unknown_artifacts))
        return errors

    def _lookup_execution(self, execution_id: str) -> object | None:
        return self._execution_provider(execution_id)

    def get(self, evidence_id: str) -> CanonicalEvidence:
        evidence = self._records.get(evidence_id)
        if evidence is None:
            raise EvidenceRegistryError(f"Unknown evidence_id: {evidence_id}")
        return evidence

    def list(
        self,
        *,
        execution_id: str | None = None,
        statuses: Iterable[str] | None = None,
    ) -> list[CanonicalEvidence]:
        selected = list(self._records.values())
        if execution_id is not None:
            selected = [item for item in selected if item.execution_id == execution_id]
        if statuses is not None:
            allowed = set(statuses)
            selected = [item for item in selected if item.validation_status in allowed]
        return selected

    def verify(
        self,
        evidence_id: str,
        *,
        content_validator: Callable[[CanonicalEvidence], list[str]] | None = None,
    ) -> CanonicalEvidence:
        """Verify evidence references and caller-provided content checks."""
        evidence = self.get(evidence_id)
        if content_validator is None:
            evidence.validation_status = "failed"
            raise EvidenceRegistryError(
                "Evidence content validator is required before verification"
            )
        errors = self.validate_references(evidence)
        errors.extend(content_validator(evidence))
        if errors:
            evidence.validation_status = "failed"
            raise EvidenceRegistryError(
                "Evidence verification failed: " + "; ".join(errors)
            )
        evidence.validation_status = "verified"
        return evidence

    def mark_failed(self, evidence_id: str, reason: str) -> CanonicalEvidence:
        evidence = self.get(evidence_id)
        evidence.validation_status = "failed"
        evidence.source_metadata["failure_reason"] = reason
        return evidence

    def report_records(self) -> dict[str, dict]:
        return {
            evidence_id: evidence.report_record()
            for evidence_id, evidence in self._records.items()
        }