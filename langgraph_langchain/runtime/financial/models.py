"""Stable models for Runtime V9 financial analysis."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from langgraph_langchain.runtime.models import utc_now


def _financial_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class Company(BaseModel):
    company_id: str = Field(default_factory=lambda: _financial_id("company"))
    stock_code: str
    company_name: str
    exchange: str
    industry_id: str
    listing_date: Optional[str] = None
    currency: str = "CNY"


class IncomeStatement(BaseModel):
    company_id: str
    period: str
    currency: str = "CNY"
    revenue: Optional[float] = None
    cost_of_revenue: Optional[float] = None
    gross_profit: Optional[float] = None
    operating_profit: Optional[float] = None
    net_profit: Optional[float] = None
    net_profit_attributable: Optional[float] = None
    eps: Optional[float] = None
    source_id: Optional[str] = None


class BalanceSheetStatement(BaseModel):
    company_id: str
    period: str
    currency: str = "CNY"
    total_assets: Optional[float] = None
    total_liabilities: Optional[float] = None
    total_equity: Optional[float] = None
    cash: Optional[float] = None
    accounts_receivable: Optional[float] = None
    inventory: Optional[float] = None
    fixed_assets: Optional[float] = None
    short_term_debt: Optional[float] = None
    long_term_debt: Optional[float] = None
    current_assets: Optional[float] = None
    current_liabilities: Optional[float] = None
    source_id: Optional[str] = None


class CashFlowStatement(BaseModel):
    company_id: str
    period: str
    currency: str = "CNY"
    operating_cash_flow: Optional[float] = None
    investing_cash_flow: Optional[float] = None
    financing_cash_flow: Optional[float] = None
    capital_expenditure: Optional[float] = None
    free_cash_flow: Optional[float] = None
    source_id: Optional[str] = None


class FinancialIndicator(BaseModel):
    company_id: str
    period: str
    metric_id: str
    value: Optional[float] = None
    unit: str
    formula: str
    source_id: Optional[str] = None


class FinancialDataSource(BaseModel):
    source_id: str = Field(default_factory=lambda: _financial_id("source"))
    source_type: str = "annual_report"
    company_id: str
    report_period: str
    published_at: Optional[datetime] = None
    document_name: str
    document_url: Optional[str] = None
    source_hash: Optional[str] = None


class FinancialCalculation(BaseModel):
    calculation_id: str = Field(default_factory=lambda: _financial_id("calc"))
    metric_id: str
    company_id: str = ""
    company_name: str = ""
    period: str = ""
    formula: str
    inputs: dict[str, Optional[float]] = Field(default_factory=dict)
    result: Optional[float] = None
    unit: str
    status: str = "calculated"
    status_reason: Optional[str] = None
    missing_fields: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    source_fields: list[str] = Field(default_factory=list)


class FinancialQuery(BaseModel):
    question: str
    task_type: Optional[str] = None
    company_names: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    start_year: Optional[int] = None
    end_year: Optional[int] = None


class FinancialObservation(BaseModel):
    company_id: str
    company_name: str
    stock_code: str
    period: str
    metric_id: str
    metric_name: str
    value: float
    unit: str
    formula: str
    fact: str
    calculation: FinancialCalculation
    source_ids: list[str] = Field(default_factory=list)
    source_fields: list[str] = Field(default_factory=list)
    source_period: str
    balance_policy: str = "ending_balance"
    status: str = "calculated"
    status_reason: Optional[str] = None
    missing_fields: list[str] = Field(default_factory=list)


class FinancialVerification(BaseModel):
    verification_id: str = Field(default_factory=lambda: _financial_id("verify"))
    metric_id: str
    company_id: str
    company_name: str
    period: str
    status: str = "passed"
    passed: bool = True
    method: str = "independent_formula_verification"
    expected_value: Optional[float] = None
    actual_value: Optional[float] = None
    message: str = ""
    calculation_id: str
    missing_fields: list[str] = Field(default_factory=list)


class FinancialEvidence(BaseModel):
    evidence_id: str = Field(default_factory=lambda: _financial_id("fevidence"))
    metric_id: str
    metric_name: str
    company_id: str
    company_name: str
    stock_code: str
    period: str
    value: float
    unit: str
    formula: str
    fact: str
    source_ids: list[str] = Field(default_factory=list)
    source_fields: list[str] = Field(default_factory=list)
    calculation_id: str
    verification_result_id: str
    verification_status: str = "verified"


class FinancialComparisonEntity(BaseModel):
    company_name: str
    value: float
    rank: int


class FinancialComparison(BaseModel):
    comparison_id: str = Field(default_factory=lambda: _financial_id("comparison"))
    metric_id: str
    metric_name: str
    period: str
    unit: str
    higher_is_better: bool = True
    entities: list[FinancialComparisonEntity] = Field(default_factory=list)
    leader_name: str
    leader_value: float
    laggard_name: str
    laggard_value: float
    difference: float
    relative_difference: float
    statement: str


class FinancialFinding(BaseModel):
    finding_id: str = Field(default_factory=lambda: _financial_id("finding"))
    finding_type: str
    statement: str
    company_names: list[str] = Field(default_factory=list)
    periods: list[str] = Field(default_factory=list)
    metric_id: Optional[str] = None
    metric_name: Optional[str] = None
    evidence_ids: list[str] = Field(default_factory=list)
    calculation_ids: list[str] = Field(default_factory=list)
    risk_signal_ids: list[str] = Field(default_factory=list)
    anomaly_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FinancialRiskSignal(BaseModel):
    company_name: str
    period: str
    signal_id: str
    severity: str = "medium"
    message: str
    metric_id: str
    value: float
    unit: str = "x"
    display_value: str = ""


class FinancialAnomalySignal(BaseModel):
    anomaly_id: str = Field(default_factory=lambda: _financial_id("anomaly"))
    company_name: str
    period: str
    metric_id: str
    metric_name: str
    value: float
    historical_mean: float
    historical_std: float
    z_score: float
    threshold: float = 2.0
    method: str = "historical_mean_std_baseline"
    message: str


class FinancialAnalysisResult(BaseModel):
    query: FinancialQuery
    task_type: str
    metrics: list[str] = Field(default_factory=list)
    observations: list[FinancialObservation] = Field(default_factory=list)
    calculations: list[FinancialCalculation] = Field(default_factory=list)
    verifications: list[FinancialVerification] = Field(default_factory=list)
    evidence: list[FinancialEvidence] = Field(default_factory=list)
    comparisons: list[FinancialComparison] = Field(default_factory=list)
    findings: list[FinancialFinding] = Field(default_factory=list)
    risk_signals: list[FinancialRiskSignal] = Field(default_factory=list)
    anomalies: list[FinancialAnomalySignal] = Field(default_factory=list)
    data_sources: list[FinancialDataSource] = Field(default_factory=list)
    summary: str = ""
    report_markdown: str = ""
    generated_at: str = Field(default_factory=utc_now)
