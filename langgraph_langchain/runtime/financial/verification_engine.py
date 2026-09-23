"""Independent financial formula verification."""

from __future__ import annotations

from typing import Optional

from .models import (
    BalanceSheetStatement,
    CashFlowStatement,
    FinancialCalculation,
    FinancialVerification,
    IncomeStatement,
)


class FinancialVerificationEngine:
    """Verify calculations using independently coded statement formulas.

    This engine intentionally does not call the metric engine.  It provides a
    second implementation path so that a passing result is not merely evidence
    that the same deterministic function returned the same value twice.
    """

    def verify(
        self,
        *,
        calculation: FinancialCalculation,
        metric_id: str,
        income: IncomeStatement,
        balance: BalanceSheetStatement,
        cash_flow: CashFlowStatement,
        previous_income: IncomeStatement | None = None,
        previous_balance: BalanceSheetStatement | None = None,
        tolerance: float = 0.000001,
    ) -> FinancialVerification:
        common = {
            "metric_id": metric_id,
            "company_id": calculation.company_id,
            "company_name": calculation.company_name,
            "period": calculation.period,
            "calculation_id": calculation.calculation_id,
        }
        if calculation.status != "calculated" or calculation.result is None:
            return FinancialVerification(
                **common,
                status="unavailable",
                passed=False,
                expected_value=calculation.result,
                actual_value=None,
                message="指标未完成计算，无法进行独立验证。",
                missing_fields=list(calculation.missing_fields),
            )

        try:
            actual_value, missing_fields = self._calculate(
                metric_id,
                income=income,
                balance=balance,
                cash_flow=cash_flow,
                previous_income=previous_income,
                previous_balance=previous_balance,
            )
        except ValueError as exc:
            return FinancialVerification(
                **common,
                status="unavailable",
                passed=False,
                expected_value=calculation.result,
                actual_value=None,
                message=str(exc),
                missing_fields=list(calculation.missing_fields),
            )

        if actual_value is None:
            unavailable = bool(missing_fields)
            return FinancialVerification(
                **common,
                status="unavailable" if unavailable else "invalid",
                passed=False,
                expected_value=calculation.result,
                actual_value=None,
                message=(
                    "独立验证所需报表字段缺失。"
                    if unavailable
                    else "分母为零或结果无效，无法验证。"
                ),
                missing_fields=missing_fields,
            )

        passed = abs(calculation.result - actual_value) <= max(
            tolerance,
            abs(calculation.result) * tolerance,
        )
        return FinancialVerification(
            **common,
            status="passed" if passed else "failed",
            passed=passed,
            expected_value=calculation.result,
            actual_value=actual_value,
            message=(
                "指标结果通过独立公式验证。"
                if passed
                else "独立公式验证结果与 Metric Engine 不一致。"
            ),
            missing_fields=missing_fields,
        )

    def _calculate(
        self,
        metric_id: str,
        *,
        income: IncomeStatement,
        balance: BalanceSheetStatement,
        cash_flow: CashFlowStatement,
        previous_income: IncomeStatement | None,
        previous_balance: BalanceSheetStatement | None,
    ) -> tuple[Optional[float], list[str]]:
        missing: list[str] = []
        reported = {
            "revenue": income.revenue,
            "cost_of_revenue": income.cost_of_revenue,
            "gross_profit": income.gross_profit,
            "operating_profit": income.operating_profit,
            "net_profit": income.net_profit,
            "net_profit_attributable": income.net_profit_attributable,
            "eps": income.eps,
            "total_assets": balance.total_assets,
            "total_liabilities": balance.total_liabilities,
            "total_equity": balance.total_equity,
            "cash": balance.cash,
            "accounts_receivable": balance.accounts_receivable,
            "inventory": balance.inventory,
            "fixed_assets": balance.fixed_assets,
            "short_term_debt": balance.short_term_debt,
            "long_term_debt": balance.long_term_debt,
            "current_assets": balance.current_assets,
            "current_liabilities": balance.current_liabilities,
            "operating_cash_flow": cash_flow.operating_cash_flow,
            "investing_cash_flow": cash_flow.investing_cash_flow,
            "financing_cash_flow": cash_flow.financing_cash_flow,
            "capital_expenditure": cash_flow.capital_expenditure,
        }
        if metric_id in reported:
            value = reported[metric_id]
            if value is None:
                missing.append(metric_id)
                raise ValueError(f"报表字段缺失: {metric_id}")
            return round(float(value), 6), missing

        if metric_id == "revenue_growth":
            return self._growth(income.revenue, self._previous_income(previous_income, "revenue", missing), missing), missing
        if metric_id == "net_profit_growth":
            return self._growth(income.net_profit, self._previous_income(previous_income, "net_profit", missing), missing), missing
        if metric_id == "operating_profit_growth":
            return self._growth(income.operating_profit, self._previous_income(previous_income, "operating_profit", missing), missing), missing
        if metric_id == "eps_growth":
            return self._growth(income.eps, self._previous_income(previous_income, "eps", missing), missing), missing
        if metric_id == "free_cash_flow":
            value = self._ratio(
                self._sum([cash_flow.operating_cash_flow], missing, ["operating_cash_flow"]),
                self._first([cash_flow.capital_expenditure], missing, ["capital_expenditure"]),
                difference=True,
            )
            return value, missing
        if metric_id == "gross_margin":
            return self._ratio(income.gross_profit, income.revenue, 100), missing
        if metric_id == "operating_margin":
            return self._ratio(income.operating_profit, income.revenue, 100), missing
        if metric_id == "net_margin":
            return self._ratio(income.net_profit, income.revenue, 100), missing
        if metric_id == "roe":
            return self._average_ratio(income.net_profit, balance.total_equity, self._previous_balance(previous_balance, "total_equity", missing), 100), missing
        if metric_id == "roa":
            return self._average_ratio(income.net_profit, balance.total_assets, self._previous_balance(previous_balance, "total_assets", missing), 100), missing
        if metric_id == "debt_to_asset":
            return self._ratio(balance.total_liabilities, balance.total_assets, 100), missing
        if metric_id == "current_ratio":
            return self._ratio(balance.current_assets, balance.current_liabilities), missing
        if metric_id == "quick_ratio":
            numerator = self._sum([balance.cash, balance.accounts_receivable], missing, ["balance.cash", "balance.accounts_receivable"])
            return self._ratio(numerator, balance.current_liabilities), missing
        if metric_id == "receivable_turnover":
            return self._average_ratio(income.revenue, balance.accounts_receivable, self._previous_balance(previous_balance, "accounts_receivable", missing)), missing
        if metric_id == "inventory_turnover":
            return self._average_ratio(income.cost_of_revenue, balance.inventory, self._previous_balance(previous_balance, "inventory", missing)), missing
        if metric_id == "asset_turnover":
            return self._average_ratio(income.revenue, balance.total_assets, self._previous_balance(previous_balance, "total_assets", missing)), missing
        if metric_id == "ocf_to_net_income":
            return self._ratio(cash_flow.operating_cash_flow, income.net_profit), missing
        raise ValueError(f"独立验证暂不支持指标: {metric_id}")

    @staticmethod
    def _previous_income(statement: IncomeStatement | None, field: str, missing: list[str]) -> Optional[float]:
        value = None if statement is None else getattr(statement, field)
        if statement is None:
            missing.append("previous_income")
        if value is None:
            missing.append(f"previous_income.{field}")
        return value

    @staticmethod
    def _previous_balance(statement: BalanceSheetStatement | None, field: str, missing: list[str]) -> Optional[float]:
        value = None if statement is None else getattr(statement, field)
        if statement is None:
            missing.append("previous_balance")
        if value is None:
            missing.append(f"previous_balance.{field}")
        return value

    @staticmethod
    def _first(values: list[Optional[float]], missing: list[str], fields: list[str]) -> Optional[float]:
        for value in values:
            if value is not None:
                return value
        missing.extend(fields)
        return None

    @staticmethod
    def _sum(values: list[Optional[float]], missing: list[str], fields: list[str]) -> Optional[float]:
        if any(value is None for value in values):
            missing.extend(fields)
            return None
        return sum(float(value) for value in values if value is not None)

    @staticmethod
    def _growth(current: Optional[float], previous: Optional[float], missing: list[str]) -> Optional[float]:
        if current is None or previous is None:
            return None
        if previous == 0:
            return None
        return round((current - previous) / abs(previous) * 100, 6)

    @staticmethod
    def _ratio(numerator: Optional[float], denominator: Optional[float], multiplier: float = 1, *, difference: bool = False) -> Optional[float]:
        if numerator is None or denominator is None:
            return None
        if not difference and denominator == 0:
            return None
        return round((numerator - denominator) if difference else numerator / denominator * multiplier, 6)

    @staticmethod
    def _average_ratio(
        numerator: Optional[float],
        current_balance: Optional[float],
        previous_balance: Optional[float],
        multiplier: float = 1,
    ) -> Optional[float]:
        if numerator is None or current_balance is None or previous_balance is None:
            return None
        average = (current_balance + previous_balance) / 2
        if average == 0:
            return None
        return round(numerator / average * multiplier, 6)
