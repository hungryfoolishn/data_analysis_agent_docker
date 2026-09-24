"""Financial fact layer linking raw source values to normalized statements."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional
from uuid import uuid4

from .models import (
    BalanceSheetStatement,
    CashFlowStatement,
    FinancialDataSource,
    IncomeStatement,
)
from .period import PeriodNormalizer
from .unit import UnitConversion, UnitNormalizer


@dataclass(frozen=True)
class FinancialFact:
    fact_id: str
    company_id: str
    period: str
    metric_id: str
    value: float | None
    unit: str
    source_id: str
    source_label: str | None = None
    original_period: str | None = None
    original_metric_name: str | None = None
    original_value: float | None = None
    original_unit: str = "CNY"
    conversion_rule: str = "none"
    normalized_unit: str = "CNY"
    status: str = "VALID"


class FinancialFactBuilder:
    """Build normalized facts and preserve original field provenance."""

    _INCOME_FIELDS = (
        "revenue",
        "cost_of_revenue",
        "gross_profit",
        "operating_profit",
        "net_profit",
        "net_profit_attributable",
        "eps",
    )
    _BALANCE_FIELDS = (
        "total_assets",
        "total_liabilities",
        "total_equity",
        "cash",
        "accounts_receivable",
        "inventory",
        "fixed_assets",
        "short_term_debt",
        "long_term_debt",
        "current_assets",
        "current_liabilities",
    )
    _CASH_FIELDS = (
        "operating_cash_flow",
        "investing_cash_flow",
        "financing_cash_flow",
        "capital_expenditure",
        "free_cash_flow",
    )

    def __init__(self, period_normalizer: PeriodNormalizer | None = None) -> None:
        self.period_normalizer = period_normalizer or PeriodNormalizer()
        self.unit_normalizer = UnitNormalizer()

    def from_statements(
        self,
        *,
        income: IncomeStatement,
        balance: BalanceSheetStatement,
        cash_flow: CashFlowStatement,
        source: FinancialDataSource,
    ) -> list[FinancialFact]:
        facts: list[FinancialFact] = []
        facts.extend(self._from_statement(income, source, self._INCOME_FIELDS, "income_statement"))
        facts.extend(self._from_statement(balance, source, self._BALANCE_FIELDS, "balance_sheet"))
        facts.extend(self._from_statement(cash_flow, source, self._CASH_FIELDS, "cash_flow"))
        return facts

    def _from_statement(self, statement, source: FinancialDataSource, fields, source_kind: str):
        facts: list[FinancialFact] = []
        normalized_period = self.period_normalizer.normalize(statement.period)
        for field in fields:
            value = getattr(statement, field)
            try:
                conversion = self.unit_normalizer.normalize(value, "CNY")
                status = "VALID" if conversion.status == "calculated" else conversion.status.upper()
                normalized_value = conversion.normalized_value
                reason = conversion.reason
            except Exception as exc:
                status = "INVALID"
                normalized_value = None
                reason = str(exc)
                conversion = UnitConversion(
                    original_value=value,
                    original_unit="CNY",
                    normalized_value=None,
                    normalized_unit="CNY",
                    conversion_rule="none",
                    status="invalid",
                    reason=reason,
                )
            facts.append(FinancialFact(
                fact_id=f"fact_{source.source_id}_{field}",
                company_id=statement.company_id,
                period=normalized_period,
                metric_id=field,
                value=normalized_value,
                unit="CNY",
                source_id=source.source_id,
                source_label=f"{source_kind}.{field}",
                original_period=statement.period,
                original_metric_name=field,
                original_value=value,
                original_unit="CNY",
                conversion_rule=conversion.conversion_rule,
                normalized_unit=conversion.normalized_unit,
                status=status,
            ))
        return facts

    def require_fact(self, facts: Iterable[FinancialFact], metric_id: str, company_id: str, period: str) -> FinancialFact:
        for fact in facts:
            if (
                fact.metric_id == metric_id
                and fact.company_id == company_id
                and fact.period == period
            ):
                return fact
        raise KeyError(f"Financial fact not found: {company_id}/{period}/{metric_id}")
