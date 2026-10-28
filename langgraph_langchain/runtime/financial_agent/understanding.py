"""Deterministic task understanding for Runtime V10.0."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass

from langgraph_langchain.runtime.financial.period import (
    PeriodBasisType,
    PeriodComparisonType,
    PeriodNormalizer,
    PeriodType,
)

from .models import (
    CompanyResolution,
    FinancialTaskUnderstanding,
    GrowthComparisonIntent,
    PeriodResolution,
    PlanningReadiness,
)


_OBJECTIVE_PATTERNS = {
    "revenue_trend": ("收入", "营收", "销售额", "revenue", "sales"),
    "profit_trend": ("利润", "profit"),
    "profitability": (
        "盈利能力", "毛利率", "净利率", "利润率", "roe", "roa",
        "净资产收益率", "总资产收益率", "profitability", "margin",
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
        growth_intents, growth_intent_source, growth_intent_diagnostics = (
            self._extract_growth_intents(raw_question, period_range)
        )
        primary_intent = growth_intents[0] if growth_intents else None
        growth_comparison_type = (
            primary_intent.comparison_type
            if primary_intent
            else PeriodComparisonType.YEAR_OVER_YEAR
        )
        growth_period_basis = (
            primary_intent.period_basis
            if primary_intent
            else PeriodBasisType.CUMULATIVE
        )
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
            growth_comparison_type=growth_comparison_type,
            growth_period_basis=growth_period_basis,
            growth_intents=growth_intents,
            growth_intent_source=growth_intent_source,
            growth_intent_diagnostics=growth_intent_diagnostics,
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

    def _extract_growth_intents(
        self,
        question: str,
        period_range: PeriodResolution | None,
    ) -> tuple[
        list[GrowthComparisonIntent],
        str,
        list[str],
    ]:
        """Return explicit growth intents, their source, and diagnostics.

        "同比和环比" means the caller wants two independent comparisons, not a
        conflict. A true conflict phrase such as "二选一" must be clarified
        instead of silently choosing one comparison.
        """
        text = question.casefold()
        has_yoy = (
            "同比" in text
            or re.search(r"\b(?:yoy|year[-_ ]over[-_ ]year)\b", text) is not None
        )
        has_qoq = (
            "环比" in text
            or "相邻期间" in text
            or re.search(r"\b(?:qoq|quarter[-_ ]over[-_ ]quarter|sequential)\b", text)
            is not None
        )
        has_cumulative_yoy = (
            ("累计" in text and "同比" in text)
            or "年初至今同比" in text
            or re.search(r"\b(?:ytd[_ -]?yoy|year[-_ ]to[-_ ]date[_ -]?yoy)\b", text)
            is not None
        )
        has_sequential_cumulative = (
            ("累计" in text and ("环比" in text or "上一报告期" in text))
            or re.search(
                r"\b(?:sequential[_ -]?cumulative|cumulative[_ -]?qoq)\b",
                text,
            )
            is not None
        )
        has_conflict = has_yoy and has_qoq and any(
            phrase in text
            for phrase in ("二选一", "不能同时", "只选一个", "口径冲突")
        )
        is_ambiguous = (
            has_yoy
            and has_qoq
            and not has_conflict
            and ("还是" in text or ("或" in text and "和" not in text))
        )

        if has_conflict:
            primary_comparison, primary_basis = self._extract_growth_semantics(
                question,
                period_range,
            )
            return (
                [GrowthComparisonIntent(
                    comparison_type=primary_comparison,
                    period_basis=primary_basis,
                    source="conflict",
                )],
                "conflict",
                ["GROWTH_COMPARISON_CONFLICT"],
            )
        if is_ambiguous:
            return (
                [],
                "ambiguous",
                ["GROWTH_COMPARISON_AMBIGUOUS"],
            )

        intents: list[GrowthComparisonIntent] = []
        if has_cumulative_yoy:
            intents.append(GrowthComparisonIntent(
                comparison_type=PeriodComparisonType.YEAR_OVER_YEAR,
                period_basis=PeriodBasisType.CUMULATIVE,
                source="explicit",
            ))
        if has_yoy and not has_cumulative_yoy:
            intents.append(self._intent_for_comparison(
                PeriodComparisonType.YEAR_OVER_YEAR,
                period_range,
            ))
        if has_sequential_cumulative:
            intents.append(GrowthComparisonIntent(
                comparison_type=PeriodComparisonType.SEQUENTIAL,
                period_basis=PeriodBasisType.CUMULATIVE,
                source="explicit",
            ))
        if has_qoq:
            intents.append(self._intent_for_comparison(
                PeriodComparisonType.SEQUENTIAL,
                period_range,
            ))

        unique_intents: list[GrowthComparisonIntent] = []
        seen: set[tuple[str, str]] = set()
        for intent in intents:
            key = (intent.comparison_type.value, intent.period_basis.value)
            if key not in seen:
                unique_intents.append(intent)
                seen.add(key)
        if unique_intents:
            return unique_intents, "explicit", []

        default_intent = self._intent_for_comparison(
            PeriodComparisonType.YEAR_OVER_YEAR,
            period_range,
        )
        default_intent = default_intent.model_copy(update={"source": "default"})
        return [default_intent], "default", []

    def _intent_for_comparison(
        self,
        comparison_type: PeriodComparisonType,
        period_range: PeriodResolution | None,
    ) -> GrowthComparisonIntent:
        has_subannual_period = bool(
            period_range
            and period_range.canonical_periods
            and any(
                self._is_subannual_period(period)
                for period in period_range.canonical_periods
            )
        )
        period_basis = (
            PeriodBasisType.SINGLE_PERIOD
            if has_subannual_period
            else PeriodBasisType.CUMULATIVE
        )
        return GrowthComparisonIntent(
            comparison_type=comparison_type,
            period_basis=period_basis,
            source="default",
        )

    def _extract_growth_semantics(
        self,
        question: str,
        period_range: PeriodResolution | None,
    ) -> tuple[PeriodComparisonType, PeriodBasisType]:
        """Map explicit user wording to comparison type and source basis.

        The period resolver owns dates; this method owns financial comparison
        semantics.  Natural-language intent must survive all the way into the
        execution plan instead of being replaced by a generic default.
        """
        text = question.casefold()
        has_yoy = (
            "同比" in text
            or re.search(r"\b(?:yoy|year[-_ ]over[-_ ]year)\b", text) is not None
        )
        has_qoq = (
            "环比" in text
            or "相邻期间" in text
            or re.search(r"\b(?:qoq|quarter[-_ ]over[-_ ]quarter|sequential)\b", text)
            is not None
        )
        has_cumulative_yoy = (
            ("累计" in text and "同比" in text)
            or "年初至今同比" in text
            or re.search(r"\b(?:ytd[_ -]?yoy|year[-_ ]to[-_ ]date[_ -]?yoy)\b", text)
            is not None
        )
        has_sequential_cumulative = (
            ("累计" in text and ("环比" in text or "上一报告期" in text))
            or re.search(
                r"\b(?:sequential[_ -]?cumulative|cumulative[_ -]?qoq)\b",
                text,
            )
            is not None
        )

        has_subannual_period = bool(
            period_range
            and period_range.canonical_periods
            and any(
                self._is_subannual_period(period)
                for period in period_range.canonical_periods
            )
        )

        if has_cumulative_yoy or has_sequential_cumulative:
            if has_cumulative_yoy:
                return PeriodComparisonType.YEAR_OVER_YEAR, PeriodBasisType.CUMULATIVE
            return PeriodComparisonType.SEQUENTIAL, PeriodBasisType.CUMULATIVE
        if has_qoq:
            if has_subannual_period:
                return PeriodComparisonType.SEQUENTIAL, PeriodBasisType.SINGLE_PERIOD
            return PeriodComparisonType.SEQUENTIAL, PeriodBasisType.CUMULATIVE
        if has_yoy:
            if has_subannual_period:
                return PeriodComparisonType.YEAR_OVER_YEAR, PeriodBasisType.SINGLE_PERIOD
            return PeriodComparisonType.YEAR_OVER_YEAR, PeriodBasisType.CUMULATIVE
        return PeriodComparisonType.YEAR_OVER_YEAR, PeriodBasisType.CUMULATIVE

    @staticmethod
    def _is_subannual_period(period: str) -> bool:
        try:
            return PeriodNormalizer().parse(period).period_type != PeriodType.FY
        except ValueError:
            return False

    def _extract_period_range(
        self,
        question: str,
    ) -> tuple[PeriodResolution | None, list[str]]:
        explicit_periods = self._extract_explicit_periods(question)
        if explicit_periods is not None:
            return explicit_periods

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

    def _extract_explicit_periods(
        self,
        question: str,
    ) -> tuple[PeriodResolution | None, list[str]]:
        """Parse explicit quarter and half-year tokens such as 2023Q1.

        Explicit sub-annual periods must win over the bare year scan so that
        "2023Q1" never silently degrades into annual fiscal year 2023.
        """
        tokens = re.findall(
            r"(?<!\d)(20\d{2})[- ]?([Qq][1-4]|[Hh][12])(?!\d)",
            question,
        )
        if not tokens:
            return None

        normalizer = PeriodNormalizer()
        canonical_periods: list[str] = []
        years: list[int] = []
        for raw_year, raw_token in tokens:
            year = int(raw_year)
            years.append(year)
            canonical = normalizer.normalize(f"{year}{raw_token.upper()}")
            if canonical not in canonical_periods:
                canonical_periods.append(canonical)
        if all(period[-2] == "Q" for period in canonical_periods):
            period_type = "QUARTERLY"
        elif all(period[-2] == "H" for period in canonical_periods):
            period_type = "HALF_YEARLY"
        else:
            period_type = "MIXED"
        return PeriodResolution(
            start_year=min(years),
            end_year=max(years),
            period_type=period_type,
            canonical_periods=canonical_periods,
        ), []

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
