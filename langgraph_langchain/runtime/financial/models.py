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
    revenue: float = 0.0
    cost_of_revenue: float = 0.0
    gross_profit: float = 0.0
    operating_profit: float = 0.0
    net_profit: float = 0.0
    net_profit_attributable: float = 0.0
    eps: float = 0.0
    source_id: Optional[str] = None


class BalanceSheetStatement(BaseModel):
    company_id: str
    period: str
    currency: str = "CNY"
    total_assets: float = 0.0
    total_liabilities: float = 0.0
    total_equity: float = 0.0
    cash: float = 0.0
    accounts_receivable: float = 0.0
    inventory: float = 0.0
    fixed_assets: float = 0.0
    short_term_debt: float = 0.0
    long_term_debt: float = 0.0
    current_assets: float = 0.0
    current_liabilities: float = 0.0
    source_id: Optional[str] = None


class CashFlowStatement(BaseModel):
    company_id: str
    period: str
    currency: str = "CNY"
    operating_cash_flow: float = 0.0
    investing_cash_flow: float = 0.0
    financing_cash_flow: float = 0.0
    capital_expenditure: float = 0.0
    free_cash_flow: float = 0.0
    source_id: Optional[str] = None


class FinancialIndicator(BaseModel):
    company_id: str
    period: str
    metric_id: str
    value: float
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
    company_name: str = ""
    period: str = ""
    formula: str
    inputs: dict[str, float] = Field(default_factory=dict)
    result: float
    unit: str


class FinancialQuery(BaseModel):
    question: str
    task_type: Optional[str] = None
    company_names: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    start_year: Optional[int] = None
    end_year: Optional[int] = None


class FinancialObservation(BaseModel):
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


class FinancialRiskSignal(BaseModel):
    company_name: str
    period: str
    signal_id: str
    severity: str = "medium"
    message: str
    metric_id: str
    value: float


class FinancialAnalysisResult(BaseModel):
    query: FinancialQuery
    task_type: str
    metrics: list[str] = Field(default_factory=list)
    observations: list[FinancialObservation] = Field(default_factory=list)
    calculations: list[FinancialCalculation] = Field(default_factory=list)
    risk_signals: list[FinancialRiskSignal] = Field(default_factory=list)
    summary: str = ""
    report_markdown: str = ""
    generated_at: str = Field(default_factory=utc_now)
