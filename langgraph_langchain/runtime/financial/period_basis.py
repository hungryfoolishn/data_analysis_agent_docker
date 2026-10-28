"""Explicit period-basis conversions for financial statements."""

from __future__ import annotations

from typing import Iterable

from langgraph_langchain.runtime.financial.models import (
    CashFlowStatement,
    IncomeStatement,
)
from .period import PeriodNormalizer, PeriodType


_ADDITIVE_INCOME_FIELDS = (
    "revenue",
    "cost_of_revenue",
    "gross_profit",
    "operating_profit",
    "net_profit",
    "net_profit_attributable",
)
_ADDITIVE_CASH_FLOW_FIELDS = (
    "operating_cash_flow",
    "investing_cash_flow",
    "financing_cash_flow",
    "capital_expenditure",
    "free_cash_flow",
)


def is_first_cumulative_period(period: str, *, normalizer=None) -> bool:
    """Return whether a cumulative fiscal period starts its reporting year."""
    parsed = (normalizer or PeriodNormalizer()).parse(period)
    return parsed.period_type in {PeriodType.FY, PeriodType.H1, PeriodType.Q1}


def _difference(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return current - previous


def _updated_values(
    current_values: dict[str, float | None],
    previous_values: dict[str, float | None],
    *,
    is_first_period: bool,
    fields: Iterable[str],
) -> dict[str, float | None]:
    updated: dict[str, float | None] = dict(current_values)
    for field in fields:
        if is_first_period:
            updated[field] = current_values.get(field)
        else:
            updated[field] = _difference(
                current_values.get(field),
                previous_values.get(field),
            )
    return updated


def derive_single_period_income(
    current: IncomeStatement,
    previous_cumulative: IncomeStatement | None,
    *,
    is_first_period: bool,
) -> IncomeStatement:
    """Derive a single-period income statement from cumulative statements.

    EPS is intentionally not derived by subtracting cumulative EPS because the
    weighted share counts can differ between periods. EPS growth must be
    treated as unavailable for non-first cumulative periods.
    """
    current_values = current.model_dump()
    previous_values = (
        previous_cumulative.model_dump() if previous_cumulative is not None else {}
    )
    updated = _updated_values(
        current_values,
        previous_values,
        is_first_period=is_first_period,
        fields=_ADDITIVE_INCOME_FIELDS,
    )
    if not is_first_period:
        updated["eps"] = None
    return current.model_copy(update=updated)


def derive_single_period_cash_flow(
    current: CashFlowStatement,
    previous_cumulative: CashFlowStatement | None,
    *,
    is_first_period: bool,
) -> CashFlowStatement:
    """Derive a single-period cash-flow statement from cumulative statements."""
    current_values = current.model_dump()
    previous_values = (
        previous_cumulative.model_dump() if previous_cumulative is not None else {}
    )
    updated = _updated_values(
        current_values,
        previous_values,
        is_first_period=is_first_period,
        fields=_ADDITIVE_CASH_FLOW_FIELDS,
    )
    return current.model_copy(update=updated)


def statement_source_ids(*statements: object | None) -> list[str]:
    """Collect non-empty source IDs while preserving first-seen order."""
    source_ids: list[str] = []
    for statement in statements:
        if statement is None:
            continue
        source_id = getattr(statement, "source_id", None)
        if source_id:
            source_ids.append(source_id)
    return list(dict.fromkeys(source_ids))
