"""Data access service for standardized financial statements."""

from __future__ import annotations

import csv
from pathlib import Path

from .models import BalanceSheetStatement, CashFlowStatement, Company, IncomeStatement


class FinancialDataService:
    """Provide normalized financial statements to deterministic workflows."""

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
        self._income_by_key = {
            (item.company_id, item.period): item for item in income_statements
        }
        self._balance_by_key = {
            (item.company_id, item.period): item for item in balance_sheets
        }
        self._cash_by_key = {
            (item.company_id, item.period): item for item in cash_flows
        }

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
        return sorted({
            item.period for item in self.income_statements
            if item.company_id == company_id
        })

    def statements(
        self,
        company_id: str,
        period: str,
        previous_period: str | None = None,
    ) -> tuple[IncomeStatement, BalanceSheetStatement, CashFlowStatement, IncomeStatement | None]:
        key = (company_id, period)
        if key not in self._income_by_key or key not in self._balance_by_key or key not in self._cash_by_key:
            raise KeyError(f"No financial statements for {company_id} {period}")
        previous_income = self._income_by_key.get((company_id, previous_period))
        return (
            self._income_by_key[key],
            self._balance_by_key[key],
            self._cash_by_key[key],
            previous_income,
        )

    def previous_balance(self, company_id: str, period: str) -> BalanceSheetStatement | None:
        """Return the prior-period balance sheet for average-balance metrics."""
        previous_period = self.previous_period(company_id, period)
        if previous_period is None:
            return None
        return self._balance_by_key.get((company_id, previous_period))

    def previous_period(self, company_id: str, period: str) -> str | None:
        periods = self.periods_for(company_id)
        try:
            index = periods.index(period)
        except ValueError as exc:
            raise KeyError(f"Unknown period {period} for {company_id}") from exc
        return periods[index - 1] if index > 0 else None
