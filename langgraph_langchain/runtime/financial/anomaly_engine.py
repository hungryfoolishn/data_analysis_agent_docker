"""Historical-baseline anomaly detection for Runtime V9.2."""

from __future__ import annotations

import math
from statistics import mean, pstdev

from .models import FinancialAnomalySignal, FinancialObservation


class FinancialAnomalyEngine:
    """Detect deviations from a company's own historical metric baseline."""

    def __init__(self, *, threshold: float = 2.0, minimum_history: int = 3) -> None:
        self.threshold = threshold
        self.minimum_history = minimum_history

    def detect(
        self,
        observations: list[FinancialObservation],
        *,
        growth_only: bool = True,
    ) -> list[FinancialAnomalySignal]:
        grouped: dict[tuple[str, str], list[FinancialObservation]] = {}
        for observation in observations:
            if growth_only and not observation.metric_id.endswith("_growth"):
                continue
            grouped.setdefault(
                (observation.company_name, observation.metric_id),
                [],
            ).append(observation)

        anomalies: list[FinancialAnomalySignal] = []
        for (company_name, metric_id), items in grouped.items():
            items = sorted(items, key=lambda item: item.period)
            for index, current in enumerate(items):
                history = [item.value for item in items[:index]]
                if len(history) < self.minimum_history:
                    continue
                baseline_mean = mean(history)
                baseline_std = pstdev(history)
                if baseline_std == 0:
                    continue
                z_score = (current.value - baseline_mean) / baseline_std
                if not math.isfinite(z_score) or abs(z_score) < self.threshold:
                    continue
                direction = "异常上升" if z_score > 0 else "异常下降"
                anomalies.append(FinancialAnomalySignal(
                    company_name=company_name,
                    period=current.period,
                    metric_id=metric_id,
                    metric_name=current.metric_name,
                    value=current.value,
                    historical_mean=round(baseline_mean, 6),
                    historical_std=round(baseline_std, 6),
                    z_score=round(z_score, 6),
                    threshold=self.threshold,
                    message=(
                        f"{current.metric_name}偏离历史均值 "
                        f"{z_score:.2f} 个标准差，判定为{direction}。"
                    ),
                ))
        return anomalies
