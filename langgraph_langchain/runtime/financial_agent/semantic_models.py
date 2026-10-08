"""Additional semantic models for Runtime V10.1."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, computed_field

from .models import CompanyResolution


class CompanyMatchType(str, Enum):
    EXACT_NAME = "EXACT_NAME"
    COMPANY_ID = "COMPANY_ID"
    STOCK_CODE = "STOCK_CODE"
    ALIAS = "ALIAS"


class CompanyMatch(BaseModel):
    company_id: str
    company_name: str
    stock_code: str = ""
    matched_term: str
    match_type: CompanyMatchType


class CompanyResolutionResult(BaseModel):
    resolved: list[CompanyResolution] = Field(default_factory=list)
    unresolved_terms: list[str] = Field(default_factory=list)
    matches: list[CompanyMatch] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class PeriodCoverage(BaseModel):
    company_id: str
    company_name: str
    requested_start_year: int | None = None
    requested_end_year: int | None = None
    available_periods: list[str] = Field(default_factory=list)
    selected_periods: list[str] = Field(default_factory=list)
    missing_periods: list[str] = Field(default_factory=list)
    status: str = "COMPLETE"

    @property
    def ready_for_execution(self) -> bool:
        """Whether at least one requested period can be analyzed."""
        return bool(self.selected_periods)


class MetricDefinitionResolution(BaseModel):
    metric_id: str
    name: str
    category: str
    formula: str
    unit: str
    calculation_type: str
    balance_policy: str
    source_fields: list[str] = Field(default_factory=list)


class MetricResolutionResult(BaseModel):
    resolved: list[MetricDefinitionResolution] = Field(default_factory=list)
    unresolved_metrics: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class DataRequirement(BaseModel):
    metric_id: str
    scope: str
    statement_type: str
    field_name: str
    source: str


class DataCoverageGap(BaseModel):
    company_id: str
    company_name: str
    period: str
    metric_id: str
    scope: str
    statement_type: str
    field_name: str
    reason: str


class MetricCoverage(BaseModel):
    company_id: str
    company_name: str
    period: str
    metric_id: str
    status: str
    missing_fields: list[str] = Field(default_factory=list)
    previous_period: str | None = None


class CompanyDataCoverage(BaseModel):
    company_id: str
    company_name: str
    available_periods: list[str] = Field(default_factory=list)
    selected_periods: list[str] = Field(default_factory=list)
    missing_periods: list[str] = Field(default_factory=list)
    metric_coverage: list[MetricCoverage] = Field(default_factory=list)
    blockers: list[DataCoverageGap] = Field(default_factory=list)
    warnings: list[DataCoverageGap] = Field(default_factory=list)

    @property
    def status(self) -> str:
        if self.missing_periods:
            return "MISSING_PERIODS"
        if self.blockers:
            return "PARTIAL"
        if self.warnings:
            return "COMPLETE_WITH_WARNINGS"
        return "COMPLETE"


class DataCoverageResult(BaseModel):
    companies: list[CompanyDataCoverage] = Field(default_factory=list)
    blockers: list[DataCoverageGap] = Field(default_factory=list)
    warnings: list[DataCoverageGap] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class SemanticResolution(BaseModel):
    companies: CompanyResolutionResult
    periods: list[PeriodCoverage]
    metrics: MetricResolutionResult
    data_coverage: DataCoverageResult
    diagnostics: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    clarifications: list[ClarificationRequest] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @computed_field  # type: ignore[misc]
    @property
    def ready_for_planning(self) -> bool:
        return not self.blockers


class ClarificationRequest(BaseModel):
    diagnostic: str
    message: str
    company_id: str | None = None
    field: str | None = None
