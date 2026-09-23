"""Deterministic financial analysis workflows for Runtime V9."""

from __future__ import annotations


from .classifier import FinancialTaskClassifier, FinancialTaskType
from .data_service import FinancialDataService
from .metrics import calculate_metric, financial_metric_registry
from .models import (
    FinancialAnalysisResult,
    FinancialCalculation,
    FinancialObservation,
    FinancialQuery,
    FinancialRiskSignal,
)


class FinancialAnalysisWorkflow:
    """Route financial questions through deterministic metric workflows."""

    _TASK_METRICS = {
        FinancialTaskType.COMPANY_OVERVIEW: [
            "revenue", "net_profit", "net_margin", "roe", "debt_to_asset",
        ],
        FinancialTaskType.FINANCIAL_TREND: [
            "revenue", "revenue_growth", "net_profit", "net_profit_growth",
        ],
        FinancialTaskType.REVENUE_ANALYSIS: ["revenue", "revenue_growth"],
        FinancialTaskType.PROFIT_ANALYSIS: ["net_profit", "net_profit_growth"],
        FinancialTaskType.PROFITABILITY_ANALYSIS: [
            "revenue", "revenue_growth", "gross_margin",
            "operating_margin", "net_margin", "roe",
        ],
        FinancialTaskType.CASHFLOW_ANALYSIS: [
            "operating_cash_flow", "free_cash_flow", "ocf_to_net_income",
        ],
        FinancialTaskType.SOLVENCY_ANALYSIS: [
            "debt_to_asset", "current_ratio", "quick_ratio",
        ],
        FinancialTaskType.OPERATING_ANALYSIS: [
            "receivable_turnover", "inventory_turnover", "asset_turnover",
        ],
        FinancialTaskType.PEER_COMPARISON: [
            "revenue", "revenue_growth", "net_margin",
        ],
        FinancialTaskType.ANOMALY_ANALYSIS: [
            "revenue_growth", "net_profit_growth",
        ],
        FinancialTaskType.RISK_ANALYSIS: [
            "revenue_growth", "net_profit_growth", "debt_to_asset",
            "ocf_to_net_income",
        ],
        FinancialTaskType.COMPREHENSIVE_ANALYSIS: [
            "revenue", "revenue_growth", "net_profit", "gross_margin",
            "net_margin", "roe", "debt_to_asset", "operating_cash_flow",
            "free_cash_flow",
        ],
    }

    def __init__(self, data_service: FinancialDataService) -> None:
        self.data_service = data_service
        self.classifier = FinancialTaskClassifier()
        self.risk_detector = __import__(
            "langgraph_langchain.runtime.financial.risk_detector",
            fromlist=["FinancialRiskDetector"],
        ).FinancialRiskDetector()

    def run(self, query: FinancialQuery | str) -> FinancialAnalysisResult:
        query = self._normalize_query(query)
        task_type = query.task_type or self.classifier.classify(query.question)
        metrics = query.metrics or self._TASK_METRICS.get(task_type, ["revenue"])
        observations: list[FinancialObservation] = []
        calculations: list[FinancialCalculation] = []

        company_names = query.company_names or [
            item.company_name for item in self.data_service.list_companies()
        ]
        for company_name in company_names:
            company = self.data_service.resolve_company(company_name)
            periods = self.data_service.periods_for(company.company_id)
            selected = [
                period
                for period in periods
                if query.start_year is None
                or query.end_year is None
                or query.start_year <= int(period) <= query.end_year
            ]
            if not selected:
                selected = periods[-min(5, len(periods)):]

            for period in selected:
                previous_period = self.data_service.previous_period(
                    company.company_id, period
                )
                income, balance, cash_flow, previous_income = self.data_service.statements(
                    company.company_id,
                    period,
                    previous_period,
                )
                for metric_id in metrics:
                    value, inputs = calculate_metric(
                        metric_id,
                        income=income,
                        balance=balance,
                        cash_flow=cash_flow,
                        previous_income=previous_income,
                    )
                    definition = financial_metric_registry.get(metric_id)
                    calculation = FinancialCalculation(
                        metric_id=metric_id,
                        company_name=company.company_name,
                        period=period,
                        formula=definition.formula,
                        inputs={key: round(float(item), 6) for key, item in inputs.items()},
                        result=round(value, 6),
                        unit=definition.unit,
                    )
                    calculations.append(calculation)
                    observations.append(FinancialObservation(
                        company_name=company.company_name,
                        stock_code=company.stock_code,
                        period=period,
                        metric_id=metric_id,
                        metric_name=definition.name,
                        value=value,
                        unit=definition.unit,
                        formula=definition.formula,
                        fact=(
                            f"{company.company_name} {period} "
                            f"{definition.name}为{value:,.4f}{definition.unit}"
                        ),
                        calculation=calculation,
                    ))

        risk_signals: list[FinancialRiskSignal] = self.risk_detector.detect(observations)
        result = FinancialAnalysisResult(
            query=query,
            task_type=task_type,
            metrics=metrics,
            observations=observations,
            calculations=calculations,
            risk_signals=risk_signals,
            summary=self._summary(task_type, observations, risk_signals),
        )
        result.report_markdown = self._report(result)
        return result

    def _normalize_query(self, query: FinancialQuery | str) -> FinancialQuery:
        if isinstance(query, FinancialQuery):
            return query
        known_companies = [
            item.company_name for item in self.data_service.list_companies()
        ]
        return self.classifier.build_query(query, known_companies)

    def _summary(
        self,
        task_type: str,
        observations: list[FinancialObservation],
        risks: list[FinancialRiskSignal],
    ) -> str:
        if not observations:
            return "未生成金融分析结果。"

        latest_period = max(item.period for item in observations)
        latest = [
            item
            for item in observations
            if item.period == latest_period
            and item.metric_id in {"revenue", "net_profit", "net_margin"}
        ]
        parts = [
            (
                f"{item.company_name} {latest_period} "
                f"{item.metric_name} 为 {item.value:,.2f}{item.unit}"
            )
            for item in latest[:6]
        ]
        summary = "；".join(parts) + "。"
        if risks:
            summary += f" 检测到 {len(risks)} 个规则型风险信号。"
        return summary

    def _report(self, result: FinancialAnalysisResult) -> str:
        lines = [
            "# 公司基本面分析",
            "",
            f"## 分析类型：{result.task_type}",
            "",
            "## 核心指标",
        ]
        for item in result.observations:
            lines.append(
                f"- {item.company_name} / {item.period} / {item.metric_name}: "
                f"{item.value:,.4f}{item.unit}"
            )

        lines.extend(["", "## 计算过程"])
        for item in result.calculations[:50]:
            inputs = ", ".join(
                f"{key}={value:,.4f}" for key, value in item.inputs.items()
            )
            lines.append(
                f"- {item.company_name} / {item.period} / {item.metric_id}: "
                f"{item.formula}; {inputs}; result={item.result:,.4f}{item.unit}"
            )

        if result.risk_signals:
            lines.extend(["", "## 风险信号"])
            for item in result.risk_signals:
                lines.append(
                    f"- {item.company_name} / {item.period}: {item.message} "
                    f"({item.value:,.2f})"
                )

        lines.extend([
            "",
            "## 说明",
            "- 以上内容区分 Fact、Calculation 和 Interpretation。",
            "- 所有数值均来自标准化金融数据，计算过程可追溯。",
            "- 本结果不构成投资建议。",
        ])
        return "\n".join(lines)
