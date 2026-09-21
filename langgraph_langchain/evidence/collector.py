"""Create EvidenceItems only from verified execution results.

This module intentionally contains no LLM logic.  It converts deterministic
execution and verification records into a traceable :class:`EvidenceItem`.
Unverified, failed, or incomplete executions are rejected.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from langgraph_langchain.runtime.models import ExecutionResult, RuntimeArtifact
from langgraph_langchain.schemas import EvidenceItem, VerificationResult
from langgraph_langchain.verification import verify_artifact_existence


class EvidenceCollectionError(ValueError):
    """Raised when an execution cannot produce trusted evidence."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        detail = "; ".join(self.errors)
        super().__init__(f"Cannot create trusted evidence: {detail}")


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvidenceCollector:
    """Build EvidenceItems from successful, verified executions."""

    def collect(
        self,
        *,
        execution: ExecutionResult,
        verification_results: Iterable[VerificationResult],
        artifacts: Optional[Mapping[str, RuntimeArtifact]] = None,
        evidence_text: Optional[str] = None,
        source_fields: Optional[list[str]] = None,
        source_artifacts: Optional[list[str]] = None,
        time_window: Optional[str] = None,
        group_dimension: Optional[str] = None,
        filters: Optional[list[str]] = None,
        stats: Optional[dict[str, Any]] = None,
        calculation_method: Optional[str] = None,
        span_id: Optional[str] = None,
    ) -> EvidenceItem:
        """Return one verified EvidenceItem or raise ``EvidenceCollectionError``.

        Args:
            execution: The execution that produced the result.
            verification_results: Verification records related to the execution.
            artifacts: Registry of artifacts referenced by the execution.
            evidence_text: Optional caller-provided factual text.  If omitted, a
                deterministic execution/verification summary is used.
            source_fields: Input or output fields involved in the result.
            source_artifacts: Human-readable artifact names.
            time_window: Optional time window represented by the evidence.
            group_dimension: Optional grouping dimension.
            filters: Optional filters used by the execution.
            stats: Optional numeric summary values.
            calculation_method: Optional calculation description.
            span_id: Optional tracing span ID.
        """
        errors: list[str] = []

        execution_id = _value(execution, "execution_id")
        if not execution_id:
            errors.append("Execution is missing execution_id")

        status = _value(execution, "status")
        if status != "succeeded":
            errors.append(f"Execution status is {status!r}, expected 'succeeded'")

        run_id = _value(execution, "run_id")
        task_id = _value(execution, "task_id")
        step_id = _value(execution, "step_id")
        tool_name = _value(execution, "tool_name", "unknown_tool")
        output_artifact_ids = list(_value(execution, "output_artifact_ids", []) or [])
        input_asset_ids = list(_value(execution, "input_asset_ids", []) or [])

        verifications = list(verification_results)
        seen_verification_ids: set[str] = set()
        relevant: list[VerificationResult] = []
        passed: list[VerificationResult] = []
        failed: list[VerificationResult] = []

        for verification in verifications:
            verification_id = _value(verification, "verification_id")
            if not verification_id:
                errors.append("Verification result is missing verification_id")
                continue
            if verification_id in seen_verification_ids:
                errors.append(f"Duplicate verification_id: {verification_id}")
                continue
            seen_verification_ids.add(verification_id)

            verification_run_id = _value(verification, "run_id")
            verification_execution_id = _value(verification, "execution_id")
            verification_task_id = _value(verification, "task_id")
            verification_step_id = _value(verification, "step_id")
            # Prefer exact execution linkage.  Task/step IDs are only a fallback
            # for verification records that do not carry an execution_id.
            related = (
                verification_execution_id == execution_id
                or (
                    verification_execution_id is None
                    and bool(task_id)
                    and verification_task_id == task_id
                )
                or (
                    verification_execution_id is None
                    and bool(step_id)
                    and verification_step_id == step_id
                )
            )
            if not related:
                continue

            if run_id and verification_run_id and verification_run_id != run_id:
                errors.append(
                    f"Verification '{verification_id}' belongs to run "
                    f"'{verification_run_id}', expected '{run_id}'"
                )
                continue

            relevant.append(verification)
            if _value(verification, "status") == "passed" and _value(verification, "passed") is True:
                passed.append(verification)
            else:
                failed.append(verification)

        if not relevant:
            errors.append("No VerificationResult is related to this execution")
        if not passed:
            errors.append("No related VerificationResult passed")
        if failed:
            failed_ids = ", ".join(
                str(_value(item, "verification_id")) for item in failed
            )
            errors.append(f"Related verification checks failed: {failed_ids}")

        artifact_registry = artifacts or {}
        artifact_names: list[str] = []
        if output_artifact_ids:
            for artifact_id in output_artifact_ids:
                artifact = artifact_registry.get(artifact_id)
                if artifact is None:
                    errors.append(f"Output artifact '{artifact_id}' is not registered")
                    continue

                existence = verify_artifact_existence(
                    artifact,
                    context={
                        "run_id": run_id,
                        "task_id": task_id,
                        "step_id": step_id,
                        "execution_id": execution_id,
                        "artifact_id": artifact_id,
                    },
                )
                if not existence.passed:
                    errors.append(
                        f"Output artifact '{artifact_id}' does not exist: "
                        f"{existence.message}"
                    )
                    continue
                artifact_names.append(str(_value(artifact, "name", artifact_id)))

        if errors:
            raise EvidenceCollectionError(errors)

        primary_verification = sorted(
            passed,
            key=lambda item: str(_value(item, "created_at", "")),
        )[0]
        passed_count = len(passed)

        if evidence_text is None:
            evidence_text = (
                f"{tool_name} execution {execution_id} succeeded and passed "
                f"{passed_count} verification check(s)."
            )
        evidence_text = str(evidence_text).strip()
        if not evidence_text:
            errors.append("evidence_text cannot be blank")

        if errors:
            raise EvidenceCollectionError(errors)

        return EvidenceItem(
            verification_status="verified",
            verification_result_id=_value(primary_verification, "verification_id"),
            evidence_text=evidence_text,
            source_fields=list(source_fields or []),
            source_artifacts=source_artifacts or artifact_names,
            source_artifact_ids=output_artifact_ids,
            source_execution_ids=[str(execution_id)],
            source_step_ids=[str(step_id)] if step_id else [],
            source_asset_ids=input_asset_ids,
            time_window=time_window,
            group_dimension=group_dimension,
            filters=list(filters or []),
            stats=stats,
            calculation_method=calculation_method or f"runtime_verified:{tool_name}",
            span_id=span_id,
            created_at=_utc_now(),
        )

