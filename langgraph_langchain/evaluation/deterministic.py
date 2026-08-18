"""Deterministic checks for facts, claims, artifacts, and evidence lineage."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from .models import (
    AssertionResult,
    EvaluationCase,
    EvaluationResult,
    EvaluationRunInput,
)


_MISSING = object()


def resolve_selector(value: Any, selector: str) -> Any:
    """Resolve a simple dotted selector with list indexes and ``*`` expansion."""
    current = [value]
    for token in filter(None, selector.split(".")):
        next_values: list[Any] = []
        for item in current:
            if token == "*":
                if isinstance(item, dict):
                    next_values.extend(item.values())
                elif isinstance(item, list):
                    next_values.extend(item)
                continue
            if isinstance(item, dict) and token in item:
                next_values.append(item[token])
            elif isinstance(item, list) and token.isdigit() and int(token) < len(item):
                next_values.append(item[int(token)])
        if not next_values:
            return _MISSING
        current = next_values
    return current[0] if len(current) == 1 else current


def _values_equal(actual: Any, expected: Any, absolute: float, relative: float) -> bool:
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        if isinstance(actual, float) and math.isnan(actual):
            return isinstance(expected, float) and math.isnan(expected)
        return math.isclose(actual, expected, abs_tol=absolute, rel_tol=relative)
    if isinstance(actual, list) and not isinstance(expected, list):
        return expected in actual
    return actual == expected


class DeterministicEvaluator:
    """Score normalized run output without consulting another model."""

    def evaluate(self, case: EvaluationCase, run: EvaluationRunInput) -> EvaluationResult:
        checks: list[AssertionResult] = []
        merged = {
            **run.payload,
            "payload": run.payload,
            "status": run.status,
            "report_text": run.report_text,
            "artifacts": run.artifacts,
            "findings": run.findings,
            "metrics": run.metrics,
        }

        checks.append(AssertionResult(
            assertion_id="workflow.completed",
            category="workflow",
            passed=run.status == "completed",
            message="Run completed" if run.status == "completed" else f"Run status is {run.status!r}",
            expected="completed",
            actual=run.status,
        ))

        for assertion in case.facts:
            actual = resolve_selector(merged, assertion.selector)
            passed = actual is not _MISSING and _values_equal(
                actual,
                assertion.expected,
                assertion.absolute_tolerance,
                assertion.relative_tolerance,
            )
            checks.append(AssertionResult(
                assertion_id=assertion.assertion_id,
                category=assertion.category,
                passed=passed,
                message=("Fact matched" if passed else "Fact was missing or outside tolerance"),
                expected=assertion.expected,
                actual=None if actual is _MISSING else actual,
                required=assertion.required,
            ))

        for forbidden in case.forbidden_claims:
            flags = re.IGNORECASE if "ignorecase" in forbidden.flags.lower() else 0
            match = re.search(forbidden.pattern, run.report_text, flags=flags)
            checks.append(AssertionResult(
                assertion_id=forbidden.assertion_id,
                category=forbidden.category,
                passed=match is None,
                message="Forbidden claim absent" if match is None else forbidden.reason,
                expected="pattern absent",
                actual=match.group(0) if match else None,
            ))

        metric_names = " ".join(
            str(item.get("metric_name") or item.get("name") or "")
            for item in run.metrics if isinstance(item, dict)
        )
        searchable = f"{run.report_text}\n{metric_names}".casefold()
        for kind, values in (
            ("metric", case.required_metrics),
            ("dimension", case.required_dimensions),
            ("time_window", case.required_time_windows),
        ):
            for index, value in enumerate(values):
                passed = str(value).casefold() in searchable
                checks.append(AssertionResult(
                    assertion_id=f"coverage.{kind}.{index}",
                    category="coverage",
                    passed=passed,
                    message=(f"Required {kind} documented" if passed else f"Required {kind} is missing: {value}"),
                    expected=value,
                    actual="present" if passed else "missing",
                ))

        for expected_artifact in case.artifacts:
            candidates = run.artifacts
            if expected_artifact.artifact_type:
                candidates = [
                    item for item in candidates
                    if item.get("artifact_type") == expected_artifact.artifact_type
                ]
            if expected_artifact.name_pattern:
                pattern = re.compile(expected_artifact.name_pattern, re.IGNORECASE)
                candidates = [
                    item for item in candidates
                    if pattern.search(str(item.get("name") or item.get("path") or ""))
                ]
            hash_ok = not expected_artifact.require_content_hash or all(
                bool(item.get("content_hash")) for item in candidates
            )
            passed = len(candidates) >= expected_artifact.minimum_count and hash_ok
            checks.append(AssertionResult(
                assertion_id=expected_artifact.assertion_id,
                category=expected_artifact.category,
                passed=passed,
                message="Artifact contract matched" if passed else expected_artifact.description,
                expected={"minimum_count": expected_artifact.minimum_count},
                actual={"count": len(candidates), "content_hashes_present": hash_ok},
            ))

        for lineage in case.lineage:
            selected = resolve_selector(merged, lineage.finding_selector)
            findings = selected if isinstance(selected, list) else []
            missing: list[str] = []
            for index, finding in enumerate(findings):
                if not isinstance(finding, dict):
                    missing.append(f"{index}:not_structured")
                    continue
                if lineage.require_execution_id and not (
                    finding.get("execution_id") or finding.get("execution_ids")
                ):
                    missing.append(f"{index}:execution")
                if lineage.require_input_asset and not (
                    finding.get("input_asset_ids") or finding.get("asset_ids")
                ):
                    missing.append(f"{index}:asset")
                has_artifact = finding.get("artifact_ids") or finding.get("artifact_refs")
                has_stats = finding.get("stats") or any(
                    isinstance(evidence, dict) and evidence.get("stats")
                    for evidence in finding.get("evidence", [])
                )
                if lineage.require_artifact_or_stats and not (has_artifact or has_stats):
                    missing.append(f"{index}:artifact_or_stats")
            passed = bool(findings) and not missing
            checks.append(AssertionResult(
                assertion_id=lineage.assertion_id,
                category=lineage.category,
                passed=passed,
                message="Finding lineage complete" if passed else lineage.description,
                expected="complete lineage",
                actual={"finding_count": len(findings), "missing": missing},
            ))

        data_hash_match = not case.data_hashes or all(
            run.data_hashes.get(name) == digest for name, digest in case.data_hashes.items()
        )
        if case.data_hashes:
            checks.append(AssertionResult(
                assertion_id="data.hashes",
                category="workflow",
                passed=data_hash_match,
                message="Input data hashes matched" if data_hash_match else "Input data version differs from the case",
                expected=case.data_hashes,
                actual=run.data_hashes,
            ))

        category_total: Counter[str] = Counter()
        category_passed: Counter[str] = Counter()
        failures: Counter[str] = Counter()
        for check in checks:
            if not check.required:
                continue
            category_total[check.category] += 1
            category_passed[check.category] += int(check.passed)
            if not check.passed:
                failures[check.category] += 1
        return EvaluationResult(
            case_id=case.case_id,
            run_id=run.run_id,
            passed=all(check.passed for check in checks if check.required),
            assertion_results=checks,
            category_scores={
                category: category_passed[category] / total
                for category, total in sorted(category_total.items())
            },
            failure_counts=dict(sorted(failures.items())),
            metadata={
                "model": run.model,
                "prompt_version": run.prompt_version,
                "code_version": run.code_version,
                "duration_seconds": run.duration_seconds,
                "token_usage": run.token_usage,
            },
        )
