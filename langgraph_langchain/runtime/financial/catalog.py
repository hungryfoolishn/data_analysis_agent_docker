"""Typed Financial indicator catalog (B10.4).

The catalog is intentionally separate from the legacy V9 metric wrapper.  It
works on canonical Decimal values and has no fuzzy concept matching.  A metric
is identified by ``metric_id + market_scope``; market-specific definitions must
not silently replace an ``any`` definition.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping, Optional

from .models import FinancialLineItem, FinancialStatement, FinancialStatisticKind

__all__ = [
    "FinancialIndicatorDefinition",
    "FinancialIndicatorRegistry",
    "FinancialIndicatorResult",
    "financial_indicator_registry",
    "compute_financial_indicator",
]

CALCULATED = "calculated"
UNAVAILABLE = "unavailable"
INVALID = "invalid"
MARKET_SCOPES = {"any", "us", "cn"}


class FinancialIndicatorError(ValueError):
    """Raised when an indicator definition or computation is invalid."""


@dataclass(frozen=True)
class FinancialIndicatorDefinition:
    metric_id: str
    display_name: str
    market_scope: str
    metric_kind: str  # raw | ratio | growth | difference
    statistic_kind: FinancialStatisticKind
    unit: str
    statement_type: Optional[str] = None
    canonical_concept: Optional[str] = None
    numerator_metric: Optional[str] = None
    denominator_metric: Optional[str] = None
    base_metric: Optional[str] = None
    minuend_metric: Optional[str] = None
    subtrahend_metric: Optional[str] = None
    period_requirement: str = "single_period"
    dependencies: tuple[str, ...] = ()
    version: str = "b10.4"
    description: str = ""

    @property
    def identity_key(self) -> tuple[str, str]:
        return (self.metric_id, self.market_scope)

    def validate(self) -> None:
        if not self.metric_id or self.metric_id != self.metric_id.strip():
            raise FinancialIndicatorError("metric_id must be a non-empty token")
        if self.market_scope not in MARKET_SCOPES:
            raise FinancialIndicatorError(
                f"market_scope must be one of {sorted(MARKET_SCOPES)}"
            )
        if self.metric_kind not in {"raw", "ratio", "growth", "difference"}:
            raise FinancialIndicatorError("metric_kind must be raw, ratio, or growth")
        if self.period_requirement not in {
            "single_period",
            "cumulative",
            "four_quarters",
        }:
            raise FinancialIndicatorError(
                "period_requirement must be single_period, cumulative, or four_quarters"
            )
        if self.metric_kind == "raw":
            if self.canonical_concept is None or self.statement_type is None:
                raise FinancialIndicatorError(
                    "raw metrics require canonical_concept and statement_type"
                )
        if self.metric_kind == "ratio":
            if not self.numerator_metric or not self.denominator_metric:
                raise FinancialIndicatorError("ratio metrics require numerator and denominator")
            dependencies = set(self.dependencies)
            if self.numerator_metric not in dependencies or self.denominator_metric not in dependencies:
                raise FinancialIndicatorError(
                    "ratio dependencies must explicitly contain numerator and denominator"
                )
        if self.metric_kind == "difference":
            if not self.minuend_metric or not self.subtrahend_metric:
                raise FinancialIndicatorError(
                    "difference metrics require minuend and subtrahend metrics"
                )
            dependencies = set(self.dependencies)
            if self.minuend_metric not in dependencies or self.subtrahend_metric not in dependencies:
                raise FinancialIndicatorError(
                    "difference dependencies must explicitly contain minuend and subtrahend"
                )
        if self.metric_kind == "growth":
            if not self.base_metric:
                raise FinancialIndicatorError("growth metrics require base_metric")
            if self.statistic_kind != "growth_rate":
                raise FinancialIndicatorError("growth metrics must use growth_rate")
        if self.metric_kind == "raw" and self.statistic_kind not in {
            "absolute_amount",
            "per_share",
        }:
            raise FinancialIndicatorError(
                "raw financial metrics must be absolute_amount or per_share"
            )


@dataclass(frozen=True)
class FinancialIndicatorResult:
    metric_id: str
    market_scope: str
    status: str
    statistic_kind: FinancialStatisticKind
    value: Optional[Decimal] = None
    unit: str = ""
    inputs: Mapping[str, Optional[Decimal]] = None  # type: ignore[assignment]
    lineage: Mapping[str, object] = None  # type: ignore[assignment]
    reason: Optional[str] = None


class FinancialIndicatorRegistry:
    def __init__(self) -> None:
        self._definitions: dict[tuple[str, str], FinancialIndicatorDefinition] = {}

    def register(self, definition: FinancialIndicatorDefinition) -> FinancialIndicatorDefinition:
        definition.validate()
        key = definition.identity_key
        existing = self._definitions.get(key)
        if existing is not None:
            if existing != definition:
                raise FinancialIndicatorError(
                    f"indicator conflict for {key}; same identity must be idempotent"
                )
            return existing
        self._definitions[key] = definition
        return definition

    def get(
        self,
        metric_id: str,
        *,
        market_scope: str = "any",
    ) -> FinancialIndicatorDefinition:
        market_key = (metric_id, market_scope)
        definition = self._definitions.get(market_key)
        if definition is not None:
            return definition
        if market_scope != "any":
            definition = self._definitions.get((metric_id, "any"))
        if definition is None:
            raise FinancialIndicatorError(f"indicator not found: {metric_id}")
        return definition

    def list_metrics(self) -> list[FinancialIndicatorDefinition]:
        return sorted(self._definitions.values(), key=lambda item: item.identity_key)

    def __len__(self) -> int:
        return len(self._definitions)


financial_indicator_registry = FinancialIndicatorRegistry()


def _decimal_value(value: object, field_name: str) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, str):
        try:
            result = Decimal(value)
        except InvalidOperation as exc:
            raise FinancialIndicatorError(f"{field_name} is not a Decimal") from exc
    else:
        raise FinancialIndicatorError(f"{field_name} must be Decimal or decimal string")
    if not result.is_finite():
        raise FinancialIndicatorError(f"{field_name} must be finite")
    return result


def compute_financial_indicator(
    definition: FinancialIndicatorDefinition,
    *,
    input_values: Mapping[str, object],
    lineage: Optional[Mapping[str, object]] = None,
) -> FinancialIndicatorResult:
    """Compute one indicator from explicitly resolved Decimal inputs."""
    resolved: dict[str, Optional[Decimal]] = {
        key: _decimal_value(value, key) for key, value in input_values.items()
    }
    if definition.metric_kind == "raw":
        value = resolved.get("value")
        inputs = {"value": value}
        if value is None:
            return FinancialIndicatorResult(
                metric_id=definition.metric_id,
                market_scope=definition.market_scope,
                status=UNAVAILABLE,
                statistic_kind=definition.statistic_kind,
                unit=definition.unit,
                inputs=inputs,
                lineage=dict(lineage or {}),
                reason="raw_value_missing",
            )
        return FinancialIndicatorResult(
            metric_id=definition.metric_id,
            market_scope=definition.market_scope,
            status=CALCULATED,
            statistic_kind=definition.statistic_kind,
            value=value,
            unit=definition.unit,
            inputs=inputs,
            lineage=dict(lineage or {}),
        )

    if definition.metric_kind == "ratio":
        numerator = resolved.get(definition.numerator_metric or "")
        denominator = resolved.get(definition.denominator_metric or "")
        inputs = {
            "numerator_metric": definition.numerator_metric,
            "denominator_metric": definition.denominator_metric,
            "numerator": numerator,
            "denominator": denominator,
        }
        if numerator is None or denominator is None:
            return FinancialIndicatorResult(
                metric_id=definition.metric_id,
                market_scope=definition.market_scope,
                status=UNAVAILABLE,
                statistic_kind=definition.statistic_kind,
                unit=definition.unit,
                inputs=inputs,
                lineage=dict(lineage or {}),
                reason="ratio_input_missing",
            )
        if denominator == 0:
            return FinancialIndicatorResult(
                metric_id=definition.metric_id,
                market_scope=definition.market_scope,
                status=INVALID,
                statistic_kind=definition.statistic_kind,
                unit=definition.unit,
                inputs=inputs,
                lineage=dict(lineage or {}),
                reason="denominator_zero",
            )
        value = numerator / denominator
        if definition.statistic_kind == "percent_ratio":
            value *= Decimal("100")
        return FinancialIndicatorResult(
            metric_id=definition.metric_id,
            market_scope=definition.market_scope,
            status=CALCULATED,
            statistic_kind=definition.statistic_kind,
            value=value,
            unit=definition.unit,
            inputs=inputs,
            lineage=dict(lineage or {}),
        )

    if definition.metric_kind == "difference":
        minuend = resolved.get(definition.minuend_metric or "")
        subtrahend = resolved.get(definition.subtrahend_metric or "")
        inputs = {
            "minuend_metric": definition.minuend_metric,
            "subtrahend_metric": definition.subtrahend_metric,
            "minuend": minuend,
            "subtrahend": subtrahend,
        }
        if minuend is None or subtrahend is None:
            return FinancialIndicatorResult(
                metric_id=definition.metric_id,
                market_scope=definition.market_scope,
                status=UNAVAILABLE,
                statistic_kind=definition.statistic_kind,
                unit=definition.unit,
                inputs=inputs,
                lineage=dict(lineage or {}),
                reason="difference_input_missing",
            )
        return FinancialIndicatorResult(
            metric_id=definition.metric_id,
            market_scope=definition.market_scope,
            status=CALCULATED,
            statistic_kind=definition.statistic_kind,
            value=minuend - subtrahend,
            unit=definition.unit,
            inputs=inputs,
            lineage=dict(lineage or {}),
        )

    base = resolved.get("current")
    comparison = resolved.get("previous")
    inputs = {"current": base, "previous": comparison, "base_metric": definition.base_metric}
    if base is None or comparison is None:
        return FinancialIndicatorResult(
            metric_id=definition.metric_id,
            market_scope=definition.market_scope,
            status=UNAVAILABLE,
            statistic_kind=definition.statistic_kind,
            unit=definition.unit,
            inputs=inputs,
            lineage=dict(lineage or {}),
            reason="growth_baseline_missing",
        )
    if comparison == 0:
        return FinancialIndicatorResult(
            metric_id=definition.metric_id,
            market_scope=definition.market_scope,
            status=INVALID,
            statistic_kind=definition.statistic_kind,
            unit=definition.unit,
            inputs=inputs,
            lineage=dict(lineage or {}),
            reason="growth_baseline_zero",
        )
    value = (base - comparison) / abs(comparison)
    if definition.statistic_kind == "growth_rate":
        value *= Decimal("100")
    return FinancialIndicatorResult(
        metric_id=definition.metric_id,
        market_scope=definition.market_scope,
        status=CALCULATED,
        statistic_kind=definition.statistic_kind,
        value=value,
        unit=definition.unit,
        inputs=inputs,
        lineage=dict(lineage or {}),
    )


def _seed_registry() -> FinancialIndicatorRegistry:
    registry = FinancialIndicatorRegistry()
    for concept in ("revenue", "gross_profit", "net_profit"):
        registry.register(
            FinancialIndicatorDefinition(
                metric_id=concept,
                display_name=concept.replace("_", " ").title(),
                market_scope="any",
                metric_kind="raw",
                statistic_kind="absolute_amount",
                unit="source_currency",
                statement_type="income",
                canonical_concept=concept,
                period_requirement="single_period",
                dependencies=(concept,),
                description="Canonical reported income-statement amount.",
            )
        )
    registry.register(
        FinancialIndicatorDefinition(
            metric_id="eps",
            display_name="Earnings Per Share",
            market_scope="any",
            metric_kind="raw",
            statistic_kind="per_share",
            unit="source_currency/share",
            statement_type="income",
            canonical_concept="eps",
            period_requirement="single_period",
            dependencies=("eps",),
            description="Canonical reported per-share amount.",
        )
    )
    registry.register(
        FinancialIndicatorDefinition(
            metric_id="gross_margin",
            display_name="Gross Margin",
            market_scope="any",
            metric_kind="ratio",
            statistic_kind="percent_ratio",
            unit="percent",
            numerator_metric="gross_profit",
            denominator_metric="revenue",
            dependencies=("gross_profit", "revenue"),
            period_requirement="single_period",
            description="Gross profit divided by revenue, reported as percent.",
        )
    )
    registry.register(
        FinancialIndicatorDefinition(
            metric_id="revenue_growth",
            display_name="Revenue Growth",
            market_scope="any",
            metric_kind="growth",
            statistic_kind="growth_rate",
            unit="percent",
            base_metric="revenue",
            dependencies=("revenue",),
            period_requirement="cumulative",
            description="Year-over-year revenue growth against an explicit baseline.",
        )
    )
    for concept, statement_type in (
        ("total_assets", "balance"),
        ("total_equity", "balance"),
        ("operating_cash_flow", "cash"),
        ("capital_expenditure", "cash"),
    ):
        registry.register(
            FinancialIndicatorDefinition(
                metric_id=concept,
                display_name=concept.replace("_", " ").title(),
                market_scope="any",
                metric_kind="raw",
                statistic_kind="absolute_amount",
                unit="source_currency",
                statement_type=statement_type,
                canonical_concept=concept,
                period_requirement="single_period",
                dependencies=(concept,),
                description="Canonical reported financial-statement amount.",
            )
        )
    registry.register(
        FinancialIndicatorDefinition(
            metric_id="roe",
            display_name="Return on Equity",
            market_scope="any",
            metric_kind="ratio",
            statistic_kind="percent_ratio",
            unit="percent",
            numerator_metric="net_profit",
            denominator_metric="total_equity",
            dependencies=("net_profit", "total_equity"),
            period_requirement="single_period",
            description="Net profit divided by total equity, reported as percent.",
        )
    )
    registry.register(
        FinancialIndicatorDefinition(
            metric_id="roa",
            display_name="Return on Assets",
            market_scope="any",
            metric_kind="ratio",
            statistic_kind="percent_ratio",
            unit="percent",
            numerator_metric="net_profit",
            denominator_metric="total_assets",
            dependencies=("net_profit", "total_assets"),
            period_requirement="single_period",
            description="Net profit divided by total assets, reported as percent.",
        )
    )
    registry.register(
        FinancialIndicatorDefinition(
            metric_id="free_cash_flow",
            display_name="Free Cash Flow",
            market_scope="any",
            metric_kind="difference",
            statistic_kind="absolute_amount",
            unit="source_currency",
            minuend_metric="operating_cash_flow",
            subtrahend_metric="capital_expenditure",
            dependencies=("operating_cash_flow", "capital_expenditure"),
            period_requirement="single_period",
            description="Operating cash flow minus capital expenditure.",
        )
    )
    return registry


financial_indicator_registry = _seed_registry()
