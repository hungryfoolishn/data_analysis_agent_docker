"""Data access service for standardized financial statements."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from .models import (
    BalanceSheetStatement,
    CashFlowStatement,
    Company,
    FinancialDataSource,
    IncomeStatement,
)
from .period import PeriodNormalizer


class FinancialDataService:
    """Provide normalized statements and their source entities to workflows."""

    def __init__(
        self,
        *,
        companies: list[Company],
        income_statements: list[IncomeStatement],
        balance_sheets: list[BalanceSheetStatement],
        cash_flows: list[CashFlowStatement],
    ) -> None:
        self.companies = companies
        self.income_statements = income_statements
        self.balance_sheets = balance_sheets
        self.cash_flows = cash_flows

        self._companies_by_id = {item.company_id: item for item in companies}
        self._companies_by_name = {item.company_name: item for item in companies}
        self._companies_by_code = {item.stock_code: item for item in companies}
        self._period_normalizer = PeriodNormalizer()
        self._income_by_key = self._build_statement_index(
            income_statements,
            "income",
        )
        self._balance_by_key = self._build_statement_index(
            balance_sheets,
            "balance",
        )
        self._cash_by_key = self._build_statement_index(
            cash_flows,
            "cash flow",
        )
        self._canonical_periods_by_company = self._build_periods()
        self._sources_by_id = self._build_sources()

    @classmethod
    def from_csv_directory(cls, directory: str | Path) -> "FinancialDataService":
        root = Path(directory)

        def _read(filename: str) -> list[dict[str, str]]:
            with (root / filename).open("r", encoding="utf-8-sig", newline="") as handle:
                return list(csv.DictReader(handle))

        companies = [Company.model_validate(item) for item in _read("companies.csv")]
        income = [IncomeStatement.model_validate(item) for item in _read("income_statement.csv")]
        balance = [BalanceSheetStatement.model_validate(item) for item in _read("balance_sheet.csv")]
        cash = [CashFlowStatement.model_validate(item) for item in _read("cash_flow.csv")]
        return cls(
            companies=companies,
            income_statements=income,
            balance_sheets=balance,
            cash_flows=cash,
        )

    def _statement_key(
        self,
        item: IncomeStatement | BalanceSheetStatement | CashFlowStatement,
    ) -> tuple[str, str]:
        """Map statements by canonical financial period, not display text."""
        return (
            item.company_id,
            self._period_normalizer.normalize(item.period),
        )

    def _build_statement_index(
        self,
        statements: list[IncomeStatement] | list[BalanceSheetStatement] | list[CashFlowStatement],
        statement_type: str,
    ):
        """Index statements while rejecting ambiguous canonical periods."""
        index: dict[tuple[str, str], IncomeStatement | BalanceSheetStatement | CashFlowStatement] = {}
        for item in statements:
            key = self._statement_key(item)
            if key in index:
                previous = index[key]
                raise ValueError(
                    f"Duplicate {statement_type} canonical period for "
                    f"{key[0]}/{key[1]}: {previous.period} and {item.period}"
                )
            index[key] = item
        return index

    def _build_periods(self) -> dict[str, list[str]]:
        periods: dict[str, set[str]] = {}
        for item in self.income_statements:
            periods.setdefault(item.company_id, set()).add(
                self._period_normalizer.normalize(item.period)
            )
        return {
            company_id: sorted(
                values,
                key=self._period_normalizer.order_key,
            )
            for company_id, values in periods.items()
        }

    def _build_sources(self) -> dict[str, FinancialDataSource]:
        rows: list[tuple[str, str, IncomeStatement | BalanceSheetStatement | CashFlowStatement]] = []
        rows.extend(("income_statement", item.source_id or "", item) for item in self.income_statements)
        rows.extend(("balance_sheet", item.source_id or "", item) for item in self.balance_sheets)
        rows.extend(("cash_flow", item.source_id or "", item) for item in self.cash_flows)

        sources: dict[str, FinancialDataSource] = {}
        for source_kind, source_id, statement in rows:
            if not source_id or source_id in sources:
                continue
            payload = json.dumps(
                statement.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            sources[source_id] = FinancialDataSource(
                source_id=source_id,
                source_type="annual_report",
                company_id=statement.company_id,
                report_period=self._period_normalizer.normalize(statement.period),
                document_name=f"{source_kind}.csv#{source_id}",
                source_hash=f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}",
            )
        return sources

    def list_companies(self) -> list[Company]:
        return sorted(self.companies, key=lambda item: item.company_name)

    def resolve_company(self, identifier: str) -> Company:
        identifier = identifier.strip()
        company = (
            self._companies_by_id.get(identifier)
            or self._companies_by_name.get(identifier)
            or self._companies_by_code.get(identifier)
        )
        if company is None:
            raise KeyError(f"Unknown company: {identifier}")
        return company

    def periods_for(self, company_id: str) -> list[str]:
        """Return canonical periods ordered by financial period semantics."""
        return list(self._canonical_periods_by_company.get(company_id, []))

    def statements(
        self,
        company_id: str,
        period: str,
        previous_period: str | None = None,
    ) -> tuple[IncomeStatement, BalanceSheetStatement, CashFlowStatement, IncomeStatement | None]:
        canonical_period = self._period_normalizer.normalize(period)
        canonical_previous_period = (
            self._period_normalizer.normalize(previous_period)
            if previous_period is not None
            else None
        )
        key = (company_id, canonical_period)
        if key not in self._income_by_key or key not in self._balance_by_key or key not in self._cash_by_key:
            raise KeyError(f"No financial statements for {company_id} {period}")
        previous_income = self._income_by_key.get(
            (company_id, canonical_previous_period)
        )
        return (
            self._income_by_key[key],
            self._balance_by_key[key],
            self._cash_by_key[key],
            previous_income,
        )

    def previous_balance(self, company_id: str, period: str) -> BalanceSheetStatement | None:
        previous_period = self.previous_period(company_id, period)
        if previous_period is None:
            return None
        return self._balance_by_key.get(
            (
                company_id,
                self._period_normalizer.normalize(previous_period),
            )
        )

    def previous_period(self, company_id: str, period: str) -> str | None:
        """Return the semantically previous period when present in the dataset."""
        canonical_period = self._period_normalizer.normalize(period)
        periods = self.periods_for(company_id)
        if canonical_period not in periods:
            raise KeyError(f"Unknown period {period} for {company_id}")
        previous = self._period_normalizer.parse(canonical_period).previous()
        # Missing prior periods must remain absent so average-balance metrics
        # become UNAVAILABLE instead of silently using an older available period.
        return previous.normalized_period if previous.normalized_period in periods else None

    def list_sources(self) -> list[FinancialDataSource]:
        return sorted(self._sources_by_id.values(), key=lambda item: item.source_id)

    def get_source(self, source_id: str) -> FinancialDataSource:
        return self._sources_by_id[source_id]

    def get_sources(self, source_ids: list[str]) -> list[FinancialDataSource]:
        return [
            self._sources_by_id[source_id]
            for source_id in dict.fromkeys(source_ids)
            if source_id in self._sources_by_id
        ]
