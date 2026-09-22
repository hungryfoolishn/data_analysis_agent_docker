"""Cross-task consistency verification for Runtime V8.5."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from itertools import combinations
from typing import Any, Iterable, Mapping, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from langgraph_langchain.runtime.models import utc_now


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _normalise_filters(filters: Mapping[str, Any] | None) -> str:
    return json.dumps(filters or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _observation_key(observation: Any) -> tuple[str, str, str, str, str]:
    return (
        str(_value(observation, "metric", "")).casefold(),
        str(_value(observation, "period", "")).casefold(),
        str(_value(observation, "dimension", "")).casefold(),
        str(_value(observation, "group", "")).casefold(),
        _normalise_filters(_value(observation, "filters", {})),
    )


class ConsistencyIssue(BaseModel):
    metric: str
    period: Optional[str] = None
    dimension: Optional[str] = None
    group: Optional[str] = None
    filters: dict[str, Any] = Field(default_factory=dict)
    task_ids: list[str] = Field(default_factory=list)
    execution_ids: list[str] = Field(default_factory=list)
    values: list[float] = Field(default_factory=list)
    tolerance: float = Field(default=0.0, ge=0)
    max_delta: float = Field(default=0.0, ge=0)
    message: str
    issue_id: str = Field(default_factory=lambda: f"consistency_{uuid4().hex[:12]}")
    detected_at: str = Field(default_factory=utc_now)


class CrossTaskConsistencyVerifier:
    """Detect contradictory values for the same metric context across tasks.

    Comparisons are pairwise.  A permissive tolerance on one observation must
    not hide a contradiction between two strict observations.
    """

    def verify(self, evaluations: Iterable[Any]) -> list[ConsistencyIssue]:
        grouped: dict[tuple[str, str, str, str, str], list[tuple[Any, Any]]] = defaultdict(list)
        for evaluation in evaluations:
            task_id = str(_value(evaluation, "task_id") or "")
            execution_id = str(_value(evaluation, "execution_id") or "")
            for observation in (_value(evaluation, "metric_observations", []) or []):
                if not task_id or not execution_id:
                    continue
                grouped[_observation_key(observation)].append((evaluation, observation))

        issues: list[ConsistencyIssue] = []
        for key, entries in grouped.items():
            # Pair observations from different tasks only.  Multiple observations
            # within one task are execution lineage, not cross-task contradiction.
            for (left_evaluation, left), (right_evaluation, right) in combinations(
                entries,
                2,
            ):
                left_task_id = str(_value(left_evaluation, "task_id"))
                right_task_id = str(_value(right_evaluation, "task_id"))
                if left_task_id == right_task_id:
                    continue

                left_value = float(_value(left, "value", 0.0))
                right_value = float(_value(right, "value", 0.0))
                left_tolerance = float(_value(left, "tolerance", 0.0) or 0.0)
                right_tolerance = float(_value(right, "tolerance", 0.0) or 0.0)
                tolerance = max(left_tolerance, right_tolerance)
                delta = round(abs(right_value - left_value), 10)
                if delta <= tolerance:
                    continue

                metric, period, dimension, group, filters_json = key
                task_ids = sorted([left_task_id, right_task_id])
                execution_ids = [
                    str(_value(left_evaluation, "execution_id")),
                    str(_value(right_evaluation, "execution_id")),
                ]
                raw = "|".join([
                    metric,
                    period,
                    dimension,
                    group,
                    filters_json,
                    ",".join(task_ids),
                    f"{left_value:.12g}",
                    f"{right_value:.12g}",
                ])

                issues.append(
                    ConsistencyIssue(
                        metric=metric,
                        period=period or None,
                        dimension=dimension or None,
                        group=group or None,
                        filters=_value(left, "filters", {}) or {},
                        task_ids=task_ids,
                        execution_ids=execution_ids,
                        values=sorted([left_value, right_value]),
                        tolerance=tolerance,
                        max_delta=delta,
                        message=(
                            f"Metric '{metric}' has inconsistent values across tasks: "
                            f"{left_task_id}={left_value} vs {right_task_id}={right_value} "
                            f"(delta={delta}, tolerance={tolerance})"
                        ),
                        issue_id=f"consistency_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}",
                    )
                )

        return sorted(
            issues,
            key=lambda item: (-item.max_delta, item.metric, item.period or "", item.task_ids),
        )
