"""Analysis Intent understanding (V1.0, P2).

Deterministic conversion of a user question into a validated AnalysisIntent with
per-field provenance. Intent is separate from Plan: the planner consumes an intent
reference but can never rewrite the user goal.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from langgraph_langchain.analysis_intelligence.semantic_contracts import (
    CompatibilityStatus,
    CompatibilityValidator,
    MetricContract,
    SemanticRegistry,
)


class Provenance(str, Enum):
    EXPLICIT = "explicit"
    RESOLVED = "resolved"
    INFERRED = "inferred"
    ASSUMED = "assumed"
    UNKNOWN = "unknown"


class IntentStatus(str, Enum):
    READY = "ready"
    READY_WITH_ASSUMPTIONS = "ready_with_assumptions"
    NEEDS_EXPLORATION = "needs_exploration"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNSUPPORTED = "unsupported"


class AnalysisType(str, Enum):
    OVERVIEW = "overview"
    AGGREGATION = "aggregation"
    COMPARISON = "comparison"
    RANKING = "ranking"
    TREND = "trend"
    DISTRIBUTION = "distribution"
    CONTRIBUTION = "contribution"
    CORRELATION = "correlation"
    ANOMALY = "anomaly"
    DRILL_DOWN = "drill_down"
    DIAGNOSIS = "diagnosis"
    FORECAST = "forecast"


# Analysis types the current deterministic runtime can actually execute end-to-end.
SUPPORTED_ANALYSIS_TYPES = {
    AnalysisType.AGGREGATION,
    AnalysisType.COMPARISON,
    AnalysisType.RANKING,
    AnalysisType.TREND,
    AnalysisType.DISTRIBUTION,
    AnalysisType.CONTRIBUTION,
    AnalysisType.CORRELATION,
    AnalysisType.ANOMALY,
    AnalysisType.DRILL_DOWN,
    AnalysisType.OVERVIEW,
}


class ResolvedEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: str
    entity_id: Optional[str] = None
    resolution_method: str = "unknown"
    provenance: Provenance = Provenance.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    detail: str = ""


class RankingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_n: Optional[int] = None
    direction: str = "desc"
    scope_dimension: Optional[str] = None


class TimeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    granularity: Optional[str] = None
    range_start: Optional[str] = None
    range_end: Optional[str] = None
    relative_expression: Optional[str] = None
    provenance: Provenance = Provenance.UNKNOWN


class DrillDownSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_dimension: str
    child_dimension: str


class AnalysisIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_id: str
    user_goal: str
    analysis_type: Optional[AnalysisType] = None
    analysis_type_provenance: Provenance = Provenance.UNKNOWN
    metrics: list[ResolvedEntity] = Field(default_factory=list)
    dimensions: list[ResolvedEntity] = Field(default_factory=list)
    filters: dict[str, str] = Field(default_factory=dict)
    time: Optional[TimeSpec] = None
    comparison: Optional[ResolvedEntity] = None
    ranking: Optional[RankingSpec] = None
    drill_down: Optional[DrillDownSpec] = None
    expected_output: str = "analysis_report"
    constraints: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    ambiguity: list[str] = Field(default_factory=list)
    status: IntentStatus = IntentStatus.NEEDS_CLARIFICATION
    status_reasons: list[str] = Field(default_factory=list)

    def metric_ids(self) -> list[str]:
        return [m.entity_id for m in self.metrics if m.entity_id]

    def dimension_ids(self) -> list[str]:
        return [d.entity_id for d in self.dimensions if d.entity_id]


class IntentResolver:
    """Deterministic user-question -> AnalysisIntent resolution against a registry."""

    _TYPE_PATTERNS: list[tuple[AnalysisType, str]] = [
        (AnalysisType.FORECAST, r"(?:预测|预报|forecast)"),
        (AnalysisType.DRILL_DOWN, r"(?:下钻|钻取|drill[\s-]?down|按.*?再看|先按.*?再按)"),
        (AnalysisType.RANKING, r"(?:排名|排行|top\s*\d*|前\s*\d+\s*名|rank)"),
        (AnalysisType.TREND, r"(?:趋势|随时间|over\s+time|逐月|逐日|按月变化|trend)"),
        (AnalysisType.COMPARISON, r"(?:比较|对比|compare|区别|差异)"),
        (AnalysisType.ANOMALY, r"(?:异常|离群|anomal)"),
        (AnalysisType.CORRELATION, r"(?:相关|correlat)"),
        (AnalysisType.CONTRIBUTION, r"(?:贡献|占比分解|decompose|拆解)"),
        (AnalysisType.DISTRIBUTION, r"(?:分布|distribution)"),
    ]

    def __init__(
        self,
        registry: SemanticRegistry,
        *,
        compatibility: Optional[CompatibilityValidator] = None,
        known_columns: Optional[Sequence[str]] = None,
    ) -> None:
        self._registry = registry
        self._compatibility = compatibility or CompatibilityValidator(registry)
        self._known_columns = {c.lower() for c in (known_columns or [])}

    def resolve(self, question: str, *, intent_id: str) -> AnalysisIntent:
        text = question.strip()
        lowered = text.lower()
        reasons: list[str] = []
        assumptions: list[str] = []
        ambiguity: list[str] = []

        # --- analysis type ---
        analysis_type: Optional[AnalysisType] = None
        type_prov = Provenance.UNKNOWN
        for candidate, pattern in self._TYPE_PATTERNS:
            if re.search(pattern, lowered):
                analysis_type = candidate
                type_prov = Provenance.INFERRED
                break
        if analysis_type is None and re.search(
            r"每个|各|按|group|分组|总计|多少|合计|sum", lowered
        ):
            analysis_type = AnalysisType.AGGREGATION
            type_prov = Provenance.INFERRED

        # --- metrics (substring scan: Chinese has no word boundaries) ---
        metrics: list[ResolvedEntity] = []
        for term, metric, method in self._scan_registry_terms(text, kind="metric"):
            metrics.append(ResolvedEntity(
                term=term,
                entity_id=metric.metric_id,
                resolution_method=method,
                provenance=Provenance.EXPLICIT if method == "exact" else Provenance.RESOLVED,
                confidence=metric.confidence,
            ))

        # --- dimensions (substring scan) ---
        dimensions: list[ResolvedEntity] = []
        for term, dim, method in self._scan_registry_terms(text, kind="dimension"):
            dimensions.append(ResolvedEntity(
                term=term,
                entity_id=dim.dimension_id,
                resolution_method=method,
                provenance=Provenance.EXPLICIT if method == "exact" else Provenance.RESOLVED,
                confidence=dim.confidence,
            ))
        # group_by style dimension: "每个部门的..." -> longest registered term that
        # the captured segment starts with; else a known-column prefix candidate.
        group_match = re.search(r"(?:每个|各|按|group\s+by)\s*([\w\u4e00-\u9fff]{2,24})", lowered)
        if group_match:
            segment = group_match.group(1)
            prefix = self._longest_registry_prefix(segment, kind="dimension")
            if prefix is not None:
                term, dim = prefix
                if not any(d.entity_id == dim.dimension_id for d in dimensions):
                    dimensions.append(ResolvedEntity(
                        term=term,
                        entity_id=dim.dimension_id,
                        resolution_method="alias",
                        provenance=Provenance.RESOLVED,
                        confidence=dim.confidence,
                    ))
            else:
                column = self._longest_column_prefix(segment)
                if column is not None:
                    dimensions.append(ResolvedEntity(
                        term=column,
                        entity_id=None,
                        resolution_method="candidate_in_data",
                        provenance=Provenance.INFERRED,
                        confidence=0.3,
                        detail="column candidate exists in schema; register/verify to use",
                    ))
                    reasons.append("dimension candidate found in data; exploration needed to verify")
                else:
                    ambiguity.append(f"unresolved dimension term: {segment}")

        # --- ranking / top N ---
        ranking: Optional[RankingSpec] = None
        top_match = re.search(r"(?:top\s*(\d+)|前\s*(\d+)\s*名)", lowered)
        if top_match or (analysis_type is AnalysisType.RANKING):
            n = None
            if top_match:
                n = int(top_match.group(1) or top_match.group(2))
            direction = "desc"
            if re.search(r"(?:升序|asc(ending)?|最低|最后)", lowered):
                direction = "asc"
            scope = dimensions[0].entity_id if dimensions else None
            ranking = RankingSpec(top_n=n, direction=direction, scope_dimension=scope)
            if n is None:
                ranking = ranking.model_copy(update={"top_n": None})
                assumptions.append("ranking requested without explicit N; full ranking returned")
            if ranking.direction == "desc" and not re.search(
                r"(?:降序|desc(ending)?|最高|top|前|rank)", lowered
            ):
                assumptions.append("descending order assumed for ranking")

        # --- time ---
        time_spec: Optional[TimeSpec] = None
        granularity = None
        if re.search(r"逐月|按月|monthly|每月", lowered):
            granularity = "monthly"
        elif re.search(r"逐日|按日|daily|每天", lowered):
            granularity = "daily"
        elif re.search(r"季度|quarterly", lowered):
            granularity = "quarterly"
        elif re.search(r"年度|按年|yearly|每年", lowered):
            granularity = "yearly"
        rel_time = None
        rel_match = re.search(
            r"(最近|过去|last|past)\s*(\d+)\s*(?:个)?(天|日|周|月|季度|年)", lowered
        )
        if rel_match:
            rel_time = rel_match.group(0)
        if granularity or rel_time:
            time_spec = TimeSpec(
                granularity=granularity,
                relative_expression=rel_time,
                provenance=Provenance.EXPLICIT if rel_match else Provenance.INFERRED,
            )
        if analysis_type is AnalysisType.TREND and not time_spec:
            time_spec = TimeSpec(provenance=Provenance.UNKNOWN)
            reasons.append("trend analysis requires a time specification; explore the data for a time column")

        # --- drill-down ---
        drill: Optional[DrillDownSpec] = None
        drill_match = re.search(
            r"(?:先按|按)\s*([\w\u4e00-\u9fff]{2,8})\s*(?:再|然后|接着)\s*按?\s*"
            r"([\w\u4e00-\u9fff]{2,8}?)(?=(?:下钻|钻取|分析|的|情况|维度))",
            lowered,
        )
        if drill_match and analysis_type is AnalysisType.DRILL_DOWN:
            parent, child = drill_match.group(1), drill_match.group(2)
            parent_res, _ = self._registry.resolve_dimension(parent)
            child_res, _ = self._registry.resolve_dimension(child)
            if parent_res and child_res:
                drill = DrillDownSpec(parent_dimension=parent_res.dimension_id, child_dimension=child_res.dimension_id)
            else:
                ambiguity.append(
                    f"drill-down terms unresolved: {parent} -> {child}; relationship verification required"
                )

        constraints: list[str] = []
        # --- explicit filters: "X 大于 100" / "X = Y" ---
        filters: dict[str, str] = {}
        for match in re.finditer(r"([\w\u4e00-\u9fff]{2,20})\s*(?:大于|>)\s*(\d+(?:\.\d+)?)", lowered):
            filters[match.group(1)] = f">{match.group(2)}"
        for match in re.finditer(r"([\w\u4e00-\u9fff]{2,20})\s*(?:小于|<)\s*(\d+(?:\.\d+)?)", lowered):
            filters[match.group(1)] = f"<{match.group(2)}"

        # --- collection vs single object ---
        if re.search(r"所有|全部|每一个|每个|所有人员|全体", lowered):
            constraints.append("collection-scope analysis requested (not single-object)")

        # --- compatibility gating ---
        invalid_combos: list[str] = []
        for metric in metrics:
            if metric.entity_id is None:
                continue
            for dim in dimensions:
                if dim.entity_id is None:
                    continue
                report = self._compatibility.check(metric.entity_id, dim.entity_id)
                if report.status is CompatibilityStatus.INVALID:
                    invalid_combos.append(f"{metric.term} x {dim.term}: {report.reason}")

        # --- completeness ---
        if not metrics and analysis_type is not AnalysisType.OVERVIEW:
            reasons.append("no metric could be resolved from the question")
        unknown_metrics = [
            m.term for m in metrics if m.entity_id is None
        ]
        if analysis_type is not None and analysis_type not in SUPPORTED_ANALYSIS_TYPES:
            status = IntentStatus.UNSUPPORTED
            reasons.append(f"analysis type {analysis_type.value} is not supported by this runtime")
        elif invalid_combos:
            status = IntentStatus.UNSUPPORTED
            reasons.extend(f"invalid metric-dimension combination: {c}" for c in invalid_combos)
        elif not metrics and analysis_type is not AnalysisType.OVERVIEW:
            status = IntentStatus.NEEDS_CLARIFICATION
        elif ambiguity:
            status = IntentStatus.NEEDS_CLARIFICATION
        elif analysis_type is AnalysisType.TREND and (
            time_spec is None or time_spec.granularity is None
        ):
            status = IntentStatus.NEEDS_EXPLORATION
        elif any(d.entity_id is None for d in dimensions) or (
            analysis_type is AnalysisType.DRILL_DOWN and drill is None
        ):
            status = IntentStatus.NEEDS_EXPLORATION
        elif assumptions:
            status = IntentStatus.READY_WITH_ASSUMPTIONS
        else:
            status = IntentStatus.READY

        return AnalysisIntent(
            intent_id=intent_id,
            user_goal=text,
            analysis_type=analysis_type,
            analysis_type_provenance=type_prov,
            metrics=metrics,
            dimensions=dimensions,
            filters=filters,
            time=time_spec,
            ranking=ranking,
            drill_down=drill,
            constraints=constraints,
            assumptions=assumptions,
            ambiguity=ambiguity,
            status=status,
            status_reasons=reasons,
        )

    def _scan_registry_terms(self, text: str, *, kind: str):
        """Scan for known registry terms as substrings of the question.

        Chinese questions have no word boundaries, so token-exact matching misses
        aliases like 收入 inside 上个月的收入是多少. Deterministic: longest-match
        first, each registry entity resolved at most once per question.
        """
        lowered = text.casefold()
        entries: list[tuple[str, object]] = []
        if kind == "metric":
            for m in self._all_metrics():
                entries.append((m.name, m))
                for alias in m.aliases:
                    entries.append((alias, m))
        else:
            for d in self._all_dimensions():
                entries.append((d.name, d))
                for alias in d.aliases:
                    entries.append((alias, d))
        seen_ids: set[int] = set()
        matches = []
        for term, entity in sorted(entries, key=lambda item: len(item[0]), reverse=True):
            if id(entity) in seen_ids:
                continue
            if term and len(term) >= 2 and term.casefold() in lowered:
                seen_ids.add(id(entity))
                canonical = getattr(entity, "name", "")
                method = "exact" if term.casefold() == canonical.casefold() else "alias"
                matches.append((lowered.index(term.casefold()), term, entity, method))
        # preserve the order in which terms appear in the user question
        matches.sort(key=lambda item: item[0])
        return [(term, entity, method) for _, term, entity, method in matches]

    def _longest_registry_prefix(self, segment: str, *, kind: str):
        """Longest registered dimension term that the segment starts with."""
        lowered = segment.casefold()
        best: tuple[str, object] | None = None
        if kind == "dimension":
            for d in self._all_dimensions():
                for term in [d.name, *d.aliases]:
                    if len(term) >= 2 and lowered.startswith(term.casefold()):
                        if best is None or len(term) > len(best[0]):
                            best = (term, d)
        return best

    def _longest_column_prefix(self, segment: str):
        if not self._known_columns:
            return None
        for column in sorted(self._known_columns, key=len, reverse=True):
            if column and segment.lower().startswith(column.lower()):
                return column
        return None

    def _all_metrics(self):
        return list(getattr(self._registry, "_metrics", {}).values())

    def _all_dimensions(self):
        return list(getattr(self._registry, "_dimensions", {}).values())
