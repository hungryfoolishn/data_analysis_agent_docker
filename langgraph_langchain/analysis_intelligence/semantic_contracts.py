"""Analysis Intelligence semantic contracts (V1.0, P1).

Deterministic, testable contracts for Metrics, Dimensions, Hierarchy relationships,
Drill-down validation and Metric-Dimension compatibility. These models are the
machine-checkable counterpart of the prompt-injected semantic layer
(``langgraph_langchain.semantic``): every trust decision here is enforced in code,
never inferred from prompt text.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Iterable, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TrustLevel(str, Enum):
    """Provenance of a semantic definition. Only trusted sources define contracts."""

    TRUSTED_DEFINITION = "trusted_definition"
    EXISTING_SKILL = "existing_skill"
    DATASET_METADATA = "dataset_metadata"
    USER_DEFINITION = "user_definition"
    INFERRED_CANDIDATE = "inferred_candidate"

    @property
    def is_trusted(self) -> bool:
        return self is not TrustLevel.INFERRED_CANDIDATE


class MetricKind(str, Enum):
    BASIC = "basic"
    DERIVED = "derived"
    RATIO = "ratio"
    RATE = "rate"
    WEIGHTED = "weighted"
    COUNT = "count"
    DISTINCT_COUNT = "distinct_count"
    AVERAGE = "average"
    RANKING = "ranking"
    STATISTICAL = "statistical"


class RelationshipType(str, Enum):
    PARENT_CHILD = "parent_child"
    MAPPING = "mapping"
    MANY_TO_MANY = "many_to_many"
    CONDITIONAL = "conditional"
    PEER = "peer"


class ValidationStatus(str, Enum):
    VERIFIED = "verified"
    DECLARED = "declared"
    INFERRED = "inferred"
    UNKNOWN = "unknown"
    INVALID = "invalid"

    @classmethod
    def drillable(cls, status: "ValidationStatus") -> bool:
        """Only verified facts or trusted declared contracts allow deterministic drill-down."""
        return status in (cls.VERIFIED, cls.DECLARED)


class CompatibilityStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    CONDITIONAL = "conditional"
    UNKNOWN = "unknown"


class MetricContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    metric_kind: MetricKind = MetricKind.BASIC
    formula: str = ""
    numerator: Optional[str] = None
    denominator: Optional[str] = None
    aggregation: str = "sum"
    unit: str = "number"
    required_columns: list[str] = Field(min_length=1)
    compatible_dimensions: list[str] = Field(default_factory=list)
    incompatible_dimensions: list[str] = Field(default_factory=list)
    supported_granularity: list[str] = Field(default_factory=list)
    source: TrustLevel = TrustLevel.INFERRED_CANDIDATE
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    validation_rules: list[str] = Field(default_factory=list)
    supported_operations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ratio_needs_denominator(self) -> "MetricContract":
        if self.metric_kind in (MetricKind.RATIO, MetricKind.RATE):
            if not self.numerator or not self.denominator:
                raise ValueError(
                    f"ratio/rate metric {self.metric_id} requires numerator and denominator"
                )
            if not self.validation_rules:
                # Guard rails are mandatory: ratios must never be averaged blindly
                # nor aggregated across grains without the denominator rule.
                raise ValueError(
                    f"ratio/rate metric {self.metric_id} requires explicit validation_rules "
                    "(e.g. no-direct-average, weighted aggregation rule)"
                )
        if self.source is TrustLevel.INFERRED_CANDIDATE and self.confidence > 0.0:
            raise ValueError("inferred candidates cannot carry trusted confidence")
        return self

    def trusts_definition(self) -> bool:
        return self.source.is_trusted and self.metric_kind is not None


class DimensionContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str
    aliases: list[str] = Field(default_factory=list)
    semantic_type: str = "categorical"
    description: str = ""
    # Built-in platform dimensions vs dataset-defined vs runtime-discovered candidates.
    source: str = "dataset_defined"
    source_column: Optional[str] = None
    data_asset_id: Optional[str] = None
    cardinality: Optional[int] = None
    low_cardinality: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    validation_status: ValidationStatus = ValidationStatus.UNKNOWN
    constraints: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _inferred_needs_verification(self) -> "DimensionContract":
        if self.validation_status is ValidationStatus.VERIFIED and self.confidence < 1.0:
            raise ValueError("verified dimensions require confidence 1.0")
        return self


class DimensionRelationship(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    parent_dimension: str
    child_dimension: str
    relationship_type: RelationshipType = RelationshipType.PARENT_CHILD
    cardinality: str = "one_to_many"
    mapping_source: str = "declared"
    mapping_key: Optional[str] = None
    applicable_data_asset: Optional[str] = None
    applicable_metric: Optional[str] = None
    preconditions: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    validation_status: ValidationStatus = ValidationStatus.UNKNOWN


class DrillDownPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_dimension: str
    child_dimension: str
    relationship_id: str
    relationship_status: ValidationStatus
    filter_propagation: bool = True
    metric_id: Optional[str] = None
    grain_compatible: bool = False


class CompatibilityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CompatibilityStatus
    metric_id: str
    dimension_id: str
    dimension_combination: list[str] = Field(default_factory=list)
    granularity: Optional[str] = None
    reason: str
    preconditions: list[str] = Field(default_factory=list)
    drill_down: Optional[DrillDownPlan] = None


class SemanticRegistry:
    """Registry of metric/dimension/relationship contracts for one domain scope.

    Trust discipline: INFERRED_CANDIDATE metrics are recorded but flagged; only
    trusted definitions may drive deterministic plan validation.
    """

    def __init__(self) -> None:
        self._metrics: dict[str, MetricContract] = {}
        self._dimensions: dict[str, DimensionContract] = {}
        self._relationships: dict[str, DimensionRelationship] = {}

    def register_metric(self, metric: MetricContract) -> MetricContract:
        if metric.metric_id in self._metrics:
            raise ValueError(f"metric already registered: {metric.metric_id}")
        for alias in metric.aliases:
            owner = self._alias_metric_owner(alias)
            if owner is not None and owner != metric.metric_id:
                raise ValueError(f"alias {alias!r} already owned by metric {owner}")
        self._metrics[metric.metric_id] = metric
        return metric

    def register_dimension(self, dimension: DimensionContract) -> DimensionContract:
        if dimension.dimension_id in self._dimensions:
            raise ValueError(f"dimension already registered: {dimension.dimension_id}")
        self._dimensions[dimension.dimension_id] = dimension
        return dimension

    def register_relationship(self, relationship: DimensionRelationship) -> DimensionRelationship:
        for dim in (relationship.parent_dimension, relationship.child_dimension):
            if dim not in self._dimensions:
                raise ValueError(f"relationship references unregistered dimension: {dim}")
        self._relationships[relationship.relation_id] = relationship
        return relationship

    def _alias_metric_owner(self, alias: str) -> Optional[str]:
        lowered = alias.casefold()
        for metric in self._metrics.values():
            if metric.name.casefold() == lowered or any(
                item.casefold() == lowered for item in metric.aliases
            ):
                return metric.metric_id
        return None

    def resolve_metric(self, term: str) -> tuple[Optional[MetricContract], str]:
        """Resolve a user term to a metric. Returns (metric, resolution_method)."""
        lowered = term.strip().casefold()
        for metric in self._metrics.values():
            if metric.metric_id == lowered or metric.name.casefold() == lowered:
                return metric, "exact"
        for metric in self._metrics.values():
            if any(alias.casefold() == lowered for alias in metric.aliases):
                return metric, "alias"
        return None, "unknown"

    def resolve_dimension(self, term: str) -> tuple[Optional[DimensionContract], str]:
        lowered = term.strip().casefold()
        for dim in self._dimensions.values():
            if dim.dimension_id == lowered or dim.name.casefold() == lowered:
                return dim, "exact"
        for dim in self._dimensions.values():
            if any(alias.casefold() == lowered for alias in dim.aliases):
                return dim, "alias"
        return None, "unknown"

    def metric(self, metric_id: str) -> Optional[MetricContract]:
        return self._metrics.get(metric_id)

    def dimension(self, dimension_id: str) -> Optional[DimensionContract]:
        return self._dimensions.get(dimension_id)

    def relationships(self) -> list[DimensionRelationship]:
        return list(self._relationships.values())

    def trusted_metrics(self) -> list[MetricContract]:
        return [m for m in self._metrics.values() if m.source.is_trusted]

    def drill_down_path(
        self, parent: str, child: str, *, metric_id: Optional[str] = None
    ) -> Optional[DimensionRelationship]:
        for rel in self._relationships.values():
            if rel.parent_dimension != parent or rel.child_dimension != child:
                continue
            if metric_id and rel.applicable_metric and rel.applicable_metric != metric_id:
                continue
            return rel
        return None


class CompatibilityValidator:
    """Deterministic metric x dimension x combination x granularity checks.

    UNKNOWN is never silently upgraded to VALID: missing contracts require
    exploration or clarification first.
    """

    def __init__(self, registry: SemanticRegistry) -> None:
        self._registry = registry

    def check(
        self,
        metric_id: str,
        dimension_id: str,
        *,
        dimension_combination: Optional[list[str]] = None,
        granularity: Optional[str] = None,
        data_asset_id: Optional[str] = None,
    ) -> CompatibilityReport:
        combination = list(dimension_combination or [dimension_id])
        metric = self._registry.metric(metric_id)
        if metric is None:
            return CompatibilityReport(
                status=CompatibilityStatus.UNKNOWN,
                metric_id=metric_id,
                dimension_id=dimension_id,
                dimension_combination=combination,
                granularity=granularity,
                reason="metric is not registered; resolve or register it first",
            )
        if not metric.source.is_trusted:
            return CompatibilityReport(
                status=CompatibilityStatus.UNKNOWN,
                metric_id=metric_id,
                dimension_id=dimension_id,
                dimension_combination=combination,
                granularity=granularity,
                reason="metric definition is an inferred candidate; verification required",
                preconditions=["verify metric formula and required columns"],
            )
        dimension = self._registry.dimension(dimension_id)
        if dimension is None:
            return CompatibilityReport(
                status=CompatibilityStatus.UNKNOWN,
                metric_id=metric_id,
                dimension_id=dimension_id,
                dimension_combination=combination,
                granularity=granularity,
                reason="dimension is not registered; explore the data asset first",
                preconditions=["discover dimension candidates from the data asset"],
            )
        if dimension_id in metric.incompatible_dimensions:
            return CompatibilityReport(
                status=CompatibilityStatus.INVALID,
                metric_id=metric_id,
                dimension_id=dimension_id,
                dimension_combination=combination,
                granularity=granularity,
                reason=f"metric {metric_id} explicitly forbids dimension {dimension_id}",
            )
        if metric.compatible_dimensions and dimension_id not in metric.compatible_dimensions:
            return CompatibilityReport(
                status=CompatibilityStatus.INVALID,
                metric_id=metric_id,
                dimension_id=dimension_id,
                dimension_combination=combination,
                granularity=granularity,
                reason=(
                    f"metric {metric_id} allows dimensions "
                    f"{metric.compatible_dimensions}; {dimension_id} is not among them"
                ),
            )
        if granularity and metric.supported_granularity and granularity not in metric.supported_granularity:
            return CompatibilityReport(
                status=CompatibilityStatus.INVALID,
                metric_id=metric_id,
                dimension_id=dimension_id,
                dimension_combination=combination,
                granularity=granularity,
                reason=(
                    f"metric {metric_id} supports granularity "
                    f"{metric.supported_granularity}; {granularity} is not supported"
                ),
            )
        preconditions: list[str] = []
        status = CompatibilityStatus.VALID
        if not metric.compatible_dimensions:
            # No declared compatibility contract: execution is conditional on
            # exploring/validating that the dimension actually fits the metric.
            status = CompatibilityStatus.CONDITIONAL
            preconditions.append(
                "verify metric-dimension fit against the data asset before execution"
            )
        if dimension.data_asset_id and data_asset_id and dimension.data_asset_id != data_asset_id:
            return CompatibilityReport(
                status=CompatibilityStatus.INVALID,
                metric_id=metric_id,
                dimension_id=dimension_id,
                dimension_combination=combination,
                granularity=granularity,
                reason=(
                    f"dimension {dimension_id} belongs to asset {dimension.data_asset_id}, "
                    f"not {data_asset_id}"
                ),
            )
        if len(combination) > 1:
            for other in combination:
                if other == dimension_id:
                    continue
                other_dim = self._registry.dimension(other)
                if other_dim is None:
                    return CompatibilityReport(
                        status=CompatibilityStatus.UNKNOWN,
                        metric_id=metric_id,
                        dimension_id=dimension_id,
                        dimension_combination=combination,
                        granularity=granularity,
                        reason=f"combination member {other} is unregistered",
                    )
                if not self._registry.drill_down_path(dimension_id, other) and not (
                    other_dim.source == dimension.source and dimension_id != other
                ):
                    preconditions.append(
                        f"confirm no double counting between {dimension_id} and {other}"
                    )
                    if status is CompatibilityStatus.VALID:
                        status = CompatibilityStatus.CONDITIONAL
        return CompatibilityReport(
            status=status,
            metric_id=metric_id,
            dimension_id=dimension_id,
            dimension_combination=combination,
            granularity=granularity,
            reason="declared contracts satisfied" if status is CompatibilityStatus.VALID else "conditional on validation preconditions",
            preconditions=preconditions,
        )

    def check_drill_down(
        self,
        metric_id: str,
        parent: str,
        child: str,
        *,
        data_asset_id: Optional[str] = None,
    ) -> CompatibilityReport:
        metric = self._registry.metric(metric_id)
        if metric is None or not metric.source.is_trusted:
            return CompatibilityReport(
                status=CompatibilityStatus.UNKNOWN,
                metric_id=metric_id,
                dimension_id=child,
                dimension_combination=[parent, child],
                reason="drill-down requires a trusted metric contract",
            )
        relationship = self._registry.drill_down_path(parent, child, metric_id=metric_id)
        if relationship is None:
            return CompatibilityReport(
                status=CompatibilityStatus.UNKNOWN,
                metric_id=metric_id,
                dimension_id=child,
                dimension_combination=[parent, child],
                reason=(
                    f"no verified relationship {parent} -> {child}; explore or clarify, "
                    "never fabricate a hierarchy"
                ),
                preconditions=["discover and verify the relationship path"],
            )
        if not ValidationStatus.drillable(relationship.validation_status):
            return CompatibilityReport(
                status=CompatibilityStatus.UNKNOWN,
                metric_id=metric_id,
                dimension_id=child,
                dimension_combination=[parent, child],
                reason=(
                    f"relationship {relationship.relation_id} is "
                    f"{relationship.validation_status.value}; verification required before drill-down"
                ),
                preconditions=["verify relationship against data"],
            )
        for dim in (parent, child):
            report = self.check(metric_id, dim, data_asset_id=data_asset_id)
            if report.status is CompatibilityStatus.INVALID:
                report = report.model_copy(
                    update={"drill_down": None, "reason": f"drill-down blocked: {report.reason}"}
                )
                return report
        plan = DrillDownPlan(
            parent_dimension=parent,
            child_dimension=child,
            relationship_id=relationship.relation_id,
            relationship_status=relationship.validation_status,
            filter_propagation=True,
            metric_id=metric_id,
            grain_compatible=True,
        )
        return CompatibilityReport(
            status=CompatibilityStatus.VALID,
            metric_id=metric_id,
            dimension_id=child,
            dimension_combination=[parent, child],
            reason=f"drill-down via {relationship.relation_id} ({relationship.validation_status.value})",
            drill_down=plan,
        )


_MONTH_TIME_TOKENS = ("date", "time", "month", "week", "quarter", "year", "day", "period")


def derive_dimension_candidates(
    columns: Mapping[str, str],
    *,
    cardinalities: Optional[Mapping[str, int]] = None,
    data_asset_id: Optional[str] = None,
) -> list[DimensionContract]:
    """Deterministic semantic discovery over a bounded schema snapshot.

    ``columns`` maps column name -> dtype (from ``scan_schema``), optionally with
    pre-computed cardinalities. Candidates are INFERRED with UNKNOWN validation
    status; they never auto-promote to trusted dimensions.
    """
    candidates: list[DimensionContract] = []
    for name, dtype in columns.items():
        lowered = name.lower()
        semantic_type = "categorical"
        confidence = 0.0
        if any(token in lowered for token in _MONTH_TIME_TOKENS) or dtype.startswith("datetime"):
            semantic_type = "temporal"
            confidence = 0.8
        elif dtype in {"object", "category", "string"}:
            semantic_type = "categorical"
            confidence = 0.4
            card = (cardinalities or {}).get(name)
            if card is not None and 1 < card <= 200:
                confidence = 0.7
            elif card is not None and card > 200:
                confidence = 0.2
                constraints = [f"high cardinality ({card}) likely identifier, not a business dimension"]
                candidates.append(DimensionContract(
                    dimension_id=re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")[:63] or "dimension",
                    name=name,
                    semantic_type="identifier",
                    source="runtime_discovered",
                    source_column=name,
                    data_asset_id=data_asset_id,
                    cardinality=card,
                    confidence=confidence,
                    constraints=constraints,
                ))
                continue
        else:
            confidence = 0.1
        dimension_id = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")[:63] or "dimension"
        candidates.append(DimensionContract(
            dimension_id=dimension_id,
            name=name,
            semantic_type=semantic_type,
            source="runtime_discovered",
            source_column=name,
            data_asset_id=data_asset_id,
            cardinality=(cardinalities or {}).get(name),
            confidence=confidence,
        ))
    return candidates
