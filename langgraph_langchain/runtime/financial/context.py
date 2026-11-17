"""Canonical Financial context for B10.6."""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Optional

from .catalog import (
    CALCULATED,
    FinancialIndicatorDefinition,
    FinancialIndicatorResult,
    financial_indicator_registry,
)
from .intent import FinancialIntent
from .models import FinancialLineItem, FinancialStatement

__all__ = ["FinancialContext", "FinancialContextError"]


class FinancialContextError(ValueError):
    """Raised when a canonical context cannot resolve a Financial indicator."""


class FinancialContext:
    def __init__(
        self,
        *,
        intent: FinancialIntent,
        statements: Iterable[FinancialStatement],
        input_asset_ids: tuple[str, ...] = (),
    ) -> None:
        self.intent = intent
        self.statements = tuple(statements)
        self.input_asset_ids = tuple(input_asset_ids)

    def _statement_for(
        self,
        *,
        statement_type: str,
        fiscal_year: int,
        fiscal_period: str,
    ) -> FinancialStatement:
        matches = [
            statement
            for statement in self.statements
            if statement.entity_id == self.intent.entity_id
            and statement.statement_type == statement_type
            and statement.fiscal_year == fiscal_year
            and statement.fiscal_period == fiscal_period
            and statement.reporting_basis == self.intent.reporting_basis
            and statement.currency == self.intent.currency
        ]
        if len(matches) != 1:
            raise FinancialContextError(
                "expected exactly one canonical statement for "
                f"{statement_type}/{fiscal_year}/{fiscal_period}; found {len(matches)}"
            )
        return matches[0]

    def _item_for(
        self,
        statement: FinancialStatement,
        definition: FinancialIndicatorDefinition,
    ) -> FinancialLineItem:
        matches = [
            item
            for item in statement.line_items
            if item.canonical_concept == definition.canonical_concept
        ]
        if len(matches) != 1:
            raise FinancialContextError(
                f"expected exactly one {definition.canonical_concept!r} line item"
            )
        return matches[0]

    def _resolve_raw(
        self,
        definition: FinancialIndicatorDefinition,
        *,
        fiscal_year: int,
    ) -> FinancialIndicatorResult:
        statement = self._statement_for(
            statement_type=definition.statement_type or self.intent.statement_type,
            fiscal_year=fiscal_year,
            fiscal_period=self.intent.fiscal_period,
        )
        item = self._item_for(statement, definition)
        return self._compute(
            definition,
            input_values={"value": item.value},
            lineage={
                "statement_id": statement.statement_id,
                "accession_number": statement.accession_number,
                "source_document_id": statement.source_document_id,
                "item_id": item.item_id,
            },
        )

    def _compute(
        self,
        definition: FinancialIndicatorDefinition,
        *,
        input_values: dict[str, object],
        lineage: dict[str, object],
    ) -> FinancialIndicatorResult:
        from .catalog import compute_financial_indicator

        return compute_financial_indicator(
            definition,
            input_values=input_values,
            lineage=lineage,
        )

    def resolve(self, metric_id: Optional[str] = None) -> FinancialIndicatorResult:
        """Resolve one metric through the typed indicator catalog."""
        metric_id = metric_id or self.intent.metric_id
        definition = financial_indicator_registry.get(
            metric_id,
            market_scope=self.intent.market_scope,
        )
        if definition.metric_kind == "raw":
            return self._resolve_raw(definition, fiscal_year=self.intent.fiscal_year)
        if definition.metric_kind == "difference":
            minuend = self.resolve(definition.minuend_metric or "")
            subtrahend = self.resolve(definition.subtrahend_metric or "")
            return self._compute(
                definition,
                input_values={
                    definition.minuend_metric or "minuend": minuend.value,
                    definition.subtrahend_metric or "subtrahend": subtrahend.value,
                },
                lineage={
                    "minuend": minuend.lineage,
                    "subtrahend": subtrahend.lineage,
                },
            )
        if definition.metric_kind == "ratio":
            numerator = self.resolve(definition.numerator_metric or "")
            denominator = self.resolve(definition.denominator_metric or "")
            return self._compute(
                definition,
                input_values={
                    definition.numerator_metric or "numerator": numerator.value,
                    definition.denominator_metric or "denominator": denominator.value,
                },
                lineage={
                    "numerator": numerator.lineage,
                    "denominator": denominator.lineage,
                },
            )
        current = self._resolve_raw(
            financial_indicator_registry.get(
                definition.base_metric or "",
                market_scope=self.intent.market_scope,
            ),
            fiscal_year=self.intent.fiscal_year,
        )
        previous = self._resolve_raw(
            financial_indicator_registry.get(
                definition.base_metric or "",
                market_scope=self.intent.market_scope,
            ),
            fiscal_year=self.intent.fiscal_year - 1,
        )
        return self._compute(
            definition,
            input_values={"current": current.value, "previous": previous.value},
            lineage={"current": current.lineage, "previous": previous.lineage},
        )
