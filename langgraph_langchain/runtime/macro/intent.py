"""MacroIntentResolver + Runtime V2 integration (B9.4).

Resolves natural-language macro questions into indicator/country/period
intent, acquires data through the platform-level AcquisitionService, builds
template plans, and hands them to the existing Runtime V2 validation and
execution chain.  The Macro Pack never calls HTTP adapters directly and never
bypasses plan validators, Scheduler, Executor, Evidence or Report.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from langgraph_langchain.data.acquisition import AcquisitionService
import pandas as pd

from langgraph_langchain.data.adapters.base import FetchRequest
from langgraph_langchain.runtime.general_planner import GeneralAnalysisPlan
from langgraph_langchain.runtime.macro.comparison import (
    ComparisonResolution,
    ComparisonSpec,
    CrossCountryComparator,
)
from langgraph_langchain.runtime.macro.models import MacroIndicatorCatalog
from langgraph_langchain.runtime.macro.registry import (
    IndicatorAssetKey,
    IndicatorAssetRegistry,
    resolve_indicator_asset_key,
)
from langgraph_langchain.runtime.macro.templates import (
    MacroTemplateError,
    ResolvedIndicatorAsset,
    build_comparison_plan,
    build_template_plan,
    check_data_sufficiency,
    get_template,
)

__all__ = [
    "MacroIntent",
    "MacroIntentResolver",
    "MacroDataRequirements",
    "MacroRuntimeIntegration",
]

_CONCEPT_KEYWORDS = {
    "gdp": ("gdp", "国内生产总值", "经济总量", "产出"),
    "cpi": ("cpi", "通胀", "物价", "消费者价格", "通货膨胀"),
    "ppi": ("ppi", "生产者价格", "工业品价格"),
    "nonfarm_payrolls": ("非农", "payrolls", "就业"),
    "unemployment_rate": ("失业率", "unemployment"),
    "policy_rate": ("利率", "政策利率", "加息", "降息", "联邦基金", "货币政策", "实际利率"),
    "exports": ("出口", "exports"),
    "imports": ("进口", "imports"),
}

_COUNTRY_KEYWORDS = {
    "us": ("美国", "us", "united states", "美联储", "fed"),
    "cn": ("中国", "cn", "china", "我国", "人民银行", "国内"),
}

_PERIOD_PATTERNS = [
    (re.compile(r"近十年|最近十年|past ten years", re.IGNORECASE), 10),
    (re.compile(r"近五年|最近五年|past five years", re.IGNORECASE), 5),
    (re.compile(r"近三年|最近三年|past (?:three|3) years", re.IGNORECASE), 3),
    (re.compile(r"近两年|最近两年|past two years", re.IGNORECASE), 2),
    (re.compile(r"近一[年]|past year|过去一年", re.IGNORECASE), 1),
]


@dataclass
class MacroIntent:
    question: str
    concepts: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    years: int = 5
    analysis_goal: str = "trend"
    ambiguities: list[str] = field(default_factory=list)


class MacroDataRequirements:
    """Structured data requirements for the platform Acquisition Orchestrator."""

    def __init__(self) -> None:
        self.requests: list[FetchRequest] = []
        self.indicators: list[Any] = []
        self.unresolved: list[str] = []


class MacroIntentResolver:
    """Resolve natural-language macro questions into structured intent."""

    def __init__(self, catalog: MacroIndicatorCatalog) -> None:
        self.catalog = catalog

    def resolve(self, question: str) -> MacroIntent:
        lowered = question.lower()
        concepts = [
            concept
            for concept, keywords in _CONCEPT_KEYWORDS.items()
            if any(keyword in lowered for keyword in keywords)
        ]
        countries = [
            country
            for country, keywords in _COUNTRY_KEYWORDS.items()
            if any(keyword in lowered for keyword in keywords)
        ]
        # "中美"/"美中" implies comparison across both countries (B9 §9.2).
        if any(marker in question for marker in ("中美", "美中")):
            for country_code in ("us", "cn"):
                if country_code not in countries:
                    countries.append(country_code)
        years = 5
        for pattern, value in _PERIOD_PATTERNS:
            if pattern.search(lowered):
                years = value
                break
        return MacroIntent(
            question=question,
            concepts=concepts,
            countries=countries,
            years=years,
            analysis_goal="comparison" if any(k in lowered for k in ("对比", "比较", "compare")) else "trend",
        )


class MacroRuntimeIntegration:
    """Bridges macro intent to data acquisition, templates and Runtime V2."""

    def __init__(
        self,
        *,
        catalog: MacroIndicatorCatalog,
        acquisition: AcquisitionService,
    ) -> None:
        self.catalog = catalog
        self.acquisition = acquisition
        self.resolver = MacroIntentResolver(catalog)
        self.runtime = acquisition.runtime

    def build_data_requirements(self, intent: MacroIntent) -> MacroDataRequirements:
        requirements = MacroDataRequirements()
        if not intent.countries:
            requirements.unresolved.append("SEMANTIC_COUNTRY_UNCLEAR")
            return requirements
        for concept in intent.concepts:
            matches = self.catalog.resolve(concept=concept)
            relevant = [i for i in matches if i.country in intent.countries]
            if not relevant:
                requirements.unresolved.append(f"SEMANTIC_UNKNOWN_CONCEPT:{concept}")
                continue
            for indicator in relevant:
                # B9 §5.9: only acquire through registered adapters.
                try:
                    self.acquisition.registry.get(indicator.source_id)
                except Exception:
                    requirements.unresolved.append(
                        f"SOURCE_NOT_REGISTERED:{indicator.source_id}:{indicator.dataset_id}"
                    )
                    continue
                from datetime import datetime, timedelta, timezone

                now = datetime.now(timezone.utc)
                end_date = now.strftime("%Y-%m-%d")
                start_date = (now - timedelta(days=intent.years * 365)).strftime("%Y-%m-%d")
                requirements.requests.append(FetchRequest(
                    dataset_id=indicator.dataset_id,
                    start=start_date,
                    end=end_date,
                    parameters=dict(indicator.series_parameters),
                ))
                requirements.indicators.append(indicator)
        return requirements

    def build_aligned_asset(
        self,
        comparison: ComparisonResolution,
        *,
        aligned_frame: pd.DataFrame,
        registry: Optional[IndicatorAssetRegistry] = None,
    ) -> Optional[str]:
        """Register the aligned cross-country comparison frame (B9.8.2 §4.4).

        Called from ``run()`` on the real production path when a comparison
        intent produced at least one comparable concept.  The aligned frame
        is deterministic data preparation (per-country YoY rates aligned on
        real common periods), never analysis execution.  Returns the aligned
        asset_id, or None when the frame is empty.
        """
        if aligned_frame is None or aligned_frame.empty:
            return None
        aligned_path = self.runtime.workspace_dir / f"aligned_{uuid.uuid4().hex[:8]}.parquet"
        aligned_frame.reset_index().to_parquet(aligned_path, index=False)
        from langgraph_langchain.data.assets import DataAsset, SchemaSnapshot

        legs = comparison.legs
        input_versions = []
        if registry is not None:
            for leg in legs:
                try:
                    input_versions.append(registry.resolve(leg.key).data_version)
                except KeyError:
                    input_versions.append("")
        disclosures = [
            item
            for comparison_item in comparison.comparisons
            for item in comparison_item.disclosures
            if comparison_item.comparable
        ]
        asset = DataAsset(
            name=f"macro_aligned_{uuid.uuid4().hex[:8]}",
            source_type="macro_aligned",
            content_hash=None,  # computed by DataAsset model
            schema_snapshot=SchemaSnapshot.from_dataframe(aligned_frame.reset_index()),
            source_metadata={
                "acquisition": "macro_context_builder",
                "alignment_method": "yoy_rate_common_period",
                "input_asset_ids": [leg.asset_id for leg in legs],
                "indicator_ids": [leg.indicator.indicator_id for leg in legs],
                "input_data_versions": input_versions,
                "comparison_type": comparison.spec.comparison_type,
                "comparison_method": comparison.spec.comparison_method,
                "alignment_policy": comparison.spec.alignment_policy,
                "comparable_concepts": comparison.comparable_concepts,
                "statistics_scope": "aligned_comparison",
                "disclosures": disclosures,
            },
        )
        self.runtime.assets[asset.asset_id] = asset
        self.runtime._persist()
        return asset.asset_id

    def _select_template(self, intent: MacroIntent) -> str:
        # B9.8.2 §4.1: a cross-country comparison is decided by the explicit
        # comparison goal AND >= 2 requested countries — never by concept
        # count alone ("中美GDP对比" is 1 concept + 2 countries).  Merely
        # mentioning two countries without comparison words keeps the joint
        # multi-indicator path.
        is_comparison = (
            intent.analysis_goal == "comparison" and len(intent.countries) >= 2
        )
        if is_comparison:
            return "cross_country_comparison"
        if len(intent.concepts) >= 2:
            # A concept set fully covered by a specific template (its required
            # concepts match) wins over the generic multi-indicator template
            # — e.g. CPI+PPI → inflation, CPI+policy_rate → monetary_policy.
            requested = set(intent.concepts)
            for template_id in (
                "gdp_growth", "inflation", "employment",
                "monetary_policy", "trade",
            ):
                template = get_template(template_id)
                covered = set(template.required_concepts) | set(template.optional_concepts)
                if (
                    requested <= covered
                    and requested & set(template.required_concepts)
                ):
                    return template_id
            return "multi_indicator"
        mapping = {
            "gdp": "gdp_growth", "cpi": "inflation", "ppi": "inflation",
            "nonfarm_payrolls": "employment", "unemployment_rate": "employment",
            "policy_rate": "monetary_policy", "exports": "trade", "imports": "trade",
        }
        return mapping.get(intent.concepts[0] if intent.concepts else "", "multi_indicator")

    def run(self, question: str, *, refresh: bool = False) -> dict[str, Any]:
        """Full macro analysis chain: intent → acquisition → template → plan."""
        intent = self.resolver.resolve(question)
        if intent.ambiguities or not intent.concepts:
            return {"status": "needs_clarification", "intent": intent, "plan": None,
                    "missing": list(intent.ambiguities) or ["SEMANTIC_NO_CONCEPT_IDENTIFIED"],
                    "sufficiency": "blocked"}

        requirements = self.build_data_requirements(intent)
        if not requirements.requests:
            # No requests at all: every concept was unresolvable.
            return {"status": "needs_clarification", "intent": intent, "plan": None,
                    "missing": list(requirements.unresolved) or ["SEMANTIC_NO_DATA_AVAILABLE"],
                    "sufficiency": "blocked"}
        # Partial unresolved sources are not blockers: proceed with what is
        # available and disclose the gaps in the report limitations.

        template_id = self._select_template(intent)
        template = get_template(template_id)
        registry = IndicatorAssetRegistry()
        observations: dict[str, ResolvedIndicatorAsset] = {}
        for request, indicator in zip(requirements.requests, requirements.indicators):
            outcome = self.acquisition.acquire(request, refresh=refresh)
            resolved = self.runtime.asset_resolver.resolve(outcome.asset.asset_id)
            # B9.8.2 §3.2: identity resolution through the single public entry.
            key = resolve_indicator_asset_key(indicator)
            registry.register(key, outcome.asset.asset_id,
                              observation_count=len(resolved.dataframe),
                              data_version=outcome.asset.content_hash or "")
            observations[indicator.indicator_id] = ResolvedIndicatorAsset(
                indicator=indicator, asset_id=outcome.asset.asset_id,
                observation_count=len(resolved.dataframe),
            )

        sufficiency, missing = check_data_sufficiency(
            template, observations,
            requested_countries=tuple(intent.countries),
        )
        if sufficiency == "blocked":
            return {"status": "needs_clarification", "intent": intent, "plan": None,
                    "missing": missing, "sufficiency": "blocked"}

        # B9.8.2 §4.4: cross-country comparison runs on the production path —
        # per-(concept, country) legs, semantic comparability, frequency
        # normalization, real-period alignment, registered aligned asset.
        comparison: Optional[ComparisonResolution] = None
        aligned_asset_id: Optional[str] = None
        if intent.analysis_goal == "comparison" and len(intent.countries) >= 2:
            spec = ComparisonSpec(
                countries=tuple(intent.countries),
                concepts=tuple(intent.concepts),
            )
            comparator = CrossCountryComparator(
                observations,
                resolve_dataframe=lambda asset_id: (
                    self.runtime.asset_resolver.resolve(asset_id).dataframe
                ),
            )
            comparison = comparator.resolve(spec)
            missing.extend(comparison.missing)
            if comparison.comparable_concepts:
                aligned_frame = comparator.build_aligned_frame(comparison)
                aligned_asset_id = self.build_aligned_asset(
                    comparison,
                    aligned_frame=aligned_frame,
                    registry=registry,
                )

        try:
            if comparison is not None:
                plan = build_comparison_plan(
                    template, observations, question=question,
                    comparison=comparison, aligned_asset_id=aligned_asset_id,
                    requested_countries=tuple(intent.countries),
                )
            else:
                plan = build_template_plan(
                    template, observations, question=question,
                    requested_countries=tuple(intent.countries),
                )
        except MacroTemplateError as exc:
            return {"status": "planning_failed", "intent": intent, "plan": None,
                    "missing": [str(exc)], "sufficiency": sufficiency}

        return {
            "status": "planned", "intent": intent, "plan": plan,
            "missing": missing, "sufficiency": sufficiency,
            "template_id": template.template_id,
            "comparison": self._comparison_summary(comparison, aligned_asset_id),
        }

    @staticmethod
    def _comparison_summary(
        comparison: Optional[ComparisonResolution],
        aligned_asset_id: Optional[str],
    ) -> Optional[dict[str, Any]]:
        if comparison is None:
            return None
        return {
            "comparison_type": comparison.spec.comparison_type,
            "countries": list(comparison.spec.countries),
            "concepts": list(comparison.spec.concepts),
            "comparable_concepts": comparison.comparable_concepts,
            "missing": list(comparison.missing),
            "aligned_asset_id": aligned_asset_id,
            "per_concept": [
                {
                    "concept": item.concept,
                    "countries": [leg.country for leg in item.legs],
                    "comparable": item.comparable,
                    "comparison_frequency": item.comparison_frequency,
                    "aligned_periods": item.aligned_periods,
                    "rate_columns": item.rate_columns,
                    "incompatibilities": item.incompatibilities,
                    "disclosures": item.disclosures,
                }
                for item in comparison.comparisons
            ],
        }
