"""Statistical semantics for macro indicator series (B9.8.3).

Separates three concepts that must never be conflated:

- physical/display unit (``unit``): percent, index points, currency, …
- statistical kind (``statistic_kind``): what the VALUE means — an index
  level, a published year-over-year index (base = 100), a YoY rate in
  percent, a level quantity, …
- price basis (``price_basis``): current / real / index.

The B9.8.3 P1: NBS publishes CPI as a "same period last year = 100" index
(100.2 means +0.2%), which the comparison pipeline passed through as if it
were a YoY rate in percent, mixing quantity dimensions in the aligned frame.
``normalize_to_yoy_rate`` is now the single explicit conversion exit; the
conversion table is closed (unknown kinds are rejected, never guessed) and
every conversion records its method and version for lineage.

Conversion table (frozen):

======================  ==============================  ==========================
input statistic_kind    method                          output
======================  ==============================  ==========================
yoy_rate                passthrough (never re-shifted)  yoy_rate / percent
yoy_index               value - 100.0                   yoy_rate / percent
index_level             PeriodIndex-aware yoy lag       yoy_rate / percent
level                   PeriodIndex-aware yoy lag       yoy_rate / percent (basis
                                                        carried: real/nominal)
mom_rate / qoq_rate     NOT supported                   explicit error
unknown                 NOT supported                   explicit error (no guessing)
======================  ==============================  ==========================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import pandas as pd

from langgraph_langchain.runtime.macro.models import MacroTimeSemantics
from langgraph_langchain.runtime.macro.transforms import MacroTransformError, apply_macro_transform

if TYPE_CHECKING:  # pragma: no cover
    from langgraph_langchain.runtime.macro.models import MacroIndicator

__all__ = [
    "StatisticKind",
    "STATISTICS_VERSION",
    "StatisticSemanticsError",
    "NormalizedRateResult",
    "resolve_statistic_kind",
    "normalize_to_yoy_rate",
    "YOY_INDEX_UNIT",
    "PERCENT_UNIT",
]

StatisticKind = Literal[
    "level",
    "index_level",
    "yoy_index",
    "yoy_rate",
    "mom_rate",
    "qoq_rate",
    "unknown",
]

STATISTICS_VERSION = "b9.8.3.0"

# Known unit constants — exact matches only; unit strings are never fuzzy-matched.
YOY_INDEX_UNIT = "same_period_last_year_100"
PERCENT_UNIT = "percent"


class StatisticSemanticsError(ValueError):
    """Raised when a statistic kind cannot be normalized or is unknown."""


@dataclass(frozen=True)
class NormalizedRateResult:
    """Result of one explicit statistic-kind normalization.

    ``dataframe`` is a NEW frame ([period, value], value = YoY rate in
    percent); the input frame is never mutated.
    """

    dataframe: pd.DataFrame
    indicator_id: str
    input_statistic_kind: StatisticKind
    output_statistic_kind: str
    output_unit: str
    conversion_method: str
    warnings: list[str] = field(default_factory=list)
    transform_version: str = STATISTICS_VERSION


def resolve_statistic_kind(indicator: "MacroIndicator") -> StatisticKind:
    """Resolve the statistical kind of an indicator's values.

    An explicitly declared ``statistic_kind`` field always wins.  Legacy
    catalog entries without the field are derived through the documented
    whitelist below (``level_kind`` first, and — only inside the
    ``growth_rate`` family — exact known unit constants).  Derivation never
    guesses: anything outside the whitelist is ``unknown`` and will be
    rejected downstream instead of silently converted.
    """
    declared = getattr(indicator, "statistic_kind", None)
    if declared is not None:
        return declared
    if indicator.level_kind == "index":
        return "index_level"
    if indicator.level_kind == "level":
        return "level"
    if indicator.level_kind == "growth_rate":
        if indicator.unit == YOY_INDEX_UNIT:
            return "yoy_index"
        if indicator.unit == PERCENT_UNIT:
            return "yoy_rate"
        return "unknown"
    return "unknown"


def _validated_numeric(values: pd.Series, *, kind: StatisticKind, indicator_id: str) -> pd.Series:
    """Reject non-finite values; NaN is preserved as missing (never treated
    as a numeric zero/−100).  Non-positive inputs are invalid for a base-100
    YoY index (100 = zero growth; the index cannot be ≤ 0)."""
    numeric = pd.to_numeric(values, errors="coerce")
    finite = numeric.dropna()
    import numpy as np

    if np.isinf(finite.to_numpy(dtype="float64")).any():
        raise StatisticSemanticsError(
            f"{indicator_id}: non-finite (inf) values are not allowed in "
            f"{kind!r} input"
        )
    if kind == "yoy_index" and (finite <= 0).any():
        raise StatisticSemanticsError(
            f"{indicator_id}: a base-100 YoY index must be positive; got "
            f"non-positive values"
        )
    return numeric


def normalize_to_yoy_rate(
    frame: pd.DataFrame,
    indicator: "MacroIndicator",
    *,
    period_column: str = "period",
    value_column: str = "value",
) -> NormalizedRateResult:
    """Normalize one indicator series to a YoY rate in percent.

    Pure function: the input frame is never mutated; the returned frame is a
    fresh [period, value] frame whose ``value`` is always percentage points
    of year-over-year change.  Unknown/unsupported statistic kinds raise
    ``StatisticSemanticsError`` — nothing is auto-guessed.
    """
    kind = resolve_statistic_kind(indicator)

    if kind == "yoy_rate":
        numeric = _validated_numeric(frame[value_column], kind=kind, indicator_id=indicator.indicator_id)
        out = pd.DataFrame(
            {"period": frame[period_column].tolist(), "value": numeric.to_numpy()}
        )
        return NormalizedRateResult(
            dataframe=out,
            indicator_id=indicator.indicator_id,
            input_statistic_kind=kind,
            output_statistic_kind="yoy_rate",
            output_unit=PERCENT_UNIT,
            conversion_method="passthrough_yoy_rate",
            warnings=[
                "published YoY rate used as-is (not re-differentiated, "
                "not shifted by 100)"
            ],
        )

    if kind == "yoy_index":
        numeric = _validated_numeric(frame[value_column], kind=kind, indicator_id=indicator.indicator_id)
        # NaN stays NaN (an observation missing from the source must never
        # become -100%); finite values convert by the base-100 rule.
        rates = numeric - 100.0
        out = pd.DataFrame(
            {"period": frame[period_column].tolist(), "value": rates.to_numpy()}
        )
        return NormalizedRateResult(
            dataframe=out,
            indicator_id=indicator.indicator_id,
            input_statistic_kind=kind,
            output_statistic_kind="yoy_rate",
            output_unit=PERCENT_UNIT,
            conversion_method="yoy_index_minus_100",
            warnings=[
                f"YoY index (unit={indicator.unit!r}, base=100) converted to "
                "percent by value - 100.0"
            ],
        )

    if kind in {"index_level", "level"}:
        semantics = MacroTimeSemantics(
            frequency=indicator.frequency,
            seasonal_adjustment=indicator.seasonal_adjustment,
            price_basis=indicator.price_basis,
            cumulative=indicator.cumulative,
            level_kind=indicator.level_kind,
            base_period=indicator.base_period,
        )
        try:
            result, warnings = apply_macro_transform(
                frame,
                transform="yoy",
                frequency=indicator.frequency,
                semantics=semantics,
                time_column=period_column,
                value_column=value_column,
            )
        except MacroTransformError as exc:
            raise StatisticSemanticsError(
                f"{indicator.indicator_id}: cannot compute YoY from "
                f"{kind!r} input: {exc}"
            ) from exc
        return NormalizedRateResult(
            dataframe=result,
            indicator_id=indicator.indicator_id,
            input_statistic_kind=kind,
            output_statistic_kind="yoy_rate",
            output_unit=PERCENT_UNIT,
            conversion_method=(
                "yoy_period_lag_transform"
                if kind == "level"
                else "index_level_yoy_period_lag_transform"
            ),
            warnings=list(warnings),
        )

    raise StatisticSemanticsError(
        f"{indicator.indicator_id}: statistic kind {kind!r} cannot be "
        "normalized to a YoY rate; refusing to guess (declare statistic_kind "
        "in the catalog or convert at acquisition)"
    )