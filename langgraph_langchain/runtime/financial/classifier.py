"""Task classification for financial analysis questions."""

from __future__ import annotations

import re
from typing import Iterable

from .models import FinancialQuery


class FinancialTaskType:
    COMPANY_OVERVIEW = "COMPANY_OVERVIEW"
    FINANCIAL_TREND = "FINANCIAL_TREND"
    REVENUE_ANALYSIS = "REVENUE_ANALYSIS"
    PROFIT_ANALYSIS = "PROFIT_ANALYSIS"
    PROFITABILITY_ANALYSIS = "PROFITABILITY_ANALYSIS"
    CASHFLOW_ANALYSIS = "CASHFLOW_ANALYSIS"
    SOLVENCY_ANALYSIS = "SOLVENCY_ANALYSIS"
    OPERATING_ANALYSIS = "OPERATING_ANALYSIS"
    PEER_COMPARISON = "PEER_COMPARISON"
    ANOMALY_ANALYSIS = "ANOMALY_ANALYSIS"
    RISK_ANALYSIS = "RISK_ANALYSIS"
    COMPREHENSIVE_ANALYSIS = "COMPREHENSIVE_ANALYSIS"


ALL_TASK_TYPES = [
    FinancialTaskType.COMPANY_OVERVIEW,
    FinancialTaskType.FINANCIAL_TREND,
    FinancialTaskType.REVENUE_ANALYSIS,
    FinancialTaskType.PROFIT_ANALYSIS,
    FinancialTaskType.PROFITABILITY_ANALYSIS,
    FinancialTaskType.CASHFLOW_ANALYSIS,
    FinancialTaskType.SOLVENCY_ANALYSIS,
    FinancialTaskType.OPERATING_ANALYSIS,
    FinancialTaskType.PEER_COMPARISON,
    FinancialTaskType.ANOMALY_ANALYSIS,
    FinancialTaskType.RISK_ANALYSIS,
    FinancialTaskType.COMPREHENSIVE_ANALYSIS,
]


class FinancialTaskClassifier:
    """Deterministic keyword classifier for first-stage financial tasks."""

    _RULES = [
        (FinancialTaskType.PEER_COMPARISON, ("同业", "对比", "比较", "peer", "compare")),
        (FinancialTaskType.COMPREHENSIVE_ANALYSIS, ("综合分析", "综合基本面", "comprehensive", "全面分析")),
        (FinancialTaskType.RISK_ANALYSIS, ("风险", "恶化", "risk")),
        (FinancialTaskType.ANOMALY_ANALYSIS, ("异常", "波动", "anomaly")),
        (FinancialTaskType.CASHFLOW_ANALYSIS, ("现金流", "自由现金流", "cash flow", "ocf")),
        (FinancialTaskType.SOLVENCY_ANALYSIS, ("偿债", "资产负债率", "流动比率", "速动比率", "solvency")),
        (FinancialTaskType.OPERATING_ANALYSIS, ("营运", "周转率", "operating efficiency")),
        (FinancialTaskType.PROFITABILITY_ANALYSIS, ("盈利能力", "毛利率", "净利率", "roe", "roa", "profitability")),
        (FinancialTaskType.PROFIT_ANALYSIS, ("净利润", "利润分析", "net profit")),
        (FinancialTaskType.REVENUE_ANALYSIS, ("营业收入", "收入分析", "销售额", "revenue")),
        (FinancialTaskType.FINANCIAL_TREND, ("趋势", "增长", "变化", "trend", "growth")),
        (FinancialTaskType.COMPANY_OVERVIEW, ("概况", "经营情况", "overview", "基本面")),
    ]

    def classify(self, question: str) -> str:
        text = str(question or "").casefold()
        for task_type, keywords in self._RULES:
            if any(keyword.casefold() in text for keyword in keywords):
                return task_type
        return FinancialTaskType.COMPANY_OVERVIEW

    def extract_companies(self, question: str, known_companies: Iterable[str]) -> list[str]:
        text = str(question or "")
        return [company for company in known_companies if company and company in text]

    def extract_period(self, question: str) -> tuple[int | None, int | None]:
        years = [int(item) for item in re.findall(r"(?<!\d)(20\d{2})(?!\d)", str(question))]
        if len(years) >= 2:
            return min(years), max(years)
        if years:
            return years[0], years[0]
        return None, None

    def build_query(
        self,
        question: str,
        known_companies: Iterable[str],
        default_start: int | None = None,
        default_end: int | None = None,
        metrics: list[str] | None = None,
    ) -> FinancialQuery:
        task_type = self.classify(question)
        companies = self.extract_companies(question, known_companies)
        start_year, end_year = self.extract_period(question)
        return FinancialQuery(
            question=question,
            task_type=task_type,
            company_names=companies,
            metrics=metrics or [],
            start_year=start_year or default_start,
            end_year=end_year or default_end,
        )
