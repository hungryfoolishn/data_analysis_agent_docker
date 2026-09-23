"""Deterministic financial analysis workflows for Runtime V9."""

from __future__ import annotations

from collections import defaultdict

from .anomaly_engine import FinancialAnomalyEngine
from .classifier import FinancialTaskClassifier, FinancialTaskType
from .data_service import FinancialDataService
from .finding_engine import FinancialFindingEngine
from .metrics import compute_metric, financial_metric_registry
from .models import (
    BalanceSheetStatement,
    CashFlowStatement,
    FinancialAnalysisResult,
    FinancialCalculation,
    FinancialComparison,
    FinancialComparisonEntity,
    FinancialEvidence,
    FinancialFinding,
    FinancialObservation,
    FinancialQuery,
    FinancialRiskSignal,
    FinancialVerification,
    IncomeStatement,
)
from .verification_engine import FinancialVerificationEngine


_LOWER_IS_BETTER = {
    "cost_of_revenue",
    "total_liabilities",
    "short_term_debt",
    "long_term_debt",
    "current_liabilities",
    "debt_to_asset",
    "capital_expenditure",
}


class FinancialAnalysisWorkflow:
    """Route financial questions through verifiable metric workflows."""

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
    _COMPARISON_ENABLED_TASKS = {
        FinancialTaskType.PEER_COMPARISON,
        FinancialTaskType.COMPREHENSIVE_ANALYSIS,
    }

    def __init__(self, data_service: FinancialDataService) -> None:
        self.data_service = data_service
        self.classifier = FinancialTaskClassifier()
        self.risk_detector = __import__(
            "langgraph_langchain.runtime.financial.risk_detector",
            fromlist=["FinancialRiskDetector"],
        ).FinancialRiskDetector()
        self.verification_engine = FinancialVerificationEngine()
        self.finding_engine = FinancialFindingEngine()
        self.anomaly_engine = FinancialAnomalyEngine()

    def run(self, query: FinancialQuery | str) -> FinancialAnalysisResult:
        query = self._normalize_query(query)
        task_type = query.task_type or self.classifier.classify(query.question)
        metrics = query.metrics or self._TASK_METRICS.get(task_type, ["revenue"])
        observations: list[FinancialObservation] = []
        calculations: list[FinancialCalculation] = []
        verifications: list[FinancialVerification] = []
        evidence: list[FinancialEvidence] = []
        source_ids: list[str] = []

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
                previous_balance = self.data_service.previous_balance(
                    company.company_id, period
                )
                income, balance, cash_flow, previous_income = self.data_service.statements(
                    company.company_id,
                    period,
                    previous_period,
                )
                for metric_id in metrics:
                    definition = financial_metric_registry.get(metric_id)
                    computation = compute_metric(
                        metric_id,
                        income=income,
                        balance=balance,
                        cash_flow=cash_flow,
                        previous_income=previous_income,
                        previous_balance=previous_balance,
                    )
                    metric_source_ids = self._source_ids_for_metric(
                        definition,
                        income=income,
                        balance=balance,
                        cash_flow=cash_flow,
                        previous_income=previous_income,
                        previous_balance=previous_balance,
                    )
                    source_fields = list(definition.source_fields)
                    calculation = FinancialCalculation(
                        metric_id=metric_id,
                        company_id=company.company_id,
                        company_name=company.company_name,
                        period=period,
                        formula=definition.formula,
                        inputs=computation.inputs,
                        result=computation.value,
                        unit=definition.unit,
                        status=computation.status,
                        status_reason=computation.reason,
                        missing_fields=computation.missing_fields,
                        source_ids=metric_source_ids,
                        source_fields=source_fields,
                    )
                    verification = self.verification_engine.verify(
                        calculation=calculation,
                        metric_id=metric_id,
                        income=income,
                        balance=balance,
                        cash_flow=cash_flow,
                        previous_income=previous_income,
                        previous_balance=previous_balance,
                    )
                    calculations.append(calculation)
                    verifications.append(verification)

                    if not verification.passed or computation.value is None:
                        continue
                    evidence_item = FinancialEvidence(
                        metric_id=metric_id,
                        metric_name=definition.name,
                        company_id=company.company_id,
                        company_name=company.company_name,
                        stock_code=company.stock_code,
                        period=period,
                        value=computation.value,
                        unit=definition.unit,
                        formula=definition.formula,
                        fact=(
                            f"{company.company_name} {period} "
                            f"{definition.name}为{computation.value:,.4f}{definition.unit}"
                        ),
                        source_ids=metric_source_ids,
                        source_fields=source_fields,
                        calculation_id=calculation.calculation_id,
                        verification_result_id=verification.verification_id,
                        verification_status="verified",
                    )
                    observation = FinancialObservation(
                        company_id=company.company_id,
                        company_name=company.company_name,
                        stock_code=company.stock_code,
                        period=period,
                        metric_id=metric_id,
                        metric_name=definition.name,
                        value=computation.value,
                        unit=definition.unit,
                        formula=definition.formula,
                        fact=evidence_item.fact,
                        calculation=calculation,
                        source_ids=metric_source_ids,
                        source_fields=source_fields,
                        source_period=period,
                        balance_policy=definition.balance_policy,
                        status="calculated",
                    )
                    evidence.append(evidence_item)
                    observations.append(observation)
                    source_ids.extend(metric_source_ids)

        risk_signals: list[FinancialRiskSignal] = self.risk_detector.detect(observations)
        comparisons: list[FinancialComparison] = (
            self._build_comparisons(observations)
            if task_type in self._COMPARISON_ENABLED_TASKS
            else []
        )
        anomalies = self.anomaly_engine.detect(observations)
        findings: list[FinancialFinding] = self.finding_engine.build(
            task_type=task_type,
            observations=observations,
            evidence=evidence,
            comparisons=comparisons,
            risks=risk_signals,
            anomalies=anomalies,
        )
        data_sources = self.data_service.get_sources(
            list(dict.fromkeys(source_ids))
        )
        result = FinancialAnalysisResult(
            query=query,
            task_type=task_type,
            metrics=metrics,
            observations=observations,
            calculations=calculations,
            verifications=verifications,
            evidence=evidence,
            comparisons=comparisons,
            findings=findings,
            risk_signals=risk_signals,
            anomalies=anomalies,
            data_sources=data_sources,
            summary=self._summary(
                observations=observations,
                calculations=calculations,
                comparisons=comparisons,
                risks=risk_signals,
                findings=findings,
                anomalies=anomalies,
            ),
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

    @staticmethod
    def _source_ids_for_metric(
        definition,
        *,
        income: IncomeStatement,
        balance: BalanceSheetStatement,
        cash_flow: CashFlowStatement,
        previous_income: IncomeStatement | None,
        previous_balance: BalanceSheetStatement | None,
    ) -> list[str]:
        current_sources = {
            "income_statement": income,
            "balance_sheet": balance,
            "cash_flow": cash_flow,
        }
        source_ids: list[str] = []
        for source_field in definition.source_fields:
            prefix = source_field.split(".", 1)[0]
            statement = current_sources.get(prefix)
            source_id = getattr(statement, "source_id", None)
            if source_id:
                source_ids.append(source_id)

        if definition.calculation_type == "growth" and previous_income is not None:
            if previous_income.source_id:
                source_ids.append(previous_income.source_id)
        if definition.balance_policy == "average_balance" and previous_balance is not None:
            if previous_balance.source_id:
                source_ids.append(previous_balance.source_id)

        return list(dict.fromkeys(source_ids))

    @staticmethod
    def _build_comparisons(
        observations: list[FinancialObservation],
    ) -> list[FinancialComparison]:
        grouped: dict[tuple[str, str], list[FinancialObservation]] = defaultdict(list)
        for observation in observations:
            grouped[(observation.period, observation.metric_id)].append(observation)

        comparisons: list[FinancialComparison] = []
        for (period, metric_id), items in sorted(grouped.items()):
            companies = {item.company_name for item in items}
            if len(items) < 2 or len(companies) != len(items):
                continue
            definition = financial_metric_registry.get_optional(metric_id)
            metric_name = definition.name if definition else metric_id
            unit = definition.unit if definition else "x"
            higher_is_better = metric_id not in _LOWER_IS_BETTER
            ordered = sorted(
                items,
                key=lambda item: item.value,
                reverse=higher_is_better,
            )
            entities = [
                FinancialComparisonEntity(
                    company_name=item.company_name,
                    value=item.value,
                    rank=rank,
                )
                for rank, item in enumerate(ordered, start=1)
            ]
            leader, laggard = ordered[0], ordered[-1]
            difference = leader.value - laggard.value
            relative_difference = (
                difference / abs(laggard.value) if laggard.value else 0.0
            )
            relation = "高于" if higher_is_better else "低于"
            statement = (
                f"{period} {metric_name}比较：{leader.company_name} "
                f"{relation} {laggard.company_name}，差异为 "
                f"{difference:,.4f}{unit}，相对差异为 {relative_difference:.2%}。"
            )
            comparisons.append(FinancialComparison(
                metric_id=metric_id,
                metric_name=metric_name,
                period=period,
                unit=unit,
                higher_is_better=higher_is_better,
                entities=entities,
                leader_name=leader.company_name,
                leader_value=leader.value,
                laggard_name=laggard.company_name,
                laggard_value=laggard.value,
                difference=round(difference, 6),
                relative_difference=round(relative_difference, 6),
                statement=statement,
            ))
        return comparisons

    @staticmethod
    def _summary(
        *,
        observations: list[FinancialObservation],
        calculations: list[FinancialCalculation],
        comparisons: list[FinancialComparison],
        risks: list[FinancialRiskSignal],
        findings: list[FinancialFinding],
        anomalies: list,
    ) -> str:
        unavailable = sum(item.status != "calculated" for item in calculations)
        if not observations:
            return "未能生成可验证的金融分析结果：所需报表数据缺失或无效。"

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
        summary += (
            f" 生成 {len(findings)} 个 Evidence-backed Finding、"
            f" {len(comparisons)} 个 Comparison、"
            f" {len(anomalies)} 个异常信号、"
            f" {len(risks)} 个规则型风险信号。"
        )
        if unavailable:
            summary += f" {unavailable} 个指标因数据缺失或无效标记为不可计算。"
        return summary

    @staticmethod
    def _report(result: FinancialAnalysisResult) -> str:
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

        unavailable = [
            item for item in result.calculations if item.status != "calculated"
        ]
        if unavailable:
            lines.extend(["", "## 数据可用性"])
            for item in unavailable:
                missing = ", ".join(item.missing_fields) or "未提供"
                lines.append(
                    f"- {item.company_name} / {item.period} / {item.metric_id}: "
                    f"{item.status}（{item.status_reason}）；missing={missing}"
                )

        if result.comparisons:
            lines.extend(["", "## 同业比较"])
            for item in result.comparisons:
                lines.append(f"- {item.statement}")

        lines.extend(["", "## 计算过程"])
        for item in result.calculations[:50]:
            inputs = ", ".join(
                f"{key}={'NULL' if value is None else f'{value:,.4f}'}"
                for key, value in item.inputs.items()
            )
            lines.append(
                f"- {item.company_name} / {item.period} / {item.metric_id}: "
                f"{item.formula}; {inputs}; "
                f"result={'NULL' if item.result is None else f'{item.result:,.4f}'}{item.unit}; "
                f"status={item.status}"
            )

        if result.findings:
            lines.extend(["", "## 核心发现"])
            for item in result.findings[:50]:
                evidence_ids = ", ".join(item.evidence_ids)
                lines.append(f"- {item.statement} Evidence: {evidence_ids}")

        if result.risk_signals:
            lines.extend(["", "## 风险信号"])
            for item in result.risk_signals:
                lines.append(
                    f"- {item.company_name} / {item.period}: {item.message} "
                    f"({item.display_value})"
                )

        if result.data_sources:
            lines.extend(["", "## 证据"])
            sources_by_id = {item.source_id: item for item in result.data_sources}
            for item in result.evidence[:50]:
                source_details = []
                for source_id in item.source_ids:
                    source = sources_by_id.get(source_id)
                    if source:
                        source_details.append(
                            f"{source.source_id}({source.document_name}, {source.source_hash})"
                        )
                lines.append(
                    f"- {item.company_name} / {item.period} / {item.metric_name}: "
                    f"{item.fact}; sources={'; '.join(source_details) or 'NULL'}; "
                    f"verification={item.verification_status}"
                )

        lines.extend([
            "",
            "## 说明",
            "- 以上内容区分 Fact、Calculation、Verification、Evidence、Finding 和 Interpretation。",
            "- 缺失数据标记为 UNAVAILABLE，分母为零或非有限值标记为 INVALID，不会用 0 代替。",
            "- 所有数值均来自标准化金融数据，计算过程和 source_id 可追溯。",
            "- 本结果不构成投资建议。",
        ])
        return "\n".join(lines)
