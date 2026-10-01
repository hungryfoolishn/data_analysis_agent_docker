"""Deterministic task understanding for Runtime V10.0."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass

from .models import (
    CompanyResolution,
    FinancialTaskUnderstanding,
    PeriodResolution,
    PlanningReadiness,
)


_OBJECTIVE_PATTERNS = {
    "revenue_trend": ("收入", "营收", "销售额", "revenue", "sales"),
    "profit_trend": ("利润", "profit"),
    "profitability": (
        "盈利能力", "毛利率", "净利率", "利润率", "roe", "roa",
        "profitability", "margin",
    ),
    "cashflow": (
        "现金流", "自由现金流", "经营现金流", "cash flow", "ocf", "fcf",
    ),
    "solvency": (
        "偿债", "资产负债率", "流动比率", "速动比率", "solvency",
    ),
    "operating_efficiency": (
        "营运", "周转率", "operating efficiency", "turnover",
    ),
    "risk": ("风险", "恶化", "risk"),
    "peer_comparison": (
        "比较", "对比", "同业", "peer", "compare", "comparison",
    ),
    "comprehensive": (
        "综合分析", "全面分析", "经营情况", "基本面", "comprehensive",
        "overview",
    ),
}

_OBJECTIVE_METRICS = {
    "revenue_trend": ("revenue", "revenue_growth"),
    "profit_trend": ("net_profit", "net_profit_growth"),
    "profitability": (
        "gross_margin", "operating_margin", "net_margin", "roe", "roa",
    ),
    "cashflow": (
        "operating_cash_flow", "free_cash_flow", "ocf_to_net_income",
    ),
    "solvency": ("debt_to_asset", "current_ratio", "quick_ratio"),
    "operating_efficiency": (
        "receivable_turnover", "inventory_turnover", "asset_turnover",
    ),
    "risk": (
        "debt_to_asset", "revenue_growth", "net_profit_growth",
        "ocf_to_net_income",
    ),
    "peer_comparison": ("revenue", "revenue_growth", "net_margin"),
    "comprehensive": (
        "revenue", "revenue_growth", "net_profit", "net_profit_growth",
        "net_margin", "roe", "debt_to_asset", "operating_cash_flow",
        "free_cash_flow",
    ),
}


_COMPANY_ALIASES = {
    "贵州茅台": ("Guizhou Moutai", "Kweichow Moutai", "Moutai"),
    "五粮液": ("Wuliangye",),
    "泸州老窖": ("Luzhou Laojiao", "Laojiao"),
}


@dataclass(frozen=True)
class _CompanyCandidate:
    name: str
    company_id: str
    stock_code: str = ""


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _candidate(item: object) -> _CompanyCandidate | None:
    if isinstance(item, str):
        name = item.strip()
        return _CompanyCandidate(name=name, company_id=name) if name else None
    if isinstance(item, dict):
        name = str(item.get("company_name", "")).strip()
        if not name:
            return None
        return _CompanyCandidate(
            name=name,
            company_id=str(item.get("company_id", name)),
            stock_code=str(item.get("stock_code", "")),
        )

    name = str(getattr(item, "company_name", "")).strip()
    if not name:
        return None
    return _CompanyCandidate(
        name=name,
        company_id=str(getattr(item, "company_id", name)),
        stock_code=str(getattr(item, "stock_code", "")),
    )


class FinancialTaskUnderstandingBuilder:
    """Convert a natural-language financial question into a stable task spec."""

    def __init__(
        self,
        known_companies: Iterable[object] = (),
        *,
        reference_year: int = 2025,
    ) -> None:
        self.reference_year = reference_year
        self._companies = [
            candidate
            for candidate in (
                _candidate(item) for item in known_companies
            )
            if candidate
        ]

    def build(self, question: str) -> FinancialTaskUnderstanding:
        raw_question = str(question or "").strip()
        text = raw_question.casefold()
        companies = self._resolve_companies(raw_question)
        objectives = self._extract_objectives(text)
        period_range, ambiguities = self._extract_period_range(raw_question)
        comparison_enabled = "peer_comparison" in objectives and len(companies) >= 2
        task_type = self._infer_task_type(objectives)

        required_metrics: list[str] = []
        for objective in objectives:
            for metric_id in _OBJECTIVE_METRICS.get(objective, ()):
                if metric_id not in required_metrics:
                    required_metrics.append(metric_id)

        missing_information: list[str] = []
        planning_diagnostics: list[str] = []
        if not raw_question:
            planning_diagnostics.append("EMPTY_QUESTION")
        if not companies:
            missing_information.append("company_names_unresolved")
            planning_diagnostics.append("UNRESOLVED_COMPANY")
        if period_range is None:
            missing_information.append("period_range_unresolved")
            planning_diagnostics.append("UNRESOLVED_PERIOD")
        if not objectives:
            missing_information.append("analysis_objectives_unresolved")
            planning_diagnostics.append("NO_ANALYSIS_OBJECTIVE")

        planning_readiness = (
            PlanningReadiness.NOT_EXECUTABLE
            if "EMPTY_QUESTION" in planning_diagnostics
            else (
                PlanningReadiness.READY
                if not planning_diagnostics
                else PlanningReadiness.NEEDS_CLARIFICATION
            )
        )

        confidence = 0.4
        if companies:
            confidence += 0.2
        if period_range is not None:
            confidence += 0.2
        if objectives:
            confidence += 0.15
        confidence = round(min(confidence, 0.95), 2)

        return FinancialTaskUnderstanding(
            query_id=_stable_id("query", raw_question),
            raw_question=raw_question,
            task_type=task_type,
            companies=companies,
            period_range=period_range,
            objectives=objectives,
            required_metrics=required_metrics,
            comparison_enabled=comparison_enabled,
            ambiguities=ambiguities,
            missing_information=missing_information,
            planning_readiness=planning_readiness,
            planning_diagnostics=planning_diagnostics,
            confidence=confidence,
        )

    def _resolve_companies(self, question: str) -> list[CompanyResolution]:
        text = question.casefold()
        resolved: list[CompanyResolution] = []
        for candidate in self._companies:
            match_terms = [
                candidate.name,
                *(alias.casefold() for alias in _COMPANY_ALIASES.get(candidate.name, ())),
                candidate.stock_code,
            ]
            matched = any(term and term.casefold() in text for term in match_terms)
            if not matched:
                continue
            if any(item.company_id == candidate.company_id for item in resolved):
                continue
            resolved.append(CompanyResolution(
                company_name=candidate.name,
                company_id=candidate.company_id,
                stock_code=candidate.stock_code,
            ))
        return resolved

    def _extract_objectives(self, text: str) -> list[str]:
        objectives: list[str] = []
        for objective, patterns in _OBJECTIVE_PATTERNS.items():
            if objective in objectives:
                continue
            if any(pattern.casefold() in text for pattern in patterns):
                objectives.append(objective)

        if "comprehensive" in objectives:
            # Comprehensive expands the default scope but never discards an
            # explicitly requested objective such as solvency, operating
            # efficiency, risk, or peer comparison.
            expanded = [
                "revenue_trend",
                "profit_trend",
                "profitability",
                "cashflow",
            ]
            for objective in objectives:
                if objective != "comprehensive" and objective not in expanded:
                    expanded.append(objective)
            objectives = expanded

        if "peer_comparison" in objectives and len(objectives) > 1:
            if "comprehensive" not in objectives:
                objectives.append("comprehensive")

        return objectives

    def _extract_period_range(
        self,
        question: str,
    ) -> tuple[PeriodResolution | None, list[str]]:
        years = [
            int(item)
            for item in re.findall(r"(?<!\d)(20\d{2})(?!\d)", question)
        ]
        if len(years) >= 2:
            return PeriodResolution(
                start_year=min(years),
                end_year=max(years),
                period_type="ANNUAL",
            ), []
        if years:
            return PeriodResolution(
                start_year=years[0],
                end_year=years[0],
                period_type="ANNUAL",
            ), []

        relative_match = re.search(
            r"(?:过去|近|最近)\s*([0-9]+|[一二两三四五六七八九十]+)\s*年",
            question,
        )
        if relative_match:
            raw_count = relative_match.group(1)
            chinese_numbers = {
                "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
                "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
            }
            count = (
                int(raw_count)
                if raw_count.isdigit()
                else chinese_numbers.get(raw_count)
            )
            if count and count > 0:
                return PeriodResolution(
                    start_year=self.reference_year - count + 1,
                    end_year=self.reference_year,
                    period_type="ANNUAL",
                ), ["relative_period_resolved_with_reference_year"]

        return None, []

    @staticmethod
    def _infer_task_type(objectives: list[str]) -> str:
        if "comprehensive" in objectives or len(objectives) > 1:
            return "COMPREHENSIVE_ANALYSIS"
        if "peer_comparison" in objectives:
            return "PEER_COMPARISON"
        if "risk" in objectives:
            return "RISK_ANALYSIS"
        if "cashflow" in objectives:
            return "CASHFLOW_ANALYSIS"
        if "solvency" in objectives:
            return "SOLVENCY_ANALYSIS"
        if "operating_efficiency" in objectives:
            return "OPERATING_ANALYSIS"
        if "profitability" in objectives:
            return "PROFITABILITY_ANALYSIS"
        if "profit_trend" in objectives:
            return "PROFIT_ANALYSIS"
        if "revenue_trend" in objectives:
            return "REVENUE_ANALYSIS"
        return "COMPANY_OVERVIEW"
