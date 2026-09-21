"""First-stage deterministic checks for Runtime V2.

These helpers never call an LLM.  They return structured
:class:`VerificationResult` objects so failures can block Evidence creation and
remain auditable later.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from numbers import Real
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from langgraph_langchain.schemas import VerificationResult


_ALLOWED_CONTEXT_FIELDS = {
    "run_id",
    "step_id",
    "task_id",
    "execution_id",
    "artifact_id",
    "evidence_id",
}


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _context(context: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if not context:
        return {}
    return {
        key: value
        for key, value in context.items()
        if key in _ALLOWED_CONTEXT_FIELDS and value is not None
    }


def _result(
    *,
    check_type: str,
    passed: bool,
    message: str,
    expected: Any = None,
    actual: Any = None,
    tolerance: Optional[float] = None,
    details: Optional[dict[str, Any]] = None,
    context: Optional[Mapping[str, Any]] = None,
) -> VerificationResult:
    return VerificationResult(
        check_type=check_type,  # type: ignore[arg-type]
        status="passed" if passed else "failed",
        passed=passed,
        expected=expected,
        actual=actual,
        tolerance=tolerance,
        message=message,
        details=details or {},
        **_context(context),
    )


def _is_number(value: Any) -> bool:
    return isinstance(value, (Real, Decimal)) and not isinstance(value, bool)


def _normalise_tolerance(tolerance: Any) -> Optional[float]:
    if tolerance is None:
        return 0.0
    if not _is_number(tolerance) or float(tolerance) < 0:
        return None
    return float(tolerance)


def verify_numeric_consistency(
    *,
    expected: Any,
    actual: Any,
    tolerance: Any = 0.0,
    context: Optional[Mapping[str, Any]] = None,
) -> VerificationResult:
    """Check that two numeric values agree within a non-negative tolerance."""
    normalised_tolerance = _normalise_tolerance(tolerance)
    if normalised_tolerance is None:
        return _result(
            check_type="numeric_consistency",
            passed=False,
            message="Tolerance must be a non-negative number",
            expected=expected,
            actual=actual,
            details={"invalid_tolerance": tolerance},
            context=context,
        )
    if not _is_number(expected) or not _is_number(actual):
        return _result(
            check_type="numeric_consistency",
            passed=False,
            message="Expected and actual values must both be numeric",
            expected=expected,
            actual=actual,
            tolerance=normalised_tolerance,
            context=context,
        )

    difference = abs(float(actual) - float(expected))
    passed = difference <= normalised_tolerance
    return _result(
        check_type="numeric_consistency",
        passed=passed,
        message=(
            f"Values match within tolerance (difference={difference})"
            if passed
            else f"Values differ by {difference}, tolerance is {normalised_tolerance}"
        ),
        expected=float(expected),
        actual=float(actual),
        tolerance=normalised_tolerance,
        details={"difference": difference},
        context=context,
    )


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def verify_time_consistency(
    *,
    start_time: Any,
    end_time: Any,
    context: Optional[Mapping[str, Any]] = None,
) -> VerificationResult:
    """Check that a validated end time is not before its start time."""
    start = _parse_datetime(start_time)
    end = _parse_datetime(end_time)
    if start is None or end is None:
        return _result(
            check_type="time_consistency",
            passed=False,
            message="start_time and end_time must be valid ISO-8601 datetimes",
            expected="valid start_time <= end_time",
            actual={"start_time": start_time, "end_time": end_time},
            context=context,
        )

    passed = end >= start
    return _result(
        check_type="time_consistency",
        passed=passed,
        message=(
            "Time window is consistent"
            if passed
            else "end_time is before start_time"
        ),
        expected={"start_time": start.isoformat(), "end_time": None},
        actual={"start_time": start.isoformat(), "end_time": end.isoformat()},
        context=context,
    )


def _component_value(component: Any, field: str) -> tuple[bool, Any]:
    if _is_number(component):
        return True, component
    value = _value(component, field)
    if _is_number(value):
        return True, value
    return False, value


def verify_aggregation_consistency(
    *,
    total: Any,
    components: Any,
    tolerance: Any = 0.0,
    component_field: str = "value",
    context: Optional[Mapping[str, Any]] = None,
) -> VerificationResult:
    """Check that component values sum to their declared total."""
    normalised_tolerance = _normalise_tolerance(tolerance)
    if normalised_tolerance is None:
        return _result(
            check_type="aggregation_consistency",
            passed=False,
            message="Tolerance must be a non-negative number",
            expected=total,
            actual=components,
            context=context,
        )
    if not _is_number(total):
        return _result(
            check_type="aggregation_consistency",
            passed=False,
            message="Total must be numeric",
            expected=total,
            actual=components,
            tolerance=normalised_tolerance,
            context=context,
        )

    if isinstance(components, Mapping):
        raw_components: Iterable[Any] = components.values()
    elif isinstance(components, (list, tuple)):
        raw_components = components
    else:
        return _result(
            check_type="aggregation_consistency",
            passed=False,
            message="Components must be a sequence or mapping",
            expected=total,
            actual=components,
            tolerance=normalised_tolerance,
            context=context,
        )

    values: list[float] = []
    errors: list[str] = []
    for index, component in enumerate(raw_components):
        valid, value = _component_value(component, component_field)
        if not valid:
            errors.append(f"component[{index}] is not numeric")
            continue
        values.append(float(value))

    if errors:
        return _result(
            check_type="aggregation_consistency",
            passed=False,
            message="; ".join(errors),
            expected=float(total),
            actual=components,
            tolerance=normalised_tolerance,
            context=context,
        )

    component_total = sum(values)
    difference = abs(component_total - float(total))
    passed = difference <= normalised_tolerance
    return _result(
        check_type="aggregation_consistency",
        passed=passed,
        message=(
            f"Components sum to total (difference={difference})"
            if passed
            else (
                f"Component total {component_total} differs from declared total "
                f"{float(total)} by {difference}"
            )
        ),
        expected=float(total),
        actual=component_total,
        tolerance=normalised_tolerance,
        details={"component_count": len(values), "difference": difference},
        context=context,
    )


def verify_artifact_existence(
    artifact: Any,
    *,
    workspace_dir: Optional[str | Path] = None,
    context: Optional[Mapping[str, Any]] = None,
) -> VerificationResult:
    """Check that a RuntimeArtifact points to an existing file."""
    artifact_id = _value(artifact, "artifact_id")
    raw_path = _value(artifact, "path") or _value(artifact, "relative_path")
    if not raw_path:
        merged_context = {
            **_context(context),
            **({"artifact_id": artifact_id} if artifact_id else {}),
        }
        return _result(
            check_type="artifact_existence",
            passed=False,
            message="Artifact has no path",
            expected="existing artifact file",
            actual=artifact,
            context=merged_context,
        )

    path = Path(str(raw_path))
    if not path.is_absolute() and workspace_dir is not None:
        path = Path(workspace_dir) / path

    passed = path.exists() and path.is_file()
    merged_context = {
        **_context(context),
        **({"artifact_id": artifact_id} if artifact_id else {}),
    }
    return _result(
        check_type="artifact_existence",
        passed=passed,
        message=f"Artifact file exists at {path}" if passed else f"Artifact file not found at {path}",
        expected="existing artifact file",
        actual=str(path),
        details={"absolute_path": str(path.resolve())},
        context=merged_context,
    )


def verify_evidence_existence(
    evidence: Any,
    *,
    context: Optional[Mapping[str, Any]] = None,
) -> VerificationResult:
    """Check that an EvidenceItem has its identity and primary text."""
    evidence_id = _value(evidence, "evidence_id")
    evidence_text = _value(evidence, "evidence_text")
    passed = bool(evidence_id) and isinstance(evidence_text, str) and bool(evidence_text.strip())
    merged_context = {
        **_context(context),
        **({"evidence_id": evidence_id} if evidence_id else {}),
    }
    return _result(
        check_type="evidence_existence",
        passed=passed,
        message="Evidence identity and text are present" if passed else "Evidence is missing an ID or non-empty text",
        expected="non-empty evidence with an ID",
        actual={
            "evidence_id": evidence_id,
            "evidence_text": evidence_text,
        },
        details={
            "source_artifact_count": len(_value(evidence, "source_artifact_ids", []) or []),
            "source_execution_count": len(_value(evidence, "source_execution_ids", []) or []),
            "source_step_count": len(_value(evidence, "source_step_ids", []) or []),
            "source_asset_count": len(_value(evidence, "source_asset_ids", []) or []),
        },
        context=merged_context,
    )


def verify_execution_result(
    result: Any,
    *,
    artifacts: Optional[Mapping[str, Any]] = None,
    workspace_dir: Optional[str | Path] = None,
    context: Optional[Mapping[str, Any]] = None,
) -> list[VerificationResult]:
    """Verify one execution and each artifact it claims to have produced."""
    artifact_registry = artifacts or {}
    output_ids = _value(result, "output_artifact_ids", []) or []
    checks: list[VerificationResult] = []

    status = _value(result, "status")
    if status != "succeeded":
        checks.append(
            _result(
                check_type="custom",
                passed=False,
                message=f"Execution status is {status!r}, expected 'succeeded'",
                expected="succeeded",
                actual=status,
                context=context,
            )
        )

    for artifact_id in output_ids:
        artifact = artifact_registry.get(artifact_id)
        if artifact is None:
            checks.append(
                _result(
                    check_type="artifact_existence",
                    passed=False,
                    message=f"Output artifact '{artifact_id}' is not registered",
                    expected="registered artifact",
                    actual=None,
                    context={**_context(context), "artifact_id": artifact_id},
                )
            )
            continue
        checks.append(verify_artifact_existence(artifact, workspace_dir=workspace_dir, context=context))

    return checks

