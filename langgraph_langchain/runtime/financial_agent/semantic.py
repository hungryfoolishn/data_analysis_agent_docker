"""Semantic resolution for Runtime V10.1."""

from __future__ import annotations

import re
from dataclasses import dataclass

from langgraph_langchain.runtime.financial.metrics import (
    FinancialMetricDefinition,
    financial_metric_registry,
)
from langgraph_langchain.runtime.financial.models import Company
from langgraph_langchain.runtime.financial.period import PeriodNormalizer

from .models import CompanyResolution, FinancialTaskUnderstanding
from .semantic_models import (
    ClarificationRequest,
    CompanyDataCoverage,
    CompanyMatch,
    CompanyMatchType,
    CompanyResolutionResult,
    DataCoverageGap,
    DataCoverageResult,
    DataRequirement,
    MetricCoverage,
    MetricDefinitionResolution,
    MetricResolutionResult,
    PeriodCoverage,
    SemanticResolution,
)


DEFAULT_COMPANY_ALIASES: dict[str, tuple[str, ...]] = {
    "贵州茅台": ("Guizhou Moutai", "Kweichow Moutai", "Moutai"),
    "五粮液": ("Wuliangye",),
    "泸州老窖": ("Luzhou Laojiao", "Laojiao"),
}

_SOURCE_FIELD_ALIASES = {
    "balance_statement": "balance_sheet",
    "cash_statement": "cash_flow",
}


def _canonical_source(source: str) -> str:
    statement_type, field_name = source.split(".", 1)
    statement_type = _SOURCE_FIELD_ALIASES.get(statement_type, statement_type)
    return f"{statement_type}.{field_name}"


@dataclass(frozen=True)
class _CompanyEntry:
    company: Company
    terms: tuple[str, ...]


