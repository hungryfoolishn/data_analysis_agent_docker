"""Rule-based financial risk detection."""

from __future__ import annotations

from typing import Iterable

from .metrics import financial_metric_registry
from .models import FinancialObservation, FinancialRiskSignal


class FinancialRiskDetector:
    """Detect rule-based financial risks from calculated observations."""

    RULES = {
        "revenue_growth": {
            "test": lambda value: value < 0,
            "message": "营业收入出现负增长",
        },
        "net_profit_growth": {
            "test": lambda value: value < 0,
            "message": "净利润出现负增长",
        },
        "debt_to_asset": {
            "test": lambda value: value > 70,
            "message": "资产负债率高于 70%",
        },
        "ocf_to_net_income": {
            # The machine value is a ratio in `x`, not a percentage.
            # 0.5 means operating cash flow covers 50% of net profit.
            "test": lambda value: value < 0.5,
            "message": "经营现金流与净利润匹配度低于 50%",
        },
    }

    def detect(
        self,
        observations: Iterable[FinancialObservation | dict[str, object]],
    ) -> list[FinancialRiskSignal]:
        signals: list[FinancialRiskSignal] = []
        for observation in observations:
            if isinstance(observation, dict):
                metric_id = str(observation.get("metric_id", ""))
                value = float(observation.get("value", 0.0))
                company_name = str(observation.get("company_name", ""))
                period = str(observation.get("period", ""))
            else:
                metric_id = observation.metric_id
                value = float(observation.value)
                company_name = observation.company_name
                period = observation.period

            rule = self.RULES.get(metric_id)
            if not rule or not rule["test"](value):
                continue

            definition = financial_metric_registry.get_optional(metric_id)
            unit = definition.unit if definition else "x"
            signals.append(FinancialRiskSignal(
                company_name=company_name,
                period=period,
                signal_id=f"{metric_id}_{company_name}_{period}",
                severity=self._severity(metric_id, value),
                message=rule["message"],
                metric_id=metric_id,
                value=value,
                unit=unit,
                display_value=f"{value:,.2f}{unit}",
            ))
        return signals

    @staticmethod
    def _severity(metric_id: str, value: float) -> str:
        if metric_id == "ocf_to_net_income":
            return "high" if value < 0.2 else "medium"
        if metric_id == "debt_to_asset":
            return "high" if value > 80 else "medium"
        return "high" if value < -20 else "medium"
