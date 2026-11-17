"""Explicit canonical financial period semantics (B10.3).

This module does not guess calendar boundaries from fiscal-period labels.  It
uses the canonical statement's ``period_start``/``period_end`` so natural and
non-natural fiscal calendars behave the same way.  Cumulative-to-quarter and
TTM conversions are explicit, complete, and lineage-producing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable, Mapping, Optional

from .models import (
    FinancialLineItem,
    FinancialStatement,
)

__all__ = [
    "PeriodSemanticsError",
    "PeriodConversionResult",
    "TTMConversionResult",
    "derive_single_quarter_item",
    "derive_ttm_item",
]

_CUMULATIVE_TO_QUARTER: Mapping[str, str] = {
    "Q1": "Q1",
    "Q2": "Q2",
    "Q3": "Q3",
    "Q4": "Q4",
    "H1": "Q2",
    "9M": "Q3",
    "FY": "Q4",
}


class PeriodSemanticsError(ValueError):
    """Raised when a period transformation cannot be performed safely."""


def _line_item(statement: FinancialStatement, concept: str) -> FinancialLineItem:
    matches = [
        item
        for item in statement.line_items
        if item.canonical_concept == concept
    ]
    if len(matches) != 1:
        raise PeriodSemanticsError(
            f"statement must contain exactly one {concept!r} line item"
        )
    return matches[0]


def _assert_compatible(
    statements: Iterable[FinancialStatement],
    item: FinancialLineItem,
) -> None:
    for statement in statements:
        if statement.statement_type == "balance":
            raise PeriodSemanticsError("balance sheet items cannot be period-differenced")
        if statement.period_type != "duration":
            raise PeriodSemanticsError("period conversion requires duration statements")
        if statement.entity_id != statements[0].entity_id:
            raise PeriodSemanticsError("period conversion requires one entity")
        if statement.statement_type != statements[0].statement_type:
            raise PeriodSemanticsError("period conversion requires one statement type")
        if statement.reporting_basis != statements[0].reporting_basis:
            raise PeriodSemanticsError("period conversion requires one reporting basis")
        if statement.currency != statements[0].currency:
            raise PeriodSemanticsError("period conversion requires one currency")
        if item.statistic_kind != "absolute_amount":
            raise PeriodSemanticsError(
                "only absolute_amount facts can be added or differenced"
            )
        if item.unit.currency != statement.currency:
            raise PeriodSemanticsError(
                "line item currency does not match statement currency"
            )


def _assert_contiguous(
    current: FinancialStatement,
    previous: FinancialStatement,
) -> None:
    previous_start = date.fromisoformat(previous.period_start)
    previous_end = date.fromisoformat(previous.period_end)
    current_start = date.fromisoformat(current.period_start)
    current_end = date.fromisoformat(current.period_end)
    if previous_end >= current_end:
        raise PeriodSemanticsError("previous cumulative period must precede current")
    if current_start != previous_start:
        raise PeriodSemanticsError(
            "cumulative quarters must share the same fiscal-period start"
        )
    if previous.fiscal_period == current.fiscal_period and previous.period_end == current.period_end:
        raise PeriodSemanticsError("cumulative periods must not be repeated")


def derive_single_quarter_item(
    current: FinancialStatement,
    previous: Optional[FinancialStatement],
    *,
    concept: str,
) -> FinancialLineItem:
    """Derive one quarter from cumulative canonical statements.

    ``previous`` must be omitted only for the first cumulative quarter of the
    declared fiscal period.  All date boundaries come from canonical statement
    dates, so non-natural fiscal calendars are supported without guessing.
    """
    statements = [current] + ([previous] if previous is not None else [])
    current_item = _line_item(current, concept)
    _assert_compatible(statements, current_item)
    if current.period_type != "duration" or current.period_start is None:
        raise PeriodSemanticsError("cumulative source must be a duration period")

    if previous is not None:
        previous_item = _line_item(previous, concept)
        if previous_item.statistic_kind != current_item.statistic_kind:
            raise PeriodSemanticsError(
                "cumulative statements must use the same statistic kind"
            )
        if previous_item.unit != current_item.unit:
            raise PeriodSemanticsError(
                "cumulative statements must use the same financial unit"
            )
        _assert_contiguous(current, previous)
        output_start = (date.fromisoformat(previous.period_end) + timedelta(days=1)).isoformat()
        output_end = current.period_end
        if current_item.value is None or previous_item.value is None:
            raise PeriodSemanticsError(
                f"cannot derive {concept!r} quarter because a cumulative value is missing"
            )
        value = current_item.value - previous_item.value
        method = "cumulative_difference"
        inputs = {
            "current_value": current_item.value,
            "previous_value": previous_item.value,
        }
        previous_identity = {
            "statement_id": previous.statement_id,
            "fiscal_year": previous.fiscal_year,
            "fiscal_period": previous.fiscal_period,
            "period_start": previous.period_start,
            "period_end": previous.period_end,
            "accession_number": previous.accession_number,
            "source_document_id": previous.source_document_id,
        }
    else:
        if current.fiscal_period != "Q1":
            raise PeriodSemanticsError(
                f"cumulative period {current.fiscal_period!r} requires a previous statement"
            )
        output_start = current.period_start
        output_end = current.period_end
        if current_item.value is None:
            raise PeriodSemanticsError(
                f"cannot derive {concept!r} quarter because the current value is missing"
            )
        value = current_item.value
        method = "identity_first_cumulative_period"
        inputs = {"current_value": current_item.value}
        previous_identity = None

    target_period = _CUMULATIVE_TO_QUARTER.get(current.fiscal_period)
    if target_period is None:
        raise PeriodSemanticsError(
            f"unsupported cumulative fiscal period: {current.fiscal_period!r}"
        )

    source_metadata = {
        **current_item.source_metadata,
        "period_derivation": {
            "method": method,
            "input_statements": [
                {
                    "statement_id": current.statement_id,
                    "fiscal_year": current.fiscal_year,
                    "fiscal_period": current.fiscal_period,
                    "period_start": current.period_start,
                    "period_end": current.period_end,
                    "accession_number": current.accession_number,
                    "source_document_id": current.source_document_id,
                }
            ],
            "previous_statement": previous_identity,
            "input_values": inputs,
            "output_period": {
                "period_type": "duration",
                "period_start": output_start,
                "period_end": output_end,
                "fiscal_year": current.fiscal_year,
                "fiscal_period": target_period,
            },
        },
    }
    return FinancialLineItem(
        canonical_concept=concept,
        original_concept=current_item.original_concept,
        value=value,
        statistic_kind=current_item.statistic_kind,
        comparison_basis=current_item.comparison_basis,
        unit=current_item.unit,
        period_start=output_start,
        period_end=output_end,
        source_metadata=source_metadata,
        normalization_metadata={
            **current_item.normalization_metadata,
            "period_semantics_version": "b10.3",
        },
    )


@dataclass(frozen=True)
class TTMConversionResult:
    output_item: FinancialLineItem
    quarter_items: tuple[FinancialLineItem, ...]


def derive_ttm_item(
    statements: list[FinancialStatement],
    *,
    concept: str,
) -> FinancialTTMResult:
    """Derive a trailing-twelve-month item from four complete cumulative quarters."""
    if len(statements) != 4:
        raise PeriodSemanticsError(
            "TTM requires exactly four cumulative quarters; incomplete data is rejected"
        )
    ordered = sorted(
        statements,
        key=lambda statement: (statement.period_start, statement.period_end),
    )
    _assert_compatible(ordered, _line_item(ordered[0], concept))
    for left, right in zip(ordered, ordered[1:]):
        _assert_contiguous(right, left)
        if left.fiscal_period == right.fiscal_period:
            raise PeriodSemanticsError("cumulative quarters must not be repeated")

    quarter_items = []
    for index, statement in enumerate(ordered):
        quarter_items.append(
            derive_single_quarter_item(
                statement,
                None if index == 0 else ordered[index - 1],
                concept=concept,
            )
        )
    if any(item.value is None for item in quarter_items):
        raise PeriodSemanticsError("TTM cannot be derived from missing quarter values")
    total = sum((item.value for item in quarter_items if item.value is not None), Decimal("0"))
    output_start = ordered[0].period_start
    output_end = ordered[-1].period_end
    base_item = quarter_items[-1]
    source_metadata = {
        **base_item.source_metadata,
        "period_derivation": {
            "method": "sum_complete_quarters",
            "ttm_period": {
                "period_type": "duration",
                "period_start": output_start,
                "period_end": output_end,
                "fiscal_year": ordered[-1].fiscal_year,
                "fiscal_period": "TTM",
            },
            "input_statements": [
                {
                    "statement_id": statement.statement_id,
                    "fiscal_year": statement.fiscal_year,
                    "fiscal_period": statement.fiscal_period,
                    "period_start": statement.period_start,
                    "period_end": statement.period_end,
                    "accession_number": statement.accession_number,
                    "source_document_id": statement.source_document_id,
                }
                for statement in ordered
            ],
            "quarter_values": [item.value for item in quarter_items],
        },
    }
    output_item = FinancialLineItem(
        canonical_concept=concept,
        original_concept=base_item.original_concept,
        value=total,
        statistic_kind=base_item.statistic_kind,
        comparison_basis=base_item.comparison_basis,
        unit=base_item.unit,
        period_start=output_start,
        period_end=output_end,
        source_metadata=source_metadata,
        normalization_metadata={
            **base_item.normalization_metadata,
            "period_semantics_version": "b10.3",
        },
    )
    return TTMConversionResult(output_item=output_item, quarter_items=tuple(quarter_items))


# Convenience alias for callers that prefer the descriptive name.
FinancialTTMResult = TTMConversionResult
