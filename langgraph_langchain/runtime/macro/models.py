"""Macro Domain Pack domain models (B9.1, frozen per b9.0 design §4-§5)."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

TransformKind = Literal[
    "yoy",
    "mom",
    "qoq",
    "annualized",
    "cumulative",
    "index_change",
    "rolling_average",
    "period_comparison",
    "cumulative_to_single_period",
]

FrequencyKind = Literal["monthly", "quarterly", "annual", "weekly", "daily"]
AdjustmentKind = Literal["sa", "nsa", "unknown"]
PriceBasisKind = Literal["current", "real", "index", "unknown"]
LevelKind = Literal["level", "growth_rate", "index"]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MacroIndicator(BaseModel):
    """One macroeconomic indicator definition bound to a real adapter dataset."""

    indicator_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,80}$")
    concept: str = Field(min_length=1)            # "cpi" / "gdp" / "unemployment_rate" ...
    country: Literal["us", "cn"]
    source_id: str = Field(min_length=1)          # AdapterRegistry adapter_id
    dataset_id: str = Field(min_length=1)         # DatasetCatalog dataset_id (must exist)
    series_parameters: dict[str, Any] = Field(default_factory=dict)
    unit: str = Field(min_length=1)
    frequency: FrequencyKind
    seasonal_adjustment: AdjustmentKind = "unknown"
    price_basis: PriceBasisKind = "unknown"
    cumulative: bool = False
    level_kind: LevelKind = "level"
    base_period: Optional[str] = None
    release_lag_days: Optional[int] = None
    revision_policy: Literal["revised", "final_only", "unknown"] = "unknown"
    comparability: dict[str, str] = Field(default_factory=dict)
    supported_transforms: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("supported_transforms", mode="before")
    @classmethod
    def _default_transforms(cls, value):
        if not value:
            return ("yoy", "index_change", "rolling_average")
        return value


class MacroIndicatorCatalog(BaseModel):
    """Versioned macro indicator catalog over real adapter datasets."""

    catalog_version: str = "b9.1.0"
    indicators: dict[str, MacroIndicator] = Field(default_factory=dict)

    @field_validator("indicators")
    @classmethod
    def _unique_concept_dataset(cls, value: dict[str, MacroIndicator]):
        # One indicator per (concept, dataset_id): a dataset backs at most one
        # indicator definition; multiple series per concept/country are
        # legitimate (e.g. real vs nominal GDP).
        seen: set[tuple[str, str]] = set()
        for indicator in value.values():
            key = (indicator.concept, indicator.dataset_id)
            if key in seen:
                raise ValueError(
                    f"Duplicate concept/dataset in catalog: {key}"
                )
            seen.add(key)
        return value

    def get(self, indicator_id: str) -> MacroIndicator:
        if indicator_id not in self.indicators:
            raise KeyError(f"Unknown macro indicator: {indicator_id}")
        return self.indicators[indicator_id]

    def resolve(
        self,
        *,
        concept: str,
        country: Optional[str] = None,
        frequency: Optional[str] = None,
    ) -> list[MacroIndicator]:
        """Resolve indicators by concept (+ optional country/frequency filter)."""
        matches = [
            indicator
            for indicator in self.indicators.values()
            if indicator.concept == concept
            and (country is None or indicator.country == country)
            and (frequency is None or indicator.frequency == frequency)
        ]
        return sorted(matches, key=lambda item: item.indicator_id)

    def filter(
        self,
        *,
        country: Optional[str] = None,
        source_id: Optional[str] = None,
        unit: Optional[str] = None,
        frequency: Optional[str] = None,
        transform: Optional[str] = None,
    ) -> list[MacroIndicator]:
        result = []
        for indicator in self.indicators.values():
            if country and indicator.country != country:
                continue
            if source_id and indicator.source_id != source_id:
                continue
            if unit and indicator.unit != unit:
                continue
            if frequency and indicator.frequency != frequency:
                continue
            if transform and transform not in indicator.supported_transforms:
                continue
            result.append(indicator)
        return sorted(result, key=lambda item: item.indicator_id)

    def to_json(self) -> str:
        import json

        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, payload: str) -> "MacroIndicatorCatalog":
        return cls.model_validate_json(payload)

    @classmethod
    def load(cls, path) -> "MacroIndicatorCatalog":
        from pathlib import Path
        import json

        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(raw)


class MacroTimeSemantics(BaseModel):
    """Time/statistical semantics that must match before comparing series."""

    frequency: FrequencyKind
    seasonal_adjustment: AdjustmentKind
    price_basis: PriceBasisKind
    cumulative: bool = False
    level_kind: LevelKind = "level"
    base_period: Optional[str] = None

    def comparable_with(self, other: "MacroTimeSemantics") -> list[str]:
        """Return incompatibility reasons; empty list means comparable."""
        reasons: list[str] = []
        if self.frequency != other.frequency:
            reasons.append(
                f"frequency mismatch: {self.frequency} vs {other.frequency}"
            )
        if self.seasonal_adjustment != other.seasonal_adjustment and (
            "unknown" not in {self.seasonal_adjustment, other.seasonal_adjustment}
        ):
            reasons.append(
                f"seasonal adjustment mismatch: {self.seasonal_adjustment} vs "
                f"{other.seasonal_adjustment}"
            )
        if self.price_basis != other.price_basis and (
            "unknown" not in {self.price_basis, other.price_basis}
        ):
            reasons.append(
                f"price basis mismatch: {self.price_basis} vs {other.price_basis}"
            )
        if self.cumulative != other.cumulative:
            reasons.append(
                f"cumulative mismatch: {self.cumulative} vs {other.cumulative}"
            )
        if self.base_period and other.base_period and self.base_period != other.base_period:
            reasons.append(
                f"base period mismatch: {self.base_period} vs {other.base_period}"
            )
        return reasons


class MacroObservation(BaseModel):
    """A macro observation that REFERENCES a platform DataAsset.

    Frozen contract §5.1: no second observation-value store.  Raw values are
    always recovered through AssetResolver/DataAsset; the fields below are
    lightweight pointers plus snapshot metadata for provenance.
    """

    indicator_id: str
    country: Literal["us", "cn"]
    asset_id: str                      # platform DataAsset reference
    data_version: str                  # DataAsset.content_hash
    observation_period: str            # period the data belongs to ("2026-03"/"2026Q1"/"2025")
    observation_date: Optional[str] = None
    release_date: Optional[str] = None
    revision_date: Optional[str] = None
    fetched_at: Optional[str] = None
    unit: str
    source_version: Optional[str] = None  # adapter_version


class MacroTransformResult(BaseModel):
    """Lineage record of one macro transform (B4.2.1.1 §5.7: multi-input)."""

    transform: str
    input_asset_ids: list[str] = Field(min_length=1)
    input_data_versions: list[str] = Field(min_length=1)
    output_asset_id: Optional[str] = None
    indicator_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    semantics_in: list[MacroTimeSemantics] = Field(default_factory=list)
    semantics_out: Optional[MacroTimeSemantics] = None
    warnings: list[str] = Field(default_factory=list)
    transform_version: str = "b9.2.0"
    computed_at: str = Field(default_factory=_utc_now_iso)


class MacroEvidenceRequirement(BaseModel):
    min_observations: int = Field(default=2, ge=1)
    required_assets: list[str] = Field(default_factory=list)
    required_transforms: list[str] = Field(default_factory=list)
    required_disclosures: tuple[str, ...] = (
        "unit",
        "seasonal_adjustment",
        "release_lag",
    )


class MacroAnalysisTemplate(BaseModel):
    template_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,60}$")
    intent: str
    required_concepts: list[str] = Field(default_factory=list)
    optional_concepts: list[str] = Field(default_factory=list)
    countries: tuple[Literal["us", "cn"], ...] = ("us",)
    default_transforms: list[str] = Field(default_factory=list)
    analysis_steps: list[str] = Field(default_factory=list)
    evidence_requirements: MacroEvidenceRequirement = Field(
        default_factory=MacroEvidenceRequirement
    )
    report_sections: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    data_sufficiency_policy: Literal["ready", "partially_ready", "blocked"] = (
        "partially_ready"
    )


class MacroPlan(BaseModel):
    """A macro plan bound to its template and resolved observations."""

    plan_id: str = Field(default_factory=lambda: f"macro_{__import__('uuid').uuid4().hex}")
    template_id: str
    question: str
    observations: list[MacroObservation] = Field(default_factory=list)
    data_sufficiency: Literal["ready", "partially_ready", "blocked"] = "blocked"
    missing: list[str] = Field(default_factory=list)
    general_plan: Optional[dict[str, Any]] = None  # serialized GeneralAnalysisPlan v2
    created_at: str = Field(default_factory=_utc_now_iso)
