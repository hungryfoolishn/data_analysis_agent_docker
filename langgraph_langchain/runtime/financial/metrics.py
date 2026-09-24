"""Deterministic financial metric definitions and calculations."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .models import BalanceSheetStatement, CashFlowStatement, IncomeStatement

CALCULATED = "calculated"
UNAVAILABLE = "unavailable"
INVALID = "invalid"


class FinancialMetricComputationError(ValueError):
    """Raised by the compatibility wrapper when a metric cannot be calculated."""


@dataclass(frozen=True)
class MetricComputation:
    metric_id: str
    status: str
    value: Optional[float] = None
    inputs: dict[str, Optional[float]] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)
    reason: Optional[str] = None


@dataclass(frozen=True)
class FinancialMetricDefinition:
    metric_id: str
    name: str
    category: str
    formula: str
    unit: str
    period_type: str = "annual"
    source_fields: tuple[str, ...] = ()
    calculation_type: str = "reported"
    numerator: Optional[str] = None
    denominator: Optional[str] = None
    base_metric: Optional[str] = None
    growth_of: Optional[str] = None
    tolerance: float = 0.001
    description: str = ""
    balance_policy: str = "ending_balance"


class FinancialMetricRegistry:
    def __init__(self) -> None:
        self._metrics: dict[str, FinancialMetricDefinition] = {}

    def register(self, definition: FinancialMetricDefinition) -> None:
        self._metrics[definition.metric_id] = definition

    def get(self, metric_id: str) -> FinancialMetricDefinition:
        return self._metrics[metric_id]

    def get_optional(self, metric_id: str) -> FinancialMetricDefinition | None:
        return self._metrics.get(metric_id)

    def list_metrics(self) -> list[FinancialMetricDefinition]:
        return sorted(self._metrics.values(), key=lambda item: (item.category, item.metric_id))

    def __len__(self) -> int:
        return len(self._metrics)


financial_metric_registry = FinancialMetricRegistry()
_registry = financial_metric_registry

_BASE_METRICS = [
    ("revenue", "营业收入", "income", "CNY"),
    ("cost_of_revenue", "营业成本", "income", "CNY"),
    ("gross_profit", "毛利", "income", "CNY"),
    ("operating_profit", "营业利润", "income", "CNY"),
    ("net_profit", "净利润", "income", "CNY"),
    ("net_profit_attributable", "归母净利润", "income", "CNY"),
    ("eps", "每股收益", "income", "CNY/share"),
    ("total_assets", "总资产", "balance", "CNY"),
    ("total_liabilities", "总负债", "balance", "CNY"),
    ("total_equity", "股东权益", "balance", "CNY"),
    ("cash", "货币资金", "balance", "CNY"),
    ("accounts_receivable", "应收账款", "balance", "CNY"),
    ("inventory", "存货", "balance", "CNY"),
    ("fixed_assets", "固定资产", "balance", "CNY"),
    ("short_term_debt", "短期负债", "balance", "CNY"),
    ("long_term_debt", "长期负债", "balance", "CNY"),
    ("current_assets", "流动资产", "balance", "CNY"),
    ("current_liabilities", "流动负债", "balance", "CNY"),
    ("operating_cash_flow", "经营现金流", "cash", "CNY"),
    ("investing_cash_flow", "投资现金流", "cash", "CNY"),
    ("financing_cash_flow", "筹资现金流", "cash", "CNY"),
    ("capital_expenditure", "资本开支", "cash", "CNY"),
]

for metric_id, name, source, unit in _BASE_METRICS:
    _registry.register(FinancialMetricDefinition(
        metric_id=metric_id,
        name=name,
        category="reported",
        formula=f"reported:{source}.{metric_id}",
        unit=unit,
        source_fields=(f"{source}_statement.{metric_id}",),
        calculation_type="reported",
        base_metric=metric_id,
        description=f"标准化报表中的{name}。",
    ))


def _register_growth(metric_id: str, name: str, base: str):
    _registry.register(FinancialMetricDefinition(
        metric_id=metric_id,
        name=name,
        category="growth",
        formula=f"({base}_current - {base}_previous) / abs({base}_previous)",
        unit="%",
        source_fields=(f"income_statement.{base}",),
        calculation_type="growth",
        growth_of=base,
        tolerance=0.001,
        description=f"{name}，同比变化。",
    ))


_register_growth("revenue_growth", "营业收入增长率", "revenue")
_register_growth("net_profit_growth", "净利润增长率", "net_profit")
_register_growth("operating_profit_growth", "营业利润增长率", "operating_profit")
_register_growth("eps_growth", "EPS 增长率", "eps")


def _register_ratio(
    metric_id: str,
    name: str,
    category: str,
    numerator: str,
    denominator: str,
    unit: str,
    source_fields: tuple[str, ...],
    *,
    balance_policy: str = "ending_balance",
    description: str = "",
):
    formula = f"{numerator} / {denominator}"
    if balance_policy == "average_balance":
        formula = f"{numerator} / average({denominator}_current, {denominator}_previous)"
    _registry.register(FinancialMetricDefinition(
        metric_id=metric_id,
        name=name,
        category=category,
        formula=formula,
        unit=unit,
        source_fields=source_fields,
        calculation_type="ratio",
        numerator=numerator,
        denominator=denominator,
        tolerance=0.001,
        description=description or f"{name}。",
        balance_policy=balance_policy,
    ))


_register_ratio("gross_margin", "毛利率", "profitability", "gross_profit", "revenue", "%", ("income_statement.gross_profit", "income_statement.revenue"))
_register_ratio("operating_margin", "营业利润率", "profitability", "operating_profit", "revenue", "%", ("income_statement.operating_profit", "income_statement.revenue"))
_register_ratio("net_margin", "净利率", "profitability", "net_profit", "revenue", "%", ("income_statement.net_profit", "income_statement.revenue"))
_register_ratio("roe", "净资产收益率", "profitability", "net_profit", "total_equity", "%", ("income_statement.net_profit", "balance_sheet.total_equity"), balance_policy="average_balance", description="净资产收益率，使用当期与上期平均股东权益。")
_register_ratio("roa", "总资产收益率", "profitability", "net_profit", "total_assets", "%", ("income_statement.net_profit", "balance_sheet.total_assets"), balance_policy="average_balance", description="总资产收益率，使用当期与上期平均总资产。")
_register_ratio("debt_to_asset", "资产负债率", "solvency", "total_liabilities", "total_assets", "%", ("balance_sheet.total_liabilities", "balance_sheet.total_assets"))
_register_ratio("current_ratio", "流动比率", "solvency", "current_assets", "current_liabilities", "x", ("balance_sheet.current_assets", "balance_sheet.current_liabilities"))
_register_ratio("quick_ratio", "速动比率", "solvency", "cash + accounts_receivable", "current_liabilities", "x", ("balance_sheet.cash", "balance_sheet.accounts_receivable", "balance_sheet.current_liabilities"))
_register_ratio("receivable_turnover", "应收账款周转率", "operating", "revenue", "accounts_receivable", "x", ("income_statement.revenue", "balance_sheet.accounts_receivable"), balance_policy="average_balance", description="应收账款周转率，使用当期与上期平均应收账款。")
_register_ratio("inventory_turnover", "存货周转率", "operating", "cost_of_revenue", "inventory", "x", ("income_statement.cost_of_revenue", "balance_sheet.inventory"), balance_policy="average_balance", description="存货周转率，使用当期与上期平均存货。")
_register_ratio("asset_turnover", "总资产周转率", "operating", "revenue", "total_assets", "x", ("income_statement.revenue", "balance_sheet.total_assets"), balance_policy="average_balance", description="总资产周转率，使用当期与上期平均总资产。")
_register_ratio("ocf_to_net_income", "经营现金流 / 净利润", "cashflow", "operating_cash_flow", "net_profit", "x", ("cash_flow.operating_cash_flow", "income_statement.net_profit"))
_registry.register(FinancialMetricDefinition(
    metric_id="free_cash_flow",
    name="自由现金流",
    category="cashflow",
    formula="operating_cash_flow - capital_expenditure",
    unit="CNY",
    source_fields=("cash_flow.operating_cash_flow", "cash_flow.capital_expenditure"),
    calculation_type="derived",
    numerator="operating_cash_flow",
    denominator="capital_expenditure",
    description="经营现金流减资本开支。",
))


def _statement_value(field: str, income: IncomeStatement, balance: BalanceSheetStatement, cash: CashFlowStatement) -> Optional[float]:
    values = {
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
        "operating_cash_flow": cash.operating_cash_flow,
        "investing_cash_flow": cash.investing_cash_flow,
        "financing_cash_flow": cash.financing_cash_flow,
        "capital_expenditure": cash.capital_expenditure,
    }
    value = values.get(field)
    return None if value is None else float(value)


def _ratio_value(
    numerator: str,
    income: IncomeStatement,
    balance: BalanceSheetStatement,
    cash: CashFlowStatement,
    missing_fields: list[str],
) -> Optional[float]:
    if numerator == "cash + accounts_receivable":
        cash_value = _statement_value("cash", income, balance, cash)
        receivable_value = _statement_value("accounts_receivable", income, balance, cash)
        if cash_value is None:
            missing_fields.append("balance_sheet.cash")
        if receivable_value is None:
            missing_fields.append("balance_sheet.accounts_receivable")
        if cash_value is None or receivable_value is None:
            return None
        return cash_value + receivable_value
    value = _statement_value(numerator, income, balance, cash)
    if value is None:
        missing_fields.append(numerator)
    return value


def _unavailable(
    metric_id: str,
    inputs: dict[str, Optional[float]],
    missing_fields: list[str],
    reason: str,
) -> MetricComputation:
    return MetricComputation(
        metric_id=metric_id,
        status=UNAVAILABLE,
        inputs=inputs,
        missing_fields=list(dict.fromkeys(missing_fields)),
        reason=reason,
    )


def _invalid(
    metric_id: str,
    inputs: dict[str, Optional[float]],
    reason: str,
) -> MetricComputation:
    return MetricComputation(
        metric_id=metric_id,
        status=INVALID,
        inputs=inputs,
        reason=reason,
    )


def compute_metric(
    metric_id: str,
    *,
    income: IncomeStatement,
    balance: BalanceSheetStatement,
    cash_flow: CashFlowStatement,
    previous_income: IncomeStatement | None = None,
    previous_balance: BalanceSheetStatement | None = None,
) -> MetricComputation:
    """Compute a metric with explicit missing/invalid semantics."""
    definition = financial_metric_registry.get(metric_id)
    missing_fields: list[str] = []

    if definition.calculation_type == "reported":
        field = definition.base_metric or metric_id
        value = _statement_value(field, income, balance, cash_flow)
        inputs = {"value": value}
        if value is None:
            missing_fields.append(field)
            return _unavailable(metric_id, inputs, missing_fields, "reported_value_missing")
        if not math.isfinite(value):
            return _invalid(metric_id, inputs, "reported_value_not_finite")
        return MetricComputation(
            metric_id=metric_id,
            status=CALCULATED,
            value=round(value, 6),
            inputs={"value": round(value, 6)},
        )

    if definition.calculation_type == "growth":
        base = definition.growth_of or ""
        current = _statement_value(base, income, balance, cash_flow)
        previous = (
            _statement_value(base, previous_income, balance, cash_flow)
            if previous_income is not None
            else None
        )
        if current is None:
            missing_fields.append(f"current.{base}")
        if previous_income is None:
            missing_fields.append("previous_income")
        if previous_income is not None and previous is None:
            missing_fields.append(f"previous.{base}")
        inputs = {"current": current, "previous": previous}
        if current is None or previous is None:
            return _unavailable(
                metric_id,
                inputs,
                missing_fields,
                "growth_previous_period_missing" if previous_income is None else "growth_value_missing",
            )
        if previous == 0:
            return _invalid(metric_id, inputs, "growth_previous_period_zero")
        if not math.isfinite(current) or not math.isfinite(previous):
            return _invalid(metric_id, inputs, "growth_value_not_finite")
        value = (current - previous) / abs(previous) * 100
        if not math.isfinite(value):
            return _invalid(metric_id, inputs, "growth_value_not_finite")
        return MetricComputation(
            metric_id=metric_id,
            status=CALCULATED,
            value=round(value, 6),
            inputs={key: round(value, 6) for key, value in inputs.items()},
        )

    if definition.metric_id == "free_cash_flow":
        operating_cash_flow = _statement_value("operating_cash_flow", income, balance, cash_flow)
        capital_expenditure = _statement_value("capital_expenditure", income, balance, cash_flow)
        if operating_cash_flow is None:
            missing_fields.append("cash_flow.operating_cash_flow")
        if capital_expenditure is None:
            missing_fields.append("cash_flow.capital_expenditure")
        inputs = {
            "operating_cash_flow": operating_cash_flow,
            "capital_expenditure": capital_expenditure,
        }
        if operating_cash_flow is None or capital_expenditure is None:
            return _unavailable(metric_id, inputs, missing_fields, "free_cash_flow_value_missing")
        if not math.isfinite(operating_cash_flow) or not math.isfinite(capital_expenditure):
            return _invalid(metric_id, inputs, "free_cash_flow_value_not_finite")
        value = operating_cash_flow - capital_expenditure
        if not math.isfinite(value):
            return _invalid(metric_id, inputs, "free_cash_flow_value_not_finite")
        return MetricComputation(
            metric_id=metric_id,
            status=CALCULATED,
            value=round(value, 6),
            inputs={key: round(value, 6) for key, value in inputs.items()},
        )

    if definition.balance_policy == "average_balance":
        numerator = _ratio_value(definition.numerator or "", income, balance, cash_flow, missing_fields)
        denominator_field = definition.denominator or ""
        current_denominator = _statement_value(denominator_field, income, balance, cash_flow)
        previous_denominator = (
            _statement_value(denominator_field, income, previous_balance, cash_flow)
            if previous_balance is not None
            else None
        )
        if numerator is None:
            missing_fields.append(definition.numerator or "numerator")
        if current_denominator is None:
            missing_fields.append(f"current.{denominator_field}")
        if previous_balance is None:
            missing_fields.append("previous_balance")
        if previous_balance is not None and previous_denominator is None:
            missing_fields.append(f"previous.{denominator_field}")
        inputs = {
            "numerator": numerator,
            "current_denominator": current_denominator,
            "previous_denominator": previous_denominator,
            "average_denominator": None,
        }
        if numerator is None or current_denominator is None or previous_denominator is None:
            return _unavailable(
                metric_id,
                inputs,
                missing_fields,
                "average_balance_previous_period_missing" if previous_balance is None else "average_balance_value_missing",
            )
        average_denominator = (current_denominator + previous_denominator) / 2
        inputs["average_denominator"] = average_denominator
        if any(
            value is not None and not math.isfinite(value)
            for value in (numerator, current_denominator, previous_denominator)
        ):
            return _invalid(metric_id, inputs, "average_balance_value_not_finite")
        if average_denominator == 0:
            return _invalid(metric_id, inputs, "average_denominator_zero")
        value = numerator / average_denominator
        if definition.unit == "%":
            value *= 100
        if not math.isfinite(value):
            return _invalid(metric_id, inputs, "average_balance_value_not_finite")
        return MetricComputation(
            metric_id=metric_id,
            status=CALCULATED,
            value=round(value, 6),
            inputs={key: round(value, 6) for key, value in inputs.items()},
        )

    numerator = _ratio_value(definition.numerator or "", income, balance, cash_flow, missing_fields)
    denominator = _ratio_value(definition.denominator or "", income, balance, cash_flow, missing_fields)
    if numerator is None:
        missing_fields.append(definition.numerator or "numerator")
    if denominator is None:
        missing_fields.append(definition.denominator or "denominator")
    inputs = {"numerator": numerator, "denominator": denominator}
    if numerator is None or denominator is None:
        return _unavailable(metric_id, inputs, missing_fields, "ratio_value_missing")
    if denominator == 0:
        return _invalid(metric_id, inputs, "denominator_zero")
    if any(
        value is not None and not math.isfinite(value)
        for value in (numerator, denominator)
    ):
        return _invalid(metric_id, inputs, "ratio_value_not_finite")
    value = numerator / denominator
    if definition.unit == "%":
        value *= 100
    if not math.isfinite(value):
        return _invalid(metric_id, inputs, "ratio_value_not_finite")
    return MetricComputation(
        metric_id=metric_id,
        status=CALCULATED,
        value=round(value, 6),
        inputs={key: round(value, 6) for key, value in inputs.items()},
    )


def calculate_metric(
    metric_id: str,
    *,
    income: IncomeStatement,
    balance: BalanceSheetStatement,
    cash_flow: CashFlowStatement,
    previous_income: IncomeStatement | None = None,
    previous_balance: BalanceSheetStatement | None = None,
) -> tuple[float, dict[str, float]]:
    """Compatibility wrapper for complete-data callers."""
    computation = compute_metric(
        metric_id,
        income=income,
        balance=balance,
        cash_flow=cash_flow,
        previous_income=previous_income,
        previous_balance=previous_balance,
    )
    if computation.status != CALCULATED or computation.value is None:
        raise FinancialMetricComputationError(
            f"{metric_id} {computation.status}: {computation.reason}; "
            f"missing={computation.missing_fields}"
        )
    return computation.value, {
        key: value
        for key, value in computation.inputs.items()
        if value is not None
    }
