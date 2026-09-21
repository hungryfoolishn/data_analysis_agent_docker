"""Runtime V8 evaluation and learning primitives.

The module turns trusted Runtime lineage into task evaluations, failure
memory, and deterministic skill-quality records.  It intentionally does not
mutate skills or prompt instructions; that belongs to a later evolution phase.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from pydantic import BaseModel, Field

from langgraph_langchain.runtime.models import ExecutionResult, new_id, utc_now
from langgraph_langchain.schemas import AnalysisTask


FailureKind = str
SkillRecommendation = str

_LEARNING_VERSION = 1


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _error_message(result: ExecutionResult) -> str:
    error = result.error or {}
    if isinstance(error, dict):
        return str(error.get("message") or error.get("type") or "")
    return str(error)


def _verification_records(runtime: Any, result: ExecutionResult) -> list[Any]:
    records: dict[str, Any] = {}
    for item in list(result.verification_results or []):
        verification_id = str(_value(item, "verification_id"))
        if verification_id:
            records[verification_id] = item
    for item in (getattr(runtime, "verifications", None) or []):
        execution_id = str(_value(item, "execution_id") or "")
        verification_id = str(_value(item, "verification_id"))
        if execution_id == result.execution_id and verification_id:
            records.setdefault(verification_id, item)
    return list(records.values())


def _related_evidence(runtime: Any, result: ExecutionResult) -> list[Any]:
    output: list[Any] = []
    seen: set[str] = set()
    direct = _value(result, "evidence")
    if direct is not None:
        evidence_id = str(_value(direct, "evidence_id"))
        if evidence_id:
            seen.add(evidence_id)
        output.append(direct)

    execution_id = result.execution_id
    for item in (getattr(runtime, "evidence", None) or []):
        evidence_id = str(_value(item, "evidence_id"))
        if evidence_id in seen:
            continue
        source_execution_ids = {
            str(value) for value in (_value(item, "source_execution_ids") or [])
        }
        if execution_id in source_execution_ids:
            seen.add(evidence_id)
            output.append(item)
    return output


def _related_findings(runtime: Any, result: ExecutionResult) -> list[Any]:
    execution_id = result.execution_id
    output: list[Any] = []
    for item in (getattr(runtime, "findings", None) or []):
        if str(_value(item, "recorded_by_execution_id") or "") == execution_id:
            output.append(item)
            continue
        for evidence in (_value(item, "evidence") or []):
            source_execution_ids = {
                str(value) for value in (_value(evidence, "source_execution_ids") or [])
            }
            if execution_id in source_execution_ids:
                output.append(item)
                break
    return output


def _classification(
    *,
    result: ExecutionResult,
    task: Optional[AnalysisTask],
    verification_status: str,
    verification_failure_count: int,
    artifact_count: int,
    verified_evidence_count: int,
    finding_count: int,
) -> tuple[bool, Optional[str], Optional[str]]:
    status = result.status
    message = _error_message(result)

    if status == "cancelled":
        return False, "cancelled", "Task execution was cancelled"

    if status != "succeeded":
        if verification_failure_count or "verification" in message.lower():
            return False, "verification", message or "Execution verification failed"
        if "evidence generation failed" in message.lower():
            return False, "evidence", message or "Evidence generation failed"
        if result.tool_name == "finish_report":
            return False, "report", message or "Report validation failed"
        return False, "execution", message or "Task execution failed"

    if verification_status == "failed":
        return False, "verification", "One or more verification checks failed"
    if artifact_count and verified_evidence_count == 0:
        return (
            False,
            "evidence",
            "Execution produced artifacts but no verified evidence",
        )
    if task is not None and task.method == "record_finding" and finding_count == 0:
        return False, "finding", "record_finding produced no Runtime finding"
    return True, None, None


def build_task_evaluation(runtime: Any, result: ExecutionResult) -> "TaskEvaluation":
    """Build one deterministic evaluation for a Runtime execution."""
    task: Optional[AnalysisTask] = None
    if getattr(runtime, "scheduler", None) is not None and result.task_id:
        try:
            task = runtime.scheduler.get_task(result.task_id)
        except KeyError:
            task = None

    verifications = _verification_records(runtime, result)
    verification_failures = [
        item
        for item in verifications
        if _value(item, "status") != "passed" or _value(item, "passed") is not True
    ]
    if verification_failures:
        verification_status = "failed"
    elif verifications:
        verification_status = "passed"
    else:
        verification_status = "skipped"

    evidence_items = _related_evidence(runtime, result)
    verified_evidence = [
        item
        for item in evidence_items
        if _value(item, "verification_status") == "verified"
        and _value(item, "verification_result_id")
    ]
    findings = _related_findings(runtime, result)
    artifact_count = len(result.output_artifact_ids or [])

    passed, failure_kind, failure_reason = _classification(
        result=result,
        task=task,
        verification_status=verification_status,
        verification_failure_count=len(verification_failures),
        artifact_count=artifact_count,
        verified_evidence_count=len(verified_evidence),
        finding_count=len(findings),
    )

    return TaskEvaluation(
        run_id=result.run_id,
        task_id=result.task_id or "",
        session_id=str(getattr(runtime, "session_id", "")),
        execution_id=result.execution_id,
        task_type=str(_value(task, "task_type", "unknown")),
        executor_type=str(_value(task, "executor_type", "unknown")),
        method=_value(task, "method", result.tool_name),
        skill_name=result.skill_name,
        skill_fallback=result.skill_name is None,
        status=str(result.status),
        verification_status=verification_status,
        verification_count=len(verifications),
        verification_failure_count=len(verification_failures),
        artifact_count=artifact_count,
        evidence_count=len(evidence_items),
        verified_evidence_count=len(verified_evidence),
        finding_count=len(findings),
        report_generated=(
            result.tool_name == "finish_report"
            and result.status == "succeeded"
            and artifact_count > 0
        ),
        duration_ms=max(0.0, float(result.duration_ms or 0.0)),
        passed=passed,
        failure_kind=failure_kind,
        failure_reason=failure_reason,
        metadata={
            "error_type": (
                result.error.get("type")
                if isinstance(result.error, dict) and result.error
                else None
            ),
            "output_artifact_ids": list(result.output_artifact_ids or []),
            "verification_ids": [
                str(_value(item, "verification_id")) for item in verifications
            ],
            "evidence_ids": [
                str(_value(item, "evidence_id")) for item in evidence_items
            ],
            "finding_ids": [
                str(_value(item, "finding_id")) for item in findings
            ],
        },
    )


def _failure_fingerprint(evaluation: "TaskEvaluation") -> str:
    raw = "|".join([
        str(evaluation.task_type),
        str(evaluation.executor_type),
        str(evaluation.method or ""),
        str(evaluation.skill_name or "fallback"),
        str(evaluation.failure_kind or "unknown"),
        str(evaluation.failure_reason or "")[:300].casefold(),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


_FAILURE_RECOMMENDATIONS = {
    "execution": (
        "Inspect the execution error and input schema; retry only after the "
        "data or tool arguments are corrected."
    ),
    "verification": (
        "Recheck the calculation against the verification contract before retrying."
    ),
    "evidence": (
        "Ensure output artifacts are registered and pass deterministic Evidence checks."
    ),
    "finding": (
        "Create findings only through FindingBuilder from verified evidence."
    ),
    "report": (
        "Revise report structure and keep all numbers traceable to verified evidence."
    ),
    "cancelled": "Resume only when the user requests a new run.",
}


def build_failure_cases(evaluations: Iterable["TaskEvaluation"]) -> list["FailureCase"]:
    """Aggregate repeated failure signatures into durable failure cases."""
    grouped: dict[str, list[TaskEvaluation]] = defaultdict(list)
    for evaluation in evaluations:
        if evaluation.passed or not evaluation.failure_kind:
            continue
        grouped[evaluation.failure_kind + ":" + _failure_fingerprint(evaluation)].append(
            evaluation
        )

    cases: list[FailureCase] = []
    for signature, items in grouped.items():
        ordered = sorted(items, key=lambda item: item.created_at)
        first = ordered[0]
        last = ordered[-1]
        fingerprint = _failure_fingerprint(first)
        cases.append(
            FailureCase(
                failure_id=f"failure_{fingerprint[:16]}",
                fingerprint=fingerprint,
                signature=signature,
                session_id=first.session_id,
                run_id=last.run_id,
                task_id=last.task_id,
                execution_id=last.execution_id,
                task_type=first.task_type,
                executor_type=first.executor_type,
                method=first.method,
                skill_name=first.skill_name,
                failure_kind=first.failure_kind,
                message=last.failure_reason or "Task failed",
                recommendation=_FAILURE_RECOMMENDATIONS.get(
                    first.failure_kind,
                    "Review the Runtime evaluation metadata before retrying.",
                ),
                occurrences=len(items),
                first_seen_at=first.created_at,
                last_seen_at=last.created_at,
                metadata={"task_ids": [item.task_id for item in ordered]},
            )
        )
    return sorted(cases, key=lambda item: item.last_seen_at, reverse=True)


def build_skill_quality(evaluations: Iterable["TaskEvaluation"]) -> list["SkillQuality"]:
    """Aggregate deterministic task outcomes into skill-quality records."""
    grouped: dict[tuple[str, str, str], list[TaskEvaluation]] = defaultdict(list)
    for evaluation in evaluations:
        skill_name = evaluation.skill_name or "__fallback__"
        key = (skill_name, str(evaluation.task_type), str(evaluation.executor_type))
        grouped[key].append(evaluation)

    records: list[SkillQuality] = []
    for (skill_name, task_type, executor_type), items in grouped.items():
        ordered = sorted(items, key=lambda item: item.created_at)
        successes = [item for item in items if item.passed]
        verification_failures = [
            item for item in items if item.failure_kind == "verification"
        ]
        evidence_failures = [item for item in items if item.failure_kind == "evidence"]
        finding_failures = [item for item in items if item.failure_kind == "finding"]
        report_failures = [item for item in items if item.failure_kind == "report"]
        attempts = len(items)
        success_count = len(successes)
        success_rate = success_count / attempts if attempts else 0.0
        quality_score = sum(
            1.0 if item.passed else 0.25 for item in items
        ) / attempts
        if attempts >= 3 and success_rate >= 0.8:
            recommendation = "reliable"
        elif attempts >= 2 and success_rate < 0.5:
            recommendation = "needs_review"
        else:
            recommendation = "neutral"

        records.append(
            SkillQuality(
                skill_name=None if skill_name == "__fallback__" else skill_name,
                task_type=task_type,
                executor_type=executor_type,
                attempts=attempts,
                successes=success_count,
                failures=attempts - success_count,
                verification_failures=len(verification_failures),
                evidence_failures=len(evidence_failures),
                finding_failures=len(finding_failures),
                report_failures=len(report_failures),
                success_rate=round(success_rate, 4),
                quality_score=round(quality_score, 4),
                recommendation=recommendation,
                last_used_at=ordered[-1].created_at,
                metadata={"task_ids": [item.task_id for item in ordered]},
            )
        )
    return sorted(
        records,
        key=lambda item: (-item.attempts, item.skill_name or "__fallback__", item.task_type),
    )


class TaskEvaluation(BaseModel):
    evaluation_id: str = Field(default_factory=lambda: new_id("eval"))
    run_id: str
    task_id: str
    session_id: str
    execution_id: str
    task_type: str = "unknown"
    executor_type: str = "unknown"
    method: Optional[str] = None
    skill_name: Optional[str] = None
    skill_fallback: bool = True
    status: str
    verification_status: str = "skipped"
    verification_count: int = Field(default=0, ge=0)
    verification_failure_count: int = Field(default=0, ge=0)
    artifact_count: int = Field(default=0, ge=0)
    evidence_count: int = Field(default=0, ge=0)
    verified_evidence_count: int = Field(default=0, ge=0)
    finding_count: int = Field(default=0, ge=0)
    report_generated: bool = False
    duration_ms: float = Field(default=0.0, ge=0)
    passed: bool
    failure_kind: Optional[str] = None
    failure_reason: Optional[str] = None
    created_at: str = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FailureCase(BaseModel):
    failure_id: str
    fingerprint: str
    signature: str
    session_id: str
    run_id: str
    task_id: str
    execution_id: str
    task_type: str
    executor_type: str
    method: Optional[str] = None
    skill_name: Optional[str] = None
    failure_kind: str
    message: str
    recommendation: str
    occurrences: int = Field(default=1, ge=1)
    first_seen_at: str
    last_seen_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillQuality(BaseModel):
    skill_name: Optional[str] = None
    task_type: str
    executor_type: str
    attempts: int = Field(default=0, ge=0)
    successes: int = Field(default=0, ge=0)
    failures: int = Field(default=0, ge=0)
    verification_failures: int = Field(default=0, ge=0)
    evidence_failures: int = Field(default=0, ge=0)
    finding_failures: int = Field(default=0, ge=0)
    report_failures: int = Field(default=0, ge=0)
    success_rate: float = Field(default=0.0, ge=0, le=1)
    quality_score: float = Field(default=0.0, ge=0, le=1)
    recommendation: SkillRecommendation = "neutral"
    last_used_at: str = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LearningSnapshot(BaseModel):
    version: int = _LEARNING_VERSION
    updated_at: str = Field(default_factory=utc_now)
    evaluations: list[TaskEvaluation] = Field(default_factory=list)
    failure_cases: list[FailureCase] = Field(default_factory=list)
    skill_quality: list[SkillQuality] = Field(default_factory=list)


class LearningMemoryStore:
    """Atomic JSON memory for evaluations, failure cases, and skill quality."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._snapshot: LearningSnapshot | None = None

    def _load(self) -> LearningSnapshot:
        if self._snapshot is not None:
            return self._snapshot
        if not self.path.exists():
            return LearningSnapshot()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._snapshot = LearningSnapshot.model_validate(raw)
        except (OSError, ValueError, json.JSONDecodeError):
            self._snapshot = LearningSnapshot()
        return self._snapshot

    def snapshot(self) -> LearningSnapshot:
        return self._load().model_copy(deep=True)

    def skill_quality(self) -> list[SkillQuality]:
        return self.snapshot().skill_quality

    def failure_cases(self) -> list[FailureCase]:
        return self.snapshot().failure_cases

    def recommendation_for_skill(self, skill_name: str) -> SkillRecommendation:
        matching = [
            item
            for item in self.skill_quality()
            if item.skill_name == skill_name
        ]
        if not matching:
            return "neutral"
        if any(item.recommendation == "needs_review" for item in matching):
            return "needs_review"
        if all(item.recommendation == "reliable" for item in matching):
            return "reliable"
        return "neutral"

    def record_evaluations(
        self,
        evaluations: Iterable[TaskEvaluation],
        *,
        save: bool = True,
    ) -> LearningSnapshot:
        snapshot = self._load()
        incoming = [
            item if isinstance(item, TaskEvaluation) else TaskEvaluation.model_validate(item)
            for item in evaluations
        ]
        by_task_id = {
            item.task_id: item for item in snapshot.evaluations
        }
        for item in incoming:
            by_task_id[item.task_id] = item
        snapshot.evaluations = sorted(
            by_task_id.values(), key=lambda item: item.created_at
        )
        snapshot.failure_cases = build_failure_cases(snapshot.evaluations)
        snapshot.skill_quality = build_skill_quality(snapshot.evaluations)
        snapshot.updated_at = utc_now()
        self._snapshot = snapshot
        if save:
            self.save()
        return snapshot.model_copy(deep=True)

    def save(self) -> None:
        snapshot = self._load()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".learning_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    snapshot.model_dump(mode="json"),
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        except BaseException:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
