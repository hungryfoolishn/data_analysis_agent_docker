"""Stable models for Runtime V9 financial analysis + B10.2 canonical models."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Optional
from uuid import uuid4, uuid5, NAMESPACE_URL

from pydantic import BaseModel, Field, field_validator, model_validator

from langgraph_langchain.runtime.models import utc_now
from .period import PeriodBasisType, PeriodComparisonType


def _financial_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


NAMESPACE_FINANCIAL = uuid5(NAMESPACE_URL, "financial-pack-b10.2")


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
    periods: list[str] = Field(default_factory=list)
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    growth_comparison_type: PeriodComparisonType = (
        PeriodComparisonType.YEAR_OVER_YEAR
    )
    growth_period_basis: PeriodBasisType = PeriodBasisType.CUMULATIVE


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


# ---------------------------------------------------------------------------
# B10.2 Canonical financial models (B10.2.2).
#
# The legacy fixed-field models above remain the V9 compat surface; the
# canonical models below are the authority for normalized financial data.
# Legacy models never bypass the canonical layer (compat adapters in
# financial/compat.py) and never share authority with it.
#
# Design rules (B10.1 §6/§7, B10.2 task §6, identity doc §3):
# - Decimal is the authority numeric type (floats never enter canonical
#   storage);
# - statistic kind is explicit — never guessed from unit strings; unknown is
#   rejected where semantics matter;
# - unit semantics are layered (statistic_kind × numeric_unit × currency ×
#   denominator × scale) so 25% ≠ 25× and USD/share ≠ CNY/share;
# - instant vs duration period semantics are explicit (balance = instant,
#   income/cash = duration with period_start + period_end);
# - filing metadata (filing_date / accession / amendment) is separated from
#   report periods and from DataAsset content versions.
# ---------------------------------------------------------------------------

FinancialStatisticKind = Literal[
    "absolute_amount",   # 绝对金额 (Revenue)
    "per_share",         # 每股数值 (EPS: currency/share)
    "percent_ratio",     # 百分比比率 (Gross Margin %)
    "times_ratio",       # 倍数比率 (Current Ratio ×)
    "growth_rate",       # 增长率（同比/环比百分比；比较基期独立声明）
    "yoy_index",         # 同比指数（上年同期=100；仅明确声明时才可 −100）
    "unknown",           # 未声明——需要语义转换时显式拒绝
]

GrowthComparisonBasis = Literal["yoy", "qoq", "mom", "unknown"]
ReportingBasis = Literal["consolidated", "parent"]
StatementTypeKind = Literal["income", "balance", "cash"]
PeriodTypeKind = Literal["instant", "duration"]
FiscalPeriodKind = Literal[
    "FY", "Q1", "Q2", "Q3", "Q4", "H1", "9M", "YTD", "TTM",
]

_MARKET_WHITELIST = {"us", "cn"}
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
_CANONICAL_CONCEPT_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,80}$")
_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_iso_date(value: Optional[str], field_name: str) -> Optional[str]:
    if value is None:
        return None
    if not _ISO_DATE_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must be an ISO date (YYYY-MM-DD), got {value!r}")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date (YYYY-MM-DD), got {value!r}") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field_name} must be canonical ISO date, got {value!r}")
    return value


class FinancialUnit(BaseModel):
    """Layered unit semantics — never a bare string.

    ``statistic_kind`` is what the VALUE means; ``numeric_unit`` is the
    physical measure ("CNY", "share", "percent", "times", "1"); ``currency``
    applies to monetary and per-share kinds; ``unit_denominator`` carries the
    per-share denominator; ``scale`` is the declared magnitude factor of the
    SOURCE value (1000 for 千元 etc., Decimal("1") default).
    """

    model_config = {"frozen": True}

    statistic_kind: FinancialStatisticKind = "unknown"
    numeric_unit: str = "1"
    currency: Optional[str] = None
    unit_denominator: Optional[str] = None
    output_unit: Optional[str] = None
    scale: Decimal = Decimal("1")

    @field_validator("currency")
    @classmethod
    def _currency_iso(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not _CURRENCY_PATTERN.fullmatch(value):
            raise ValueError(f"currency must be ISO 4217 (AAA), got {value!r}")
        return value

    @field_validator("scale")
    @classmethod
    def _positive_scale(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError(f"scale must be positive, got {value}")
        return value

    @model_validator(mode="after")
    def _kind_unit_consistency(self) -> "FinancialUnit":
        kind = self.statistic_kind
        unit = self.numeric_unit
        if kind == "per_share" and self.unit_denominator != "share":
            raise ValueError("per_share requires unit_denominator='share'")
        if kind in {"percent_ratio", "growth_rate", "yoy_index"} and unit not in {"", "percent"}:
            raise ValueError(
                f"{kind} requires numeric_unit 'percent', got {unit!r}"
            )
        if kind == "times_ratio" and unit not in {"", "times"}:
            raise ValueError(f"times_ratio requires numeric_unit 'times', got {unit!r}")
        if kind == "absolute_amount" and not self.currency:
            raise ValueError("absolute_amount requires an explicit currency")
        if kind == "per_share" and not self.currency:
            raise ValueError("per_share requires an explicit currency")
        if self.currency and kind not in {"absolute_amount", "per_share"}:
            raise ValueError(
                f"currency applies only to absolute_amount/per_share, not {kind!r}"
            )
        return self


class Entity(BaseModel):
    """A real reporting entity — identity never rests on ticker alone."""

    model_config = {"frozen": True}

    entity_id: str = ""
    legal_name: str
    display_name: Optional[str] = None
    market: str
    exchange: Optional[str] = None
    ticker: Optional[str] = None
    country: Optional[str] = None
    identifiers: dict[str, str] = Field(default_factory=dict)  # authority ids
    currency_context: str  # primary reporting currency (ISO 4217)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("market")
    @classmethod
    def _known_market(cls, value: str) -> str:
        if value not in _MARKET_WHITELIST:
            raise ValueError(f"market must be one of {sorted(_MARKET_WHITELIST)}, got {value!r}")
        return value

    @field_validator("currency_context")
    @classmethod
    def _currency_context(cls, value: str) -> str:
        if not _CURRENCY_PATTERN.fullmatch(value):
            raise ValueError(f"currency_context must be ISO 4217, got {value!r}")
        return value

    @model_validator(mode="after")
    def _stable_entity_id(self) -> "Entity":
        if not self.legal_name or not self.legal_name.strip():
            raise ValueError("legal_name is required (an entity without a "
                             "name has no derivable identity)")
        if self.entity_id:
            return self
        # Deterministic internal id from (market, exchange, ticker, legal_name)
        # — stable across runs, independent of any single external identifier.
        seed = "|".join(
            str(part or "")
            for part in (self.market, self.exchange, self.ticker, self.legal_name)
        )
        if not seed.strip("|"):
            raise ValueError("entity_id requires market/legal_name to derive")
        derived = f"ent_{uuid5(NAMESPACE_FINANCIAL, seed).hex}"
        object.__setattr__(self, "entity_id", derived)
        return self


class FinancialLineItem(BaseModel):
    """One canonical line item — the authority for normalized values."""

    model_config = {"frozen": True}

    item_id: str = Field(default_factory=lambda: _financial_id("item"))
    canonical_concept: str
    original_concept: Optional[str] = None
    value: Optional[Decimal] = None  # None = missing; never fabricated to 0
    statistic_kind: FinancialStatisticKind = "unknown"
    comparison_basis: Optional[GrowthComparisonBasis] = None  # growth_rate only
    unit: FinancialUnit = Field(default_factory=FinancialUnit)
    period_start: Optional[str] = None  # ISO date; duration items only
    period_end: Optional[str] = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    normalization_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("period_start")
    @classmethod
    def _item_period_start(cls, value, info):
        return _validate_iso_date(value, "period_start")

    @field_validator("period_end")
    @classmethod
    def _item_period_end(cls, value, info):
        return _validate_iso_date(value, "period_end")

    @field_validator("canonical_concept")
    @classmethod
    def _canonical_concept(cls, value: str) -> str:
        if not _CANONICAL_CONCEPT_PATTERN.fullmatch(value):
            raise ValueError(
                f"canonical_concept must be snake_case, got {value!r}"
            )
        return value

    @model_validator(mode="after")
    def _item_semantics_are_consistent(self) -> "FinancialLineItem":
        if self.unit.statistic_kind != self.statistic_kind:
            raise ValueError(
                "line item statistic_kind and unit.statistic_kind must match: "
                f"{self.statistic_kind!r} != {self.unit.statistic_kind!r}"
            )
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError(
                "period_start must not be after period_end: "
                f"{self.period_start} > {self.period_end}"
            )
        return self

    @model_validator(mode="after")
    def _growth_requires_comparison_basis(self) -> "FinancialLineItem":
        # model_validator (not field_validator): defaults never trigger field
        # validators, and a growth_rate item WITHOUT a declared basis must be
        # rejected even when comparison_basis falls back to its default None.
        if self.statistic_kind == "growth_rate" and self.comparison_basis is None:
            raise ValueError("growth_rate items must declare comparison_basis")
        if self.comparison_basis is not None and self.statistic_kind != "growth_rate":
            raise ValueError(
                "comparison_basis is only valid for growth_rate items, "
                f"not {self.statistic_kind!r}"
            )
        return self


class FinancialStatement(BaseModel):
    """Canonical statement — instant (balance) or duration (income/cash)."""

    model_config = {"frozen": True}

    statement_id: str = Field(default_factory=lambda: _financial_id("stmt"))
    entity_id: str
    statement_type: StatementTypeKind
    period_type: PeriodTypeKind
    period_start: Optional[str] = None  # ISO date; duration only
    period_end: str  # ISO date
    fiscal_year: int
    fiscal_period: FiscalPeriodKind
    reporting_basis: ReportingBasis
    currency: str
    filing_date: Optional[str] = None
    accession_number: Optional[str] = None
    source_document_id: Optional[str] = None
    amendment: bool = False
    source_id: Optional[str] = None
    version: str = ""  # filing version label (e.g. accession or 公告 ID)
    line_items: tuple[FinancialLineItem, ...] = Field(default_factory=tuple)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def _currency_iso(cls, value: str) -> str:
        if not _CURRENCY_PATTERN.fullmatch(value):
            raise ValueError(f"currency must be ISO 4217, got {value!r}")
        return value

    @field_validator("period_type")
    @classmethod
    def _period_type_matches_statement(cls, value: str, info) -> str:
        statement_type = (info.data or {}).get("statement_type")
        expected = "instant" if statement_type == "balance" else "duration"
        if value != expected:
            raise ValueError(
                f"{statement_type} statements must be {expected!r}, got {value!r}"
            )
        return value

    @field_validator("period_start")
    @classmethod
    def _statement_period_start(cls, value, info):
        return _validate_iso_date(value, "period_start")

    @field_validator("period_end")
    @classmethod
    def _statement_period_end(cls, value: str) -> str:
        return _validate_iso_date(value, "period_end")

    @field_validator("period_start")
    @classmethod
    def _duration_requires_start(cls, value, info):
        period_type = (info.data or {}).get("period_type")
        if period_type == "duration" and not value:
            raise ValueError("duration statements require period_start")
        if period_type == "instant" and value is not None:
            raise ValueError("instant statements must not carry period_start")
        return value

    @model_validator(mode="after")
    def _version_defaults_to_filing_identity(self) -> "FinancialStatement":
        if not self.version:
            derived = self.accession_number or self.source_document_id or ""
            object.__setattr__(self, "version", derived)
        return self

    @model_validator(mode="after")
    def _statement_periods_are_consistent(self) -> "FinancialStatement":
        if self.period_type == "duration" and self.period_start > self.period_end:
            raise ValueError(
                "period_start must not be after period_end: "
                f"{self.period_start} > {self.period_end}"
            )
        for item in self.line_items:
            if item.period_start is None and item.period_end is None:
                continue
            if self.period_type == "instant":
                if item.period_start is not None:
                    raise ValueError(
                        f"instant line item {item.canonical_concept!r} must not carry period_start"
                    )
                if item.period_end != self.period_end:
                    raise ValueError(
                        f"instant line item {item.canonical_concept!r} period_end must match "
                        f"statement period_end {self.period_end!r}"
                    )
            else:
                if item.period_start != self.period_start or item.period_end != self.period_end:
                    raise ValueError(
                        f"duration line item {item.canonical_concept!r} period must match "
                        f"statement period {self.period_start!r}..{self.period_end!r}"
                    )
        return self
