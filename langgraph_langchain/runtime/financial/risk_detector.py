"""Rule-based financial risk detection."""

from __future__ import annotations

from typing import Iterable

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
            "test": lambda value: value < 50,
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
                metric_id = observation.get("metric_id")
                value = float(observation.get("value", 0.0))
                company_name = str(observation.get("company_name", ""))
                period = str(observation.get("period", ""))
            else:
                metric_id = observation.metric_id
                value = float(observation.value)
                company_name = observation.company_name
                period = observation.period
            rule = self.RULES.get(metric_id)
            if not rule:
                continue
            if rule["test"](value):
                signals.append(FinancialRiskSignal(
                    company_name=company_name,
                    period=period,
                    signal_id=f"{metric_id}_{company_name}_{period}",
                    severity="high" if value < -20 or value > 90 else "medium",
                    message=rule["message"],
                    metric_id=metric_id,
                    value=value,
                ))
        return signals
