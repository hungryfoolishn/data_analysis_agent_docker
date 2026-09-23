"""Deterministic financial metric definitions and calculations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import BalanceSheetStatement, CashFlowStatement, IncomeStatement


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


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _growth(current: float, previous: float) -> float:
    return (current - previous) / abs(previous) if previous else 0.0


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
_register_ratio(
    "roe", "净资产收益率", "profitability", "net_profit", "total_equity", "%",
    ("income_statement.net_profit", "balance_sheet.total_equity"),
    balance_policy="average_balance",
    description="净资产收益率，使用当期与上期平均股东权益。",
)
_register_ratio(
    "roa", "总资产收益率", "profitability", "net_profit", "total_assets", "%",
    ("income_statement.net_profit", "balance_sheet.total_assets"),
    balance_policy="average_balance",
    description="总资产收益率，使用当期与上期平均总资产。",
)
_register_ratio("debt_to_asset", "资产负债率", "solvency", "total_liabilities", "total_assets", "%", ("balance_sheet.total_liabilities", "balance_sheet.total_assets"))
_register_ratio("current_ratio", "流动比率", "solvency", "current_assets", "current_liabilities", "x", ("balance_sheet.current_assets", "balance_sheet.current_liabilities"))
_register_ratio("quick_ratio", "速动比率", "solvency", "cash + accounts_receivable", "current_liabilities", "x", ("balance_sheet.cash", "balance_sheet.accounts_receivable", "balance_sheet.current_liabilities"))
_register_ratio(
    "receivable_turnover", "应收账款周转率", "operating", "revenue", "accounts_receivable", "x",
    ("income_statement.revenue", "balance_sheet.accounts_receivable"),
    balance_policy="average_balance",
    description="应收账款周转率，使用当期与上期平均应收账款。",
)
_register_ratio(
    "inventory_turnover", "存货周转率", "operating", "cost_of_revenue", "inventory", "x",
    ("income_statement.cost_of_revenue", "balance_sheet.inventory"),
    balance_policy="average_balance",
    description="存货周转率，使用当期与上期平均存货。",
)
_register_ratio(
    "asset_turnover", "总资产周转率", "operating", "revenue", "total_assets", "x",
    ("income_statement.revenue", "balance_sheet.total_assets"),
    balance_policy="average_balance",
    description="总资产周转率，使用当期与上期平均总资产。",
)
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


def _statement_value(field: str, income: IncomeStatement, balance: BalanceSheetStatement, cash: CashFlowStatement) -> float:
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
    return float(values.get(field, 0.0))


def _ratio_value(numerator: str, income: IncomeStatement, balance: BalanceSheetStatement, cash: CashFlowStatement) -> float:
    if numerator == "cash + accounts_receivable":
        return balance.cash + balance.accounts_receivable
    return _statement_value(numerator, income, balance, cash)


def calculate_metric(
    metric_id: str,
    *,
    income: IncomeStatement,
    balance: BalanceSheetStatement,
    cash_flow: CashFlowStatement,
    previous_income: IncomeStatement | None = None,
    previous_balance: BalanceSheetStatement | None = None,
) -> tuple[float, dict[str, float]]:
    definition = financial_metric_registry.get(metric_id)
    if definition.calculation_type == "reported":
        value = _statement_value(definition.base_metric or metric_id, income, balance, cash_flow)
        inputs = {"value": value}
    elif definition.calculation_type == "growth":
        base = definition.growth_of or ""
        current = _statement_value(base, income, balance, cash_flow)
        previous = _statement_value(base, previous_income, balance, cash_flow) if previous_income else 0.0
        value = _growth(current, previous)
        inputs = {"current": current, "previous": previous}
    elif definition.metric_id == "free_cash_flow":
        value = cash_flow.operating_cash_flow - cash_flow.capital_expenditure
        inputs = {
            "operating_cash_flow": cash_flow.operating_cash_flow,
            "capital_expenditure": cash_flow.capital_expenditure,
        }
    elif definition.balance_policy == "average_balance":
        numerator = _ratio_value(definition.numerator or "", income, balance, cash_flow)
        denominator_field = definition.denominator or ""
        current_denominator = _statement_value(denominator_field, income, balance, cash_flow)
        previous_denominator = (
            _statement_value(denominator_field, income, previous_balance, cash_flow)
            if previous_balance is not None
            else current_denominator
        )
        average_denominator = (current_denominator + previous_denominator) / 2
        value = _ratio(numerator, average_denominator)
        inputs = {
            "numerator": numerator,
            "current_denominator": current_denominator,
            "previous_denominator": previous_denominator,
            "average_denominator": average_denominator,
        }
    else:
        numerator = _ratio_value(definition.numerator or "", income, balance, cash_flow)
        denominator = _ratio_value(definition.denominator or "", income, balance, cash_flow)
        value = _ratio(numerator, denominator)
        inputs = {"numerator": numerator, "denominator": denominator}

    if definition.unit == "%":
        value *= 100.0
    return round(value, 6), inputs