class CompanyRegistry:
    """Unified company identifier and alias registry."""

    def __init__(
        self,
        companies: list[Company],
        *,
        aliases: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        selected_aliases = DEFAULT_COMPANY_ALIASES if aliases is None else aliases
        self._entries: list[_CompanyEntry] = []
        for company in companies:
            terms = [company.company_name]
            terms.extend(selected_aliases.get(company.company_name, ()))
            terms.extend(
                term
                for term in (company.company_id, company.stock_code)
                if term
            )
            deduped: list[str] = []
            for term in terms:
                if term and term not in deduped:
                    deduped.append(term)
            self._entries.append(_CompanyEntry(
                company=company,
                terms=tuple(deduped),
            ))

    def resolve(self, text: str, requested_terms: list[str] | None = None):
        haystack = text.casefold()
        matches: dict[str, CompanyMatch] = {}
        for entry in self._entries:
            for term in entry.terms:
                match_type = self._match_type(term, entry.company, haystack)
                if match_type is None:
                    continue
                match = CompanyMatch(
                    company_id=entry.company.company_id,
                    company_name=entry.company.company_name,
                    stock_code=entry.company.stock_code,
                    matched_term=term,
                    match_type=match_type,
                )
                # Prefer the most explicit identifier when multiple aliases
                # for the same company match.
                previous = matches.get(entry.company.company_id)
                if previous is None or match_type.value == "STOCK_CODE":
                    matches[entry.company.company_id] = match
                break

        requested = requested_terms or []
        resolved_identifiers: set[str] = set()
        for match in matches.values():
            resolved_identifiers.update({
                match.company_id,
                match.company_name,
                match.stock_code,
                match.matched_term,
            })
        unresolved_terms = [
            term
            for term in requested
            if term and term not in resolved_identifiers
        ]
        resolved = [
            CompanyResolution(
                company_name=match.company_name,
                company_id=match.company_id,
                stock_code=match.stock_code,
            )
            for match in matches.values()
        ]
        return CompanyResolutionResult(
            resolved=resolved,
            unresolved_terms=unresolved_terms,
            matches=list(matches.values()),
            diagnostics=[
                f"SEMANTIC_UNRESOLVED_COMPANY:{term}"
                for term in unresolved_terms
            ],
        )

    @staticmethod
    def _match_type(
        term: str,
        company: Company,
        haystack: str,
    ) -> CompanyMatchType | None:
        normalized_term = term.casefold()
        if re.fullmatch(r"\d{6}", normalized_term):
            if re.search(
                rf"(?<!\d){re.escape(normalized_term)}(?!\d)",
                haystack,
            ):
                return CompanyMatchType.STOCK_CODE
            return None
        if normalized_term not in haystack:
            return None
        if normalized_term == company.company_id.casefold():
            return CompanyMatchType.COMPANY_ID
        if normalized_term == company.company_name.casefold():
            return CompanyMatchType.EXACT_NAME
        return CompanyMatchType.ALIAS


class FinancialSemanticResolver:
    """Resolve companies, periods, metrics, requirements, and coverage."""

    def __init__(
        self,
        data_service,
        *,
        company_aliases: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.data_service = data_service
        self.period_normalizer = PeriodNormalizer()
        self.company_registry = CompanyRegistry(
            data_service.list_companies(),
            aliases=company_aliases,
        )

    def resolve(
        self,
        understanding: FinancialTaskUnderstanding,
    ) -> SemanticResolution:
        companies = self.company_registry.resolve(
            understanding.raw_question,
            [item.company_id for item in understanding.companies],
        )
        periods = self._resolve_periods(understanding, companies.resolved)
        metrics = self._resolve_metrics(understanding.required_metrics)
        coverage = self._resolve_coverage(
            companies.resolved,
            periods,
            metrics.resolved,
        )

        diagnostics: list[str] = []
        diagnostics.extend(companies.diagnostics)
        if not companies.resolved:
            diagnostics.append("SEMANTIC_NO_COMPANIES")
        if not periods:
            diagnostics.append("SEMANTIC_NO_PERIOD_COVERAGE")
        diagnostics.extend(metrics.diagnostics)
        if metrics.resolved:
            diagnostics.extend(coverage.diagnostics)
        else:
            diagnostics.append("SEMANTIC_NO_METRICS")

        blockers: list[str] = []
        if companies.unresolved_terms or not companies.resolved:
            blockers.append("SEMANTIC_COMPANY_NOT_RESOLVED")
        if metrics.unresolved_metrics or not metrics.resolved:
            blockers.append("SEMANTIC_METRIC_NOT_RESOLVED")
        if not periods or all(not item.ready_for_execution for item in periods):
            blockers.append("SEMANTIC_PERIOD_NOT_AVAILABLE")
        if metrics.resolved and coverage.companies:
            has_usable_metrics = any(
                metric.status == "AVAILABLE"
                for company in coverage.companies
                for metric in company.metric_coverage
            )
            if not has_usable_metrics:
                blockers.append("SEMANTIC_DATA_NOT_AVAILABLE")

        clarifications = self._clarifications(
            companies.unresolved_terms,
            periods,
            metrics,
            coverage,
            data_not_available="SEMANTIC_DATA_NOT_AVAILABLE" in blockers,
        )

        return SemanticResolution(
            companies=companies,
            periods=periods,
            metrics=metrics,
            data_coverage=coverage,
            diagnostics=list(dict.fromkeys(diagnostics)),
            blockers=list(dict.fromkeys(blockers)),
            metadata={
                "resolver_version": "v10.1.0",
                "ready_for_planning": not blockers,
                "resolved_company_count": len(companies.resolved),
                "resolved_metric_count": len(metrics.resolved),
            },
            clarifications=clarifications,
        )

    def _resolve_periods(
        self,
        understanding: FinancialTaskUnderstanding,
        companies: list[CompanyResolution],
    ) -> list[PeriodCoverage]:
        if understanding.period_range is None:
            return []
        start_year = understanding.period_range.start_year
        end_year = understanding.period_range.end_year
        requested_years = list(range(start_year, end_year + 1))

        coverage: list[PeriodCoverage] = []
        for company in companies:
            available_annual = [
                period
                for period in self.data_service.periods_for(company.company_id)
                if self.period_normalizer.parse(period).period_type.value == "FY"
            ]
            available_years = {int(period) for period in available_annual}
            selected = [
                str(year) for year in requested_years if year in available_years
            ]
            missing = [
                str(year)
                for year in requested_years
                if year not in available_years
            ]
            if not available_annual:
                status = "NO_AVAILABLE_PERIODS"
            elif not selected:
                status = "NO_MATCHING_PERIODS"
            elif missing:
                status = "PARTIAL"
            else:
                status = "COMPLETE"
            coverage.append(PeriodCoverage(
                company_id=company.company_id,
                company_name=company.company_name,
                requested_start_year=start_year,
                requested_end_year=end_year,
                available_periods=available_annual,
                selected_periods=selected,
                missing_periods=missing,
                status=status,
            ))
        return coverage

    def _resolve_metrics(
        self,
        metric_ids: list[str],
    ) -> MetricResolutionResult:
        resolved: list[MetricDefinitionResolution] = []
        unresolved: list[str] = []
        diagnostics: list[str] = []
        for metric_id in metric_ids:
            definition = financial_metric_registry.get_optional(metric_id)
            if definition is None:
                unresolved.append(metric_id)
                diagnostics.append(f"SEMANTIC_UNKNOWN_METRIC:{metric_id}")
                continue
            resolved.append(self._metric_definition(definition))
        if not metric_ids:
            diagnostics.append("SEMANTIC_NO_REQUESTED_METRICS")
        return MetricResolutionResult(
            resolved=resolved,
            unresolved_metrics=unresolved,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _metric_definition(
        definition: FinancialMetricDefinition,
    ) -> MetricDefinitionResolution:
        return MetricDefinitionResolution(
            metric_id=definition.metric_id,
            name=definition.name,
            category=definition.category,
            formula=definition.formula,
            unit=definition.unit,
            calculation_type=definition.calculation_type,
            balance_policy=definition.balance_policy,
            source_fields=[
                _canonical_source(source)
                for source in definition.source_fields
            ],
        )

    def _resolve_coverage(
        self,
        companies: list[CompanyResolution],
        period_coverage: list[PeriodCoverage],
        metrics: list[MetricDefinitionResolution],
    ) -> DataCoverageResult:
        if not companies or not period_coverage or not metrics:
            return DataCoverageResult(
                diagnostics=["SEMANTIC_DATA_COVERAGE_INCOMPLETE_INPUT"],
            )

        requirements: dict[str, list[DataRequirement]] = {
            item.metric_id: self._metric_requirements(item)
            for item in metrics
        }
        company_coverage: list[CompanyDataCoverage] = []
        blockers: list[DataCoverageGap] = []
        warnings: list[DataCoverageGap] = []
        diagnostics: list[str] = []

        for company in companies:
            period_item = next(
                item for item in period_coverage
                if item.company_id == company.company_id
            )
            item = self._coverage_for_company(
                company,
                period_item,
                metrics,
                requirements,
            )
            company_coverage.append(item)
            blockers.extend(item.blockers)
            warnings.extend(item.warnings)

        for gap in blockers:
            diagnostics.append(
                "SEMANTIC_DATA_BLOCKER:"
                f"{gap.company_id}/{gap.period}/{gap.metric_id}/{gap.scope}"
            )
        for gap in warnings:
            diagnostics.append(
                "SEMANTIC_DATA_WARNING:"
                f"{gap.company_id}/{gap.period}/{gap.metric_id}/{gap.scope}"
            )

        return DataCoverageResult(
            companies=company_coverage,
            blockers=blockers,
            warnings=warnings,
            diagnostics=list(dict.fromkeys(diagnostics)),
        )

    def _coverage_for_company(
        self,
        company: CompanyResolution,
        period_item: PeriodCoverage,
        metrics: list[MetricDefinitionResolution],
        requirements: dict[str, list[DataRequirement]],
    ) -> CompanyDataCoverage:
        metric_coverage: list[MetricCoverage] = []
        blockers: list[DataCoverageGap] = []
        warnings: list[DataCoverageGap] = []

        for period in period_item.selected_periods:
            try:
                previous_period = self.data_service.previous_period(
                    company.company_id,
                    period,
                )
                income, balance, cash_flow, previous_income = (
                    self.data_service.statements(
                        company.company_id,
                        period,
                        previous_period,
                    )
                )
                previous_balance = self.data_service.previous_balance(
                    company.company_id,
                    period,
                )
            except KeyError:
                for metric in metrics:
                    gap = self._gap(
                        company,
                        period,
                        metric.metric_id,
                        "CURRENT",
                        "",
                        "",
                        "period_not_available",
                    )
                    warnings.append(gap)
                    metric_coverage.append(MetricCoverage(
                        company_id=company.company_id,
                        company_name=company.company_name,
                        period=period,
                        metric_id=metric.metric_id,
                        status="PERIOD_MISSING",
                    ))
                continue

            statements = {
                "income_statement": income,
                "balance_sheet": balance,
                "cash_flow": cash_flow,
            }
            previous_statements = {
                "income_statement": previous_income,
                "balance_sheet": previous_balance,
            }

            for metric in metrics:
                missing_current: list[str] = []
                missing_previous: list[str] = []
                for requirement in requirements.get(metric.metric_id, []):
                    if requirement.scope == "CURRENT":
                        statement = statements.get(requirement.statement_type)
                        value = (
                            getattr(statement, requirement.field_name, None)
                            if statement
                            else None
                        )
                        if value is None:
                            missing_current.append(requirement.source)
                    else:
                        statement = previous_statements.get(
                            requirement.statement_type
                        )
                        value = (
                            getattr(statement, requirement.field_name, None)
                            if statement
                            else None
                        )
                        if (
                            previous_period is None
                            or statement is None
                            or value is None
                        ):
                            missing_previous.append(requirement.source)

                if missing_current:
                    status = "MISSING_CURRENT_DATA"
                    for source in missing_current:
                        statement_type, field_name = source.split(".", 1)
                        warnings.append(self._gap(
                            company,
                            period,
                            metric.metric_id,
                            "CURRENT",
                            statement_type,
                            field_name,
                            "field_missing",
                        ))
                elif missing_previous:
                    status = "MISSING_PREVIOUS_DATA"
                    for source in missing_previous:
                        statement_type, field_name = source.split(".", 1)
                        warnings.append(self._gap(
                            company,
                            period,
                            metric.metric_id,
                            "PREVIOUS",
                            statement_type,
                            field_name,
                            "previous_field_missing",
                        ))
                else:
                    status = "AVAILABLE"

                metric_coverage.append(MetricCoverage(
                    company_id=company.company_id,
                    company_name=company.company_name,
                    period=period,
                    metric_id=metric.metric_id,
                    status=status,
                    missing_fields=[*missing_current, *missing_previous],
                    previous_period=previous_period,
                ))

        return CompanyDataCoverage(
            company_id=company.company_id,
            company_name=company.company_name,
            available_periods=period_item.available_periods,
            selected_periods=period_item.selected_periods,
            missing_periods=period_item.missing_periods,
            metric_coverage=metric_coverage,
            blockers=blockers,
            warnings=warnings,
        )

    def _metric_requirements(
        self,
        metric: MetricDefinitionResolution,
    ) -> list[DataRequirement]:
        requirements: list[DataRequirement] = []
        for source in metric.source_fields:
            statement_type, field_name = source.split(".", 1)
            requirements.append(DataRequirement(
                metric_id=metric.metric_id,
                scope="CURRENT",
                statement_type=statement_type,
                field_name=field_name,
                source=source,
            ))

        needs_previous = (
            metric.calculation_type == "growth"
            or metric.balance_policy == "average_balance"
        )
        if needs_previous:
            if metric.calculation_type == "growth":
                previous_sources = metric.source_fields
            else:
                previous_sources = [
                    source
                    for source in metric.source_fields
                    if source.startswith("balance_sheet.")
                ]
            for source in previous_sources:
                statement_type, field_name = source.split(".", 1)
                requirements.append(DataRequirement(
                    metric_id=metric.metric_id,
                    scope="PREVIOUS",
                    statement_type=statement_type,
                    field_name=field_name,
                    source=source,
                ))
        return requirements

    @staticmethod
    def _gap(
        company: CompanyResolution,
        period: str,
        metric_id: str,
        scope: str,
        statement_type: str,
        field_name: str,
        reason: str,
    ) -> DataCoverageGap:
        return DataCoverageGap(
            company_id=company.company_id,
            company_name=company.company_name,
            period=period,
            metric_id=metric_id,
            scope=scope,
            statement_type=statement_type,
            field_name=field_name,
            reason=reason,
        )

    @staticmethod
    def _clarifications(
        unresolved_terms: list[str],
        periods: list[PeriodCoverage],
        metrics: MetricResolutionResult,
        coverage: DataCoverageResult,
        *,
        data_not_available: bool = False,
    ) -> list[ClarificationRequest]:
        requests: list[ClarificationRequest] = []
        for term in unresolved_terms:
            requests.append(ClarificationRequest(
                diagnostic=f"SEMANTIC_UNRESOLVED_COMPANY:{term}",
                message=f"无法识别公司“{term}”，请使用数据集中的公司名称或证券代码。",
            ))
        if not periods:
            requests.append(ClarificationRequest(
                diagnostic="SEMANTIC_NO_PERIOD_COVERAGE",
                message="无法确定分析期间，请明确年度范围。",
            ))
        for metric_id in metrics.unresolved_metrics:
            requests.append(ClarificationRequest(
                diagnostic=f"SEMANTIC_UNKNOWN_METRIC:{metric_id}",
                message=f"指标 {metric_id} 不在当前指标注册表中。",
            ))
        if not metrics.resolved:
            requests.append(ClarificationRequest(
                diagnostic="SEMANTIC_NO_METRICS",
                message="未能解析出任何可分析指标。",
            ))
        for gap in coverage.blockers:
            requests.append(ClarificationRequest(
                diagnostic=(
                    f"SEMANTIC_DATA_BLOCKER:{gap.company_id}/"
                    f"{gap.period}/{gap.metric_id}"
                ),
                message=(
                    f"{gap.company_name} {gap.period} {gap.metric_id} "
                    f"缺少 {gap.scope} 数据 "
                    f"{gap.statement_type}.{gap.field_name}。"
                ),
                company_id=gap.company_id,
                field=f"{gap.statement_type}.{gap.field_name}",
            ))
        if data_not_available:
            requests.append(ClarificationRequest(
                diagnostic="SEMANTIC_DATA_NOT_AVAILABLE",
                message="请求数据不足以计算任何已解析指标，请调整公司、期间或指标范围。",
            ))
        return requests
