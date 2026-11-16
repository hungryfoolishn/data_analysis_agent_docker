"""Cross-country same-concept comparison pipeline (B9.8.2 stage 2).

Resolves per-(concept, country) asset legs from the IndicatorAssetRegistry,
unifies each leg into a comparable rate metric, validates semantic
comparability (price basis / statistical nature), normalizes frequencies,
aligns on common periods, and produces the aligned comparison frame that
``MacroRuntimeIntegration`` registers as a DataAsset and feeds into the
comparison analysis plan.

Frozen rules (task B9.8.2 §4.5):
- alignment is by REAL period keys, never by row position;
- frequency-incompatible series are normalized by an explicit, disclosed
  conversion (calendar-year mean of sub-annual rates) — never silent
  resampling, never treating monthly data as quarterly;
- CPI comparison happens on YoY RATES, never on index levels with different
  base periods; published YoY rates are passed through, never re-diffed
  (B9 §5.5);
- nominal-vs-real GDP growth rates are NOT directly comparable: the direct
  comparison is blocked with an explicit reason unless a deflator converts
  one side (no such series is registered today);
- duplicate normalized periods collapse by mean, with disclosure;
- missing periods are handled by inner-join on the common period set, and
  the coverage is disclosed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

import pandas as pd

from langgraph_langchain.runtime.macro.models import (
    MacroIndicator,
    MacroTimeSemantics,
    ResolvedIndicatorAsset,
)
from langgraph_langchain.runtime.macro.registry import (
    IndicatorAssetKey,
    resolve_indicator_asset_key,
)
from langgraph_langchain.runtime.macro.transforms import (
    MacroTransformError,
    apply_macro_transform,
)

__all__ = [
    "ComparisonSpec",
    "ComparisonError",
    "CountryAssetLeg",
    "ConceptComparison",
    "ComparisonResolution",
    "CrossCountryComparator",
]

_FREQUENCY_ORDER = {"weekly": 0, "monthly": 1, "quarterly": 2, "annual": 3}
_PANDAS_ALIAS = {"monthly": "M", "quarterly": "Q", "annual": "Y", "weekly": "W"}

# Rate nature after metric unification: a YoY rate derived from an index or
# unknown-basis level is a pure rate (base-independent); from a real level it
# is a real growth rate; from a current-price level it is a nominal rate.
_RATE_NATURE = {
    "index": "rate",
    "unknown": "rate",
    "real": "real",
    "current": "nominal",
}


class ComparisonError(ValueError):
    """Raised when a comparison request cannot be resolved at all."""


@dataclass(frozen=True)
class ComparisonSpec:
    """Explicit comparison request (B9.8.2 §4.2)."""

    comparison_type: str = "cross_country"
    countries: tuple[str, ...] = ()
    concepts: tuple[str, ...] = ()
    requested_period: Optional[tuple[Optional[str], Optional[str]]] = None
    requested_frequency: Optional[str] = None
    comparison_method: str = "yoy_rate"
    alignment_policy: str = "common_period"


@dataclass(frozen=True)
class CountryAssetLeg:
    """One (concept, country) resolved asset inside a comparison."""

    concept: str
    country: str
    indicator: MacroIndicator
    key: IndicatorAssetKey
    asset_id: str
    observation_count: int

    @property
    def semantics(self) -> MacroTimeSemantics:
        indicator = self.indicator
        return MacroTimeSemantics(
            frequency=indicator.frequency,
            seasonal_adjustment=indicator.seasonal_adjustment,
            price_basis=indicator.price_basis,
            cumulative=indicator.cumulative,
            level_kind=indicator.level_kind,
            base_period=indicator.base_period,
        )

    @property
    def rate_nature(self) -> str:
        if self.indicator.level_kind == "growth_rate":
            return "rate"
        return _RATE_NATURE.get(self.indicator.price_basis, "rate")


@dataclass
class ConceptComparison:
    """Per-concept comparison verdict and aligned series."""

    concept: str
    legs: list[CountryAssetLeg] = field(default_factory=list)
    comparable: bool = False
    comparison_frequency: Optional[str] = None
    incompatibilities: list[str] = field(default_factory=list)
    disclosures: list[str] = field(default_factory=list)
    aligned_periods: list[str] = field(default_factory=list)
    rate_columns: list[str] = field(default_factory=list)
    # rate_column -> {period_str: value}, inner-joined on common periods
    aligned_values: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass
class ComparisonResolution:
    """Full comparison resolution: legs, verdicts, missing disclosure."""

    spec: ComparisonSpec
    comparisons: list[ConceptComparison] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def comparable_concepts(self) -> list[str]:
        return [item.concept for item in self.comparisons if item.comparable]

    @property
    def blocked(self) -> bool:
        """True when NO requested concept produced a complete country set."""
        return bool(self.spec.concepts) and not any(
            len(item.legs) >= 2 for item in self.comparisons
        )

    @property
    def legs(self) -> list[CountryAssetLeg]:
        return [leg for item in self.comparisons for leg in item.legs]


def _parse_periods(values: Sequence[str], frequency: str) -> pd.PeriodIndex:
    alias = _PANDAS_ALIAS.get(frequency)
    if alias is None:
        raise ComparisonError(f"Unsupported comparison frequency: {frequency!r}")
    try:
        return pd.PeriodIndex(pd.to_datetime(list(values)), freq=alias)
    except (TypeError, ValueError, OverflowError):
        try:
            return pd.PeriodIndex([pd.Period(str(v), freq=alias) for v in values])
        except (TypeError, ValueError) as exc:
            raise ComparisonError(
                f"Cannot parse periods as {frequency}: {exc}"
            ) from exc


def _leg_period_frame(
    frame: pd.DataFrame, leg: CountryAssetLeg
) -> tuple[pd.DataFrame, list[str]]:
    """Normalize a leg's raw adapter frame to [period, value] with real
    PeriodIndex keys (B9.8.2 §4.5: never row-position alignment).

    Duplicate periods collapse by mean at parse time — before any transform —
    so derived series can never inherit duplicated index entries; the
    collapse is disclosed.
    """
    if "period" in frame.columns:
        period_col = "period"
    elif "date" in frame.columns:
        period_col = "date"
    else:
        raise ComparisonError(
            f"Asset for {leg.concept}/{leg.country} has no period/date column: "
            f"{list(frame.columns)}"
        )
    periods = _parse_periods(frame[period_col].astype(str).tolist(), leg.indicator.frequency)
    values = pd.to_numeric(frame["value"], errors="coerce")
    out = pd.DataFrame({"period": periods, "value": values})
    out = out.dropna(subset=["period", "value"]).sort_values("period")
    disclosures: list[str] = []
    duplicated = int(out.duplicated(subset=["period"]).sum())
    if duplicated:
        out = out.groupby("period", as_index=False)["value"].mean()
        disclosures.append(
            f"{leg.country} {leg.concept}: {duplicated} duplicate period(s) "
            "collapsed by mean"
        )
    return out.reset_index(drop=True), disclosures


def _leg_rate_series(
    frame: pd.DataFrame, leg: CountryAssetLeg
) -> tuple[pd.DataFrame, list[str]]:
    """Unify one leg into a YoY-rate series.

    Published growth-rate legs pass through untouched (B9 §5.5); level/index
    legs get a frequency-aware yoy transform (annual yoy = previous-year
    change).  Returns (rate_frame, disclosures).
    """
    prepared, parse_disclosures = _leg_period_frame(frame, leg)
    disclosures: list[str] = list(parse_disclosures)
    if leg.indicator.level_kind == "growth_rate":
        # Published YoY rate: use as-is, never re-diff.
        disclosures.append(
            f"{leg.country} {leg.concept}: published YoY rate used as-is "
            "(not re-differentiated)"
        )
        return prepared, disclosures
    result, warnings = apply_macro_transform(
        prepared,
        transform="yoy",
        frequency=leg.indicator.frequency,
        semantics=leg.semantics,
    )
    for warning in warnings:
        disclosures.append(f"{leg.country} {leg.concept}: {warning}")
    disclosures.append(
        f"{leg.country} {leg.concept}: yoy computed from "
        f"{leg.indicator.level_kind} series ({leg.indicator.price_basis} basis, "
        f"{leg.indicator.frequency})"
    )
    return result, disclosures


def _normalize_frequency(
    rates: list[tuple[CountryAssetLeg, pd.DataFrame]],
    target: str,
) -> tuple[list[tuple[CountryAssetLeg, pd.DataFrame]], list[str]]:
    """Normalize sub-annual rate series to the target (coarser) frequency by
    calendar-year mean — an explicit, disclosed conversion (never silent
    resampling)."""
    disclosures: list[str] = []
    normalized: list[tuple[CountryAssetLeg, pd.DataFrame]] = []
    for leg, frame in rates:
        if leg.indicator.frequency == target:
            normalized.append((leg, frame))
            continue
        periods = pd.PeriodIndex(frame["period"], freq=_PANDAS_ALIAS[leg.indicator.frequency])
        grouped = (
            pd.DataFrame({"year": periods.year, "value": frame["value"].values})
            .groupby("year")["value"]
            .mean()
            .dropna()
        )
        out = pd.DataFrame(
            {"period": [pd.Period(f"{y}", freq="Y") for y in grouped.index],
             "value": grouped.values}
        )
        disclosures.append(
            f"{leg.country} {leg.concept}: {leg.indicator.frequency} rates "
            f"aggregated to {target} by calendar-period mean"
        )
        normalized.append((leg, out))
    return normalized, disclosures


class CrossCountryComparator:
    """Resolve and compare the same concept across requested countries."""

    def __init__(
        self,
        resolved_assets: dict[str, ResolvedIndicatorAsset],
        resolve_dataframe: Callable[[str], pd.DataFrame],
    ) -> None:
        self._resolved = resolved_assets
        self._resolve_dataframe = resolve_dataframe

    def resolve(self, spec: ComparisonSpec) -> ComparisonResolution:
        resolution = ComparisonResolution(spec=spec)
        index = {
            resolve_indicator_asset_key(item.indicator): (indicator_id, item)
            for indicator_id, item in self._resolved.items()
        }
        for concept in spec.concepts:
            comparison = ConceptComparison(concept=concept)
            for country in spec.countries:
                match = next(
                    (
                        (indicator_id, item)
                        for key, (indicator_id, item) in index.items()
                        if key.concept == concept and key.country == country
                    ),
                    None,
                )
                if match is None:
                    resolution.missing.append(f"{concept}:{country}")
                    continue
                indicator_id, item = match
                comparison.legs.append(
                    CountryAssetLeg(
                        concept=concept,
                        country=country,
                        indicator=item.indicator,
                        key=resolve_indicator_asset_key(item.indicator),
                        asset_id=item.asset_id,
                        observation_count=item.observation_count,
                    )
                )
            if comparison.legs:
                resolution.comparisons.append(comparison)
        self._evaluate(resolution)
        return resolution

    def _evaluate(self, resolution: ComparisonResolution) -> None:
        for comparison in resolution.comparisons:
            if len(comparison.legs) < 2:
                comparison.incompatibilities.append(
                    "incomplete country set: "
                    f"{[leg.country for leg in comparison.legs]} of "
                    f"{list(resolution.spec.countries)}"
                )
                continue
            # Semantic comparability on rate nature (real vs nominal GDP rule).
            natures = {leg.rate_nature for leg in comparison.legs}
            if "real" in natures and "nominal" in natures:
                comparison.incompatibilities.append(
                    "price basis mismatch at growth-rate level: "
                    + " vs ".join(
                        f"{leg.country}={leg.rate_nature}" for leg in comparison.legs
                    )
                    + "; direct growth-rate comparison blocked (no deflator "
                    "series registered to convert)"
                )
                continue
            # Frequency normalization to the coarsest common frequency.
            target = max(
                (leg.indicator.frequency for leg in comparison.legs),
                key=lambda f: _FREQUENCY_ORDER.get(f, -1),
            )
            rate_frames: list[tuple[CountryAssetLeg, pd.DataFrame]] = []
            all_disclosures: list[str] = []
            blocked = False
            for leg in comparison.legs:
                try:
                    rates, disclosures = _leg_rate_series(
                        self._resolve_dataframe(leg.asset_id), leg
                    )
                except (ComparisonError, MacroTransformError) as exc:
                    comparison.incompatibilities.append(
                        f"{leg.country} {leg.concept}: {exc}"
                    )
                    blocked = True
                    continue
                all_disclosures.extend(disclosures)
                rate_frames.append((leg, rates))
            if blocked or len(rate_frames) < 2:
                continue
            rate_frames, normalize_disclosures = _normalize_frequency(
                rate_frames, target
            )
            all_disclosures.extend(normalize_disclosures)
            # Align on real common periods (inner join on PeriodIndex).
            aligned: dict[pd.Period, dict[str, float]] = {}
            for leg, frame in rate_frames:
                for _, row in frame.iterrows():
                    columns = aligned.setdefault(row["period"], {})
                    name = f"{leg.concept}_{leg.country}"
                    if name in columns:
                        # Defensive duplicate collapse by mean (adapters dedup
                        # upstream; duplicates here can only come from an
                        # unexpected payload shape).
                        columns[name] = (columns[name] + float(row["value"])) / 2.0
                        comparison.disclosures.append(
                            f"duplicate normalized period collapsed by mean: "
                            f"{name}@{row['period']}"
                        )
                    else:
                        columns[name] = float(row["value"])
            common = sorted(
                period
                for period, values in aligned.items()
                if len(values) == len(rate_frames)
            )
            if len(common) < 2:
                comparison.incompatibilities.append(
                    "insufficient common periods after alignment: "
                    f"{[str(p) for p in common]}"
                )
                continue
            for period in common:
                comparison.aligned_periods.append(str(period))
            comparison.rate_columns = sorted(aligned[common[0]])
            # Store the inner-joined aligned values (single source for the
            # registered aligned asset — no re-resolution divergence).
            for period in common:
                for column, value in aligned[period].items():
                    comparison.aligned_values.setdefault(column, {})[
                        str(period)
                    ] = value
            comparison.comparison_frequency = target
            comparison.disclosures = all_disclosures
            comparison.disclosures.append(
                "alignment: inner join on real period keys; common periods "
                f"{comparison.aligned_periods[0]}..{comparison.aligned_periods[-1]} "
                f"({len(common)} periods)"
            )
            comparison.comparable = True

    def build_aligned_frame(self, resolution: ComparisonResolution) -> pd.DataFrame:
        """Build the aligned comparison frame from evaluated comparisons."""
        columns: dict[str, dict[str, float]] = {}
        for comparison in resolution.comparisons:
            if not comparison.comparable:
                continue
            for column, values in comparison.aligned_values.items():
                merged = columns.setdefault(column, {})
                for period, value in values.items():
                    merged[period] = value
        if not columns:
            return pd.DataFrame()
        frame = pd.DataFrame(
            {name: pd.Series(values) for name, values in sorted(columns.items())}
        )
        frame.index.name = "period"
        return frame.sort_index()
