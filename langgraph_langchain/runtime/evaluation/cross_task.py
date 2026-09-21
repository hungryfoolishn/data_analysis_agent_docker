"""Cross-task consistency verification for Runtime V8.5."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
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
    """Detect contradictory values for the same metric context across tasks."""

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
            distinct_tasks = {
                str(_value(evaluation, "task_id")) for evaluation, _ in entries
            }
            if len(distinct_tasks) < 2:
                continue

            ordered = sorted(entries, key=lambda pair: float(_value(pair[1], "value", 0.0)))
            values = [float(_value(item, "value", 0.0)) for _, item in ordered]
            max_delta = values[-1] - values[0] if values else 0.0
            tolerance = max(
                [float(_value(item, "tolerance", 0.0) or 0.0) for _, item in ordered]
                or [0.0]
            )
            if max_delta <= tolerance:
                continue

            first_evaluation, first_observation = ordered[0]
            metric, period, dimension, group, filters_json = key
            raw = "|".join([
                metric,
                period,
                dimension,
                group,
                filters_json,
                ",".join(sorted(distinct_tasks)),
                ",".join(f"{value:.12g}" for value in values),
            ])
            issue_id = f"consistency_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"

            issues.append(
                ConsistencyIssue(
                    metric=metric,
                    period=period or None,
                    dimension=dimension or None,
                    group=group or None,
                    filters=_value(first_observation, "filters", {}) or {},
                    task_ids=sorted(distinct_tasks),
                    execution_ids=[
                        str(_value(evaluation, "execution_id"))
                        for evaluation, _ in ordered
                    ],
                    values=values,
                    tolerance=tolerance,
                    max_delta=round(max_delta, 10),
                    message=(
                        f"Metric '{metric}' has inconsistent values across tasks: "
                        f"{values[0]} vs {values[-1]} (delta={max_delta}, tolerance={tolerance})"
                    ),
                    issue_id=issue_id,
                )
            )

        return sorted(issues, key=lambda item: (-item.max_delta, item.metric, item.period or ""))
