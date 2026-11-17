"""Explicit normalization for canonical financial facts.

This module is the single authority for converting a canonical financial
value from its declared source representation into an explicitly requested
representation.  It deliberately does not infer semantics, calculate derived
financial metrics, or perform currency conversion without an explicit rate.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Optional

from .models import (
    FinancialLineItem,
    FinancialStatement,
    FinancialStatisticKind,
    FinancialUnit,
)

FINANCIAL_NORMALIZATION_VERSION = "b10.2.3"


class FinancialNormalizationError(ValueError):
    """Raised when a financial transformation cannot be made safely."""


@dataclass(frozen=True)
class FinancialCurrencyRate:
    """An explicitly supplied currency conversion rate."""

    source_currency: str
    target_currency: str
    rate: Decimal
    rate_date: str
    rate_source: str
    direction: str
    rate_version: str
    conversion_method: str = "multiply_by_explicit_rate"

    def __post_init__(self) -> None:
        for code, field_name in (
            (self.source_currency, "source_currency"),
            (self.target_currency, "target_currency"),
        ):
            if len(code) != 3 or not code.isalpha() or not code.isupper():
                raise FinancialNormalizationError(
                    f"{field_name} must be ISO 4217"
                )
        if not self.rate.is_finite() or self.rate <= 0:
            raise FinancialNormalizationError(
                "currency rate must be finite and positive"
            )
        if self.direction not in {"source_to_target", "target_to_source"}:
            raise FinancialNormalizationError(
                "currency rate direction must be explicit"
            )


@dataclass(frozen=True)
class FinancialLineNormalizationResult:
    """A line item, its output, and an auditable transformation record."""

    input_line_item: FinancialLineItem
    output_line_item: FinancialLineItem
    lineage: dict[str, Any]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class FinancialStatementNormalizationResult:
    """All line-item normalization results for one immutable statement input."""

    input_statement: FinancialStatement
    output_statement: FinancialStatement
    item_results: tuple[FinancialLineNormalizationResult, ...]
    warnings: tuple[str, ...]
    lineage: dict[str, Any]


def _decimal(value: Optional[Decimal], field_name: str) -> Optional[Decimal]:
    if value is None:
        return None
    if not isinstance(value, Decimal):
        raise FinancialNormalizationError(f"{field_name} must be Decimal")
    if not value.is_finite():
        raise FinancialNormalizationError(f"{field_name} must be finite")
    return value


def _currency_code(value: Optional[str], field_name: str) -> Optional[str]:
    if value is None:
        return None
    if len(value) != 3 or not value.isalpha() or not value.isupper():
        raise FinancialNormalizationError(f"{field_name} must be ISO 4217")
    return value


def _target_rate(
    item: FinancialLineItem,
    target_currency: Optional[str],
    rate: Optional[FinancialCurrencyRate],
) -> tuple[Optional[str], Optional[Decimal], Optional[dict[str, Any]], tuple[str, ...]]:
    source_currency = _currency_code(item.unit.currency, "input currency")
    target = _currency_code(target_currency, "target currency")
    if target is None:
        return source_currency, None, None, ()
    if source_currency is None:
        raise FinancialNormalizationError(
            f"cannot convert {item.canonical_concept!r}: source currency is unknown"
        )
    if source_currency == target:
        return target, None, None, ()
    if rate is None:
        raise FinancialNormalizationError(
            f"cannot convert {source_currency} to {target}: an explicit "
            "FinancialCurrencyRate is required"
        )
    if rate.source_currency != source_currency or rate.target_currency != target:
        raise FinancialNormalizationError(
            "currency rate does not match the requested source/target currencies"
        )
    if rate.direction == "target_to_source":
        conversion_rate = Decimal("1") / rate.rate
        conversion_method = "divide_by_explicit_rate"
    else:
        conversion_rate = rate.rate
        conversion_method = rate.conversion_method
    rate_metadata = {
        "source_currency": rate.source_currency,
        "target_currency": rate.target_currency,
        "rate": rate.rate,
        "applied_rate": conversion_rate,
        "rate_date": rate.rate_date,
        "rate_source": rate.rate_source,
        "direction": rate.direction,
        "rate_version": rate.rate_version,
        "conversion_method": conversion_method,
    }
    return target, conversion_rate, rate_metadata, ()


def _apply_semantics(
    item: FinancialLineItem,
    target_kind: Optional[FinancialStatisticKind],
) -> tuple[FinancialStatisticKind, Optional[Decimal], str, Optional[str]]:
    source_kind = item.statistic_kind
    if target_kind is None or target_kind == source_kind:
        return source_kind, None, ("identity" if target_kind is None else "same_kind"), None
    if source_kind == "unknown":
        raise FinancialNormalizationError(
            f"cannot normalize unknown statistic kind for {item.canonical_concept!r}"
        )
    if source_kind == "yoy_index" and target_kind == "growth_rate":
        value = _decimal(item.value, "value")
        if value is None:
            return target_kind, None, "explicit_yoy_index_minus_base_100", None
        return target_kind, value - Decimal("100"), (
            "explicit_yoy_index_minus_base_100"
        ), "yoy"
    if source_kind in {"growth_rate", "percent_ratio", "times_ratio"} and (
        target_kind == source_kind
    ):
        return target_kind, None, "same_kind", None
    raise FinancialNormalizationError(
        f"semantic conversion {source_kind!r} -> {target_kind!r} is not defined"
    )


def normalize_financial_line_item(
    item: FinancialLineItem,
    *,
    semantic_targets: Optional[Mapping[str, FinancialStatisticKind]] = None,
    target_currency: Optional[str] = None,
    currency_rate: Optional[FinancialCurrencyRate] = None,
    source_data_version: Optional[str] = None,
) -> FinancialLineNormalizationResult:
    """Normalize one item through the only explicit transformation exit.

    ``semantic_targets`` is keyed by ``canonical_concept`` so a caller must
    name the concept and its requested semantics.  No unit string or concept
    name is interpreted to guess a statistic kind.
    """
    warnings: list[str] = []
    input_value = _decimal(item.value, "value")
    output_value = input_value
    methods: list[str] = []
    concept_targets = dict(semantic_targets or {})
    target_kind, semantic_value, semantic_method, comparison_basis = _apply_semantics(
        item, concept_targets.get(item.canonical_concept)
    )
    if semantic_value is not None:
        output_value = semantic_value
    if semantic_method != "identity":
        methods.append(semantic_method)
    if comparison_basis:
        warnings.append(f"comparison basis set to {comparison_basis!r}")

    scale = item.unit.scale
    if scale != Decimal("1"):
        if target_kind not in {"absolute_amount", "per_share"}:
            raise FinancialNormalizationError(
                f"non-unit scale {scale} is only meaningful for monetary facts"
            )
        if output_value is not None:
            output_value *= scale
        methods.append("multiply_by_declared_source_scale")

    output_currency, currency_rate_value, currency_metadata, currency_warnings = (
        _target_rate(item, target_currency, currency_rate)
    )
    warnings.extend(currency_warnings)
    if currency_rate_value is not None:
        if output_value is not None:
            output_value *= currency_rate_value
        methods.append(currency_metadata["conversion_method"])

    output_numeric_unit = item.unit.numeric_unit
    if target_currency is not None and target_kind in {"absolute_amount", "per_share"}:
        output_numeric_unit = output_currency
    output_unit = item.unit.model_copy(
        update={
            "statistic_kind": target_kind,
            "numeric_unit": output_numeric_unit,
            "currency": output_currency,
            "scale": Decimal("1"),
        }
    )
    update: dict[str, Any] = {"value": output_value, "unit": output_unit}
    if target_kind != item.statistic_kind:
        update["statistic_kind"] = target_kind
    if comparison_basis:
        update["comparison_basis"] = comparison_basis
    if target_currency is not None and item.unit.currency != output_currency:
        update["source_metadata"] = {
            **item.source_metadata,
            "currency_conversion": currency_metadata,
        }
    output_item = item.model_copy(update=update, deep=True)
    output_item.normalization_metadata.update(
        {
            "normalization_version": FINANCIAL_NORMALIZATION_VERSION,
            "lineage": {
                "input_value": input_value,
                "input_statistic_kind": item.statistic_kind,
                "input_unit": item.unit.model_dump(mode="json"),
                "input_currency": item.unit.currency,
                "output_value": output_value,
                "output_statistic_kind": target_kind,
                "output_unit": output_unit.model_dump(mode="json"),
                "output_currency": output_currency,
                "conversion_method": methods or ["identity"],
                "normalization_version": FINANCIAL_NORMALIZATION_VERSION,
                "warnings": warnings,
                "source_metadata": dict(item.source_metadata),
                "input_data_version": source_data_version
                or item.source_metadata.get("data_version"),
            },
        }
    )
    if item.statistic_kind == "unknown":
        warnings.append(
            "statistic_kind remains 'unknown'; semantic conversion was rejected"
        )
    return FinancialLineNormalizationResult(
        input_line_item=item,
        output_line_item=output_item,
        lineage=output_item.normalization_metadata["lineage"],
        warnings=tuple(warnings),
    )


def normalize_financial_statement(
    statement: FinancialStatement,
    *,
    semantic_targets: Optional[Mapping[str, FinancialStatisticKind]] = None,
    target_currency: Optional[str] = None,
    currency_rate: Optional[FinancialCurrencyRate] = None,
) -> FinancialStatementNormalizationResult:
    """Normalize every line item while retaining statement filing identity."""
    item_results = tuple(
        normalize_financial_line_item(
            item,
            semantic_targets=semantic_targets,
            target_currency=target_currency,
            currency_rate=currency_rate,
            source_data_version=statement.version or None,
        )
        for item in statement.line_items
    )
    filing_context = {
        "statement_id": statement.statement_id,
        "filing_date": statement.filing_date,
        "accession_number": statement.accession_number,
        "source_document_id": statement.source_document_id,
        "statement_version": statement.version,
    }
    output_items = tuple(result.output_line_item for result in item_results)
    output_statement = statement.model_copy(
        update={"line_items": output_items, "metadata": {**statement.metadata,
            "normalization_version": FINANCIAL_NORMALIZATION_VERSION}}
    )
    warnings = tuple(
        warning
        for result in item_results
        for warning in result.warnings
    )
    return FinancialStatementNormalizationResult(
        input_statement=statement,
        output_statement=output_statement,
        item_results=item_results,
        warnings=warnings,
        lineage={"filing": filing_context},
    )
