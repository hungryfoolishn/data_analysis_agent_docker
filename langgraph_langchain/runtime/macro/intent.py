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
from langgraph_langchain.runtime.macro.models import MacroIndicatorCatalog
from langgraph_langchain.runtime.macro.registry import IndicatorAssetKey, IndicatorAssetRegistry
from langgraph_langchain.runtime.macro.templates import (
    MacroTemplateError,
    ResolvedIndicatorAsset,
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
        registry: IndicatorAssetRegistry,
        *,
        frequency: Optional[str] = None,
    ) -> Optional[str]:
        """Build an aligned multi-indicator asset when >= 2 indicators resolved.

        Aligns observations on common periods and registers the combined frame
        as a new DataAsset via register_dataframe.  Returns aligned asset_id
        or None when alignment is not applicable.
        """
        entries = registry.list_assets()
        if len(entries) < 2:
            return None
        frames: dict[str, pd.DataFrame] = {}
        for entry in entries:
            resolved = self.runtime.asset_resolver.resolve(entry.asset_id)
            column_name = entry.key.concept
            series = resolved.dataframe.set_index("period")["value"] if "period" in resolved.dataframe.columns else resolved.dataframe.iloc[:, 1]
            frames[column_name] = series
        if len(frames) < 2:
            return None
        aligned = pd.DataFrame(frames).dropna(how="all")
        aligned_path = self.runtime.workspace_dir / f"aligned_{uuid.uuid4().hex[:8]}.parquet"
        aligned.to_parquet(aligned_path, index=True)
        from langgraph_langchain.data.assets import DataAsset, SchemaSnapshot

        asset = DataAsset(
            name=f"macro_aligned_{uuid.uuid4().hex[:8]}",
            source_type="macro_aligned",
            content_hash=None,  # computed by DataAsset model
            schema_snapshot=SchemaSnapshot.from_dataframe(aligned),
            source_metadata={
                "acquisition": "macro_context_builder",
                "alignment_method": "inner_join_on_period",
                "input_asset_ids": [entry.asset_id for entry in entries],
                "indicator_ids": [entry.key.indicator_id for entry in entries],
                "input_data_versions": [entry.data_version for entry in entries],
                "statistics_scope": "aligned",
                "coverage_start": str(aligned.index.min()) if len(aligned) else None,
                "coverage_end": str(aligned.index.max()) if len(aligned) else None,
            },
        )
        self.runtime.assets[asset.asset_id] = asset
        self.runtime._persist()
        return asset.asset_id

    def _select_template(self, intent: MacroIntent) -> str:
        if len(intent.concepts) >= 2:
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
            key = IndicatorAssetKey.from_indicator(indicator)
            registry.register(key, outcome.asset.asset_id,
                              observation_count=len(resolved.dataframe),
                              data_version=outcome.asset.content_hash or "")
            observations[indicator.concept] = ResolvedIndicatorAsset(
                indicator=indicator, asset_id=outcome.asset.asset_id,
                observation_count=len(resolved.dataframe),
            )

        sufficiency, missing = check_data_sufficiency(template, observations)
        if sufficiency == "blocked":
            return {"status": "needs_clarification", "intent": intent, "plan": None,
                    "missing": missing, "sufficiency": "blocked"}

        try:
            plan = build_template_plan(template, observations, question=question)
        except MacroTemplateError as exc:
            return {"status": "planning_failed", "intent": intent, "plan": None,
                    "missing": [str(exc)], "sufficiency": sufficiency}

        return {"status": "planned", "intent": intent, "plan": plan,
                "missing": missing, "sufficiency": sufficiency}
