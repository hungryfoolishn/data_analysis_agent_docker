"""Explicit tool registry for the V10 financial planner."""

from __future__ import annotations

from .models import FinancialPlanTaskType, FinancialToolSpec


def _tool(
    tool_id: str,
    name: str,
    version: str,
    capabilities: list[str],
    supported_task_types: list[FinancialPlanTaskType],
    *,
    requires_verification: bool = False,
) -> FinancialToolSpec:
    return FinancialToolSpec(
        tool_id=tool_id,
        name=name,
        version=version,
        input_schema=f"{tool_id}_input_v1",
        output_schema=f"{tool_id}_output_v1",
        capabilities=capabilities,
        supported_task_types=supported_task_types,
        deterministic=True,
        requires_verification=requires_verification,
    )


_FINANCIAL_TOOL_REGISTRY: dict[str, FinancialToolSpec] = {
    spec.tool_id: spec
    for spec in [
        _tool(
            "company_resolver",
            "Company Resolver",
            "v10.1.0",
            ["company_resolution"],
            [FinancialPlanTaskType.RESOLVE_COMPANIES],
        ),
        _tool(
            "period_resolver",
            "Period Resolver",
            "v10.1.0",
            ["period_resolution"],
            [FinancialPlanTaskType.RESOLVE_PERIODS],
        ),
        _tool(
            "data_coverage_checker",
            "Data Coverage Checker",
            "v10.1.0",
            ["data_quality", "coverage_check"],
            [FinancialPlanTaskType.VALIDATE_DATA_COVERAGE],
        ),
        _tool(
            "financial_metric_engine",
            "Financial Metric Engine",
            "v9.3.0",
            ["metric_calculation", "trend_analysis"],
            [
                FinancialPlanTaskType.REVENUE_TREND,
                FinancialPlanTaskType.PROFIT_TREND,
                FinancialPlanTaskType.PROFITABILITY_ANALYSIS,
                FinancialPlanTaskType.CASHFLOW_ANALYSIS,
                FinancialPlanTaskType.SOLVENCY_ANALYSIS,
                FinancialPlanTaskType.OPERATING_ANALYSIS,
            ],
            requires_verification=True,
        ),
        _tool(
            "risk_detector",
            "Financial Risk Detector",
            "v9.0.0",
            ["rule_based_risk_detection"],
            [FinancialPlanTaskType.RISK_DETECTION],
            requires_verification=True,
        ),
        _tool(
            "comparison_engine",
            "Financial Comparison Engine",
            "v9.0.0",
            ["peer_comparison"],
            [FinancialPlanTaskType.PEER_COMPARISON],
            requires_verification=True,
        ),
        _tool(
            "verification_engine",
            "Financial Verification Engine",
            "v9.3.0",
            ["independent_verification"],
            [FinancialPlanTaskType.VERIFY_CALCULATIONS],
        ),
        _tool(
            "finding_engine",
            "Financial Finding Engine",
            "v9.0.0",
            ["evidence_backed_finding"],
            [FinancialPlanTaskType.BUILD_FINDINGS],
        ),
        _tool(
            "report_builder",
            "Financial Report Builder",
            "v9.0.0",
            ["report_generation"],
            [FinancialPlanTaskType.GENERATE_REPORT],
        ),
    ]
}

TASK_TOOL_IDS: dict[FinancialPlanTaskType, str] = {
    FinancialPlanTaskType.RESOLVE_COMPANIES: "company_resolver",
    FinancialPlanTaskType.RESOLVE_PERIODS: "period_resolver",
    FinancialPlanTaskType.VALIDATE_DATA_COVERAGE: "data_coverage_checker",
    FinancialPlanTaskType.REVENUE_TREND: "financial_metric_engine",
    FinancialPlanTaskType.PROFIT_TREND: "financial_metric_engine",
    FinancialPlanTaskType.PROFITABILITY_ANALYSIS: "financial_metric_engine",
    FinancialPlanTaskType.CASHFLOW_ANALYSIS: "financial_metric_engine",
    FinancialPlanTaskType.SOLVENCY_ANALYSIS: "financial_metric_engine",
    FinancialPlanTaskType.OPERATING_ANALYSIS: "financial_metric_engine",
    FinancialPlanTaskType.RISK_DETECTION: "risk_detector",
    FinancialPlanTaskType.PEER_COMPARISON: "comparison_engine",
    FinancialPlanTaskType.VERIFY_CALCULATIONS: "verification_engine",
    FinancialPlanTaskType.BUILD_FINDINGS: "finding_engine",
    FinancialPlanTaskType.GENERATE_REPORT: "report_builder",
}

FINANCIAL_TOOL_REGISTRY = _FINANCIAL_TOOL_REGISTRY
