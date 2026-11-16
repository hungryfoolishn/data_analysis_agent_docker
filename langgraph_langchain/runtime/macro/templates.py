"""Six macro analysis templates (B9.3).

Templates only GENERATE GeneralAnalysisPlan v2 plans; they never execute
analysis.  All emitted operations are whitelisted (``macro_transform``,
``data_quality``, ``trend``, ``correlation``, ``summarize``,
``period_comparison``), and cross-step data flow uses the B11.2.1/B4.2.1
``{step_id}.{output_name}`` output-reference contract.

Multi-indicator joint analysis consumes an *aligned asset* produced by the
Macro Context Builder (``build_aligned_asset`` in planner.py) — the alignment
is deterministic data preparation, not analysis execution.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from langgraph_langchain.runtime.general_planner import (
    GeneralAnalysisPlan,
    GeneralPlanStep,
)
from langgraph_langchain.runtime.macro.models import (
    MacroAnalysisTemplate,
    MacroEvidenceRequirement,
    MacroIndicator,
    ResolvedIndicatorAsset,
)
from langgraph_langchain.runtime.macro.registry import (
    IndicatorAssetKey,
    resolve_indicator_asset_key,
)


class MacroTemplateError(ValueError):
    """Raised when a template cannot produce a valid plan."""


def _step_id(tag: str) -> str:
    return f"step_{uuid.uuid5(uuid.NAMESPACE_URL, f'macro:{tag}').hex}"


def _time_column_for(indicator: MacroIndicator) -> str:
    """Physical time column of a registered adapter asset (B9.8.2 E2E fix):
    FRED normalizes to ``date``; NBS/BLS/BEA normalize to ``period``."""
    return "date" if indicator.source_id == "fred" else "period"


MACRO_TEMPLATES: dict[str, MacroAnalysisTemplate] = {
    "gdp_growth": MacroAnalysisTemplate(
        template_id="gdp_growth",
        intent="Analyze GDP growth (level, YoY, trend).",
        required_concepts=["gdp"],
        optional_concepts=[],
        countries=("us", "cn"),
        default_transforms=["yoy", "index_change"],
        analysis_steps=["data_quality", "trend", "period_comparison"],
        evidence_requirements=MacroEvidenceRequirement(
            min_observations=8,
            required_disclosures=("unit", "price_basis", "release_lag"),
        ),
        report_sections=["growth_overview", "trend", "volatility", "limitations"],
        limitations=[
            "cumulative series revisions cascade into derived single periods",
            "real vs nominal GDP must not be mixed without deflation",
        ],
        data_sufficiency_policy="partially_ready",
    ),
    "inflation": MacroAnalysisTemplate(
        template_id="inflation",
        intent="Analyze CPI/PPI inflation (YoY, MoM, level changes).",
        required_concepts=["cpi"],
        optional_concepts=["ppi"],
        countries=("us", "cn"),
        default_transforms=["yoy", "mom"],
        analysis_steps=["data_quality", "period_comparison"],
        evidence_requirements=MacroEvidenceRequirement(
            min_observations=13,
            required_disclosures=("unit", "base_period", "seasonal_adjustment"),
        ),
        report_sections=["yoy", "mom", "cpi_ppi_divergence", "limitations"],
        limitations=[
            "index levels of different countries are not comparable (different base periods)",
            "published YoY rates must not be re-diffed",
        ],
        data_sufficiency_policy="partially_ready",
    ),
    "employment": MacroAnalysisTemplate(
        template_id="employment",
        intent="Analyze employment market (levels, changes, trends).",
        required_concepts=[],
        optional_concepts=["nonfarm_payrolls", "unemployment_rate", "unemployment_level_bls"],
        any_of_concepts=[["nonfarm_payrolls"], ["unemployment_rate"], ["unemployment_level_bls"]],
        countries=("us",),
        default_transforms=["mom", "rolling_average"],
        analysis_steps=["data_quality", "period_comparison"],
        evidence_requirements=MacroEvidenceRequirement(
            min_observations=6,
            required_disclosures=("unit", "seasonal_adjustment"),
        ),
        report_sections=["level", "change", "trend", "limitations"],
        limitations=[
            "single-month moves are not trend conclusions",
            "different employment statistics must not be mixed",
        ],
        data_sufficiency_policy="partially_ready",
    ),
    "monetary_policy": MacroAnalysisTemplate(
        template_id="monetary_policy",
        intent="Analyze policy rates and the monetary environment.",
        required_concepts=["policy_rate"],
        optional_concepts=["cpi"],
        countries=("us", "cn"),
        default_transforms=["period_comparison"],
        analysis_steps=["data_quality", "period_comparison"],
        evidence_requirements=MacroEvidenceRequirement(
            min_observations=6,
            required_disclosures=("unit", "frequency"),
        ),
        report_sections=["policy_path", "real_rate_context", "limitations"],
        limitations=[
            "correlation does not establish policy causality",
            "real rate is an ex-post approximation when using CPI",
        ],
        data_sufficiency_policy="partially_ready",
    ),
    "trade": MacroAnalysisTemplate(
        template_id="trade",
        intent="Analyze trade flows (exports, imports, balance).",
        required_concepts=[],
        optional_concepts=["exports", "imports"],
        any_of_concepts=[["exports"], ["imports"], ["exports", "imports"]],
        countries=("us",),
        default_transforms=["yoy", "rolling_average"],
        analysis_steps=["data_quality", "trend", "period_comparison"],
        evidence_requirements=MacroEvidenceRequirement(
            min_observations=13,
            required_disclosures=("unit", "frequency"),
        ),
        report_sections=["exports", "imports", "balance", "limitations"],
        limitations=[
            "single-side trade data cannot produce a bilateral balance",
            "currency effects are not adjusted",
        ],
        data_sufficiency_policy="partially_ready",
    ),
    "multi_indicator": MacroAnalysisTemplate(
        template_id="multi_indicator",
        intent="Joint multi-indicator macro analysis (>= 2 concepts).",
        required_concepts=[],
        optional_concepts=["gdp", "cpi", "ppi", "nonfarm_payrolls", "policy_rate"],
        countries=("us", "cn"),
        default_transforms=["yoy", "rolling_average"],
        analysis_steps=["data_quality", "correlation", "period_comparison"],
        evidence_requirements=MacroEvidenceRequirement(
            min_observations=13,
            required_disclosures=("unit", "frequency", "seasonal_adjustment"),
        ),
        report_sections=["per_indicator", "correlation", "limitations"],
        limitations=[
            "different frequencies/units must not be merged without disclosure",
            "correlation is not causality",
        ],
        data_sufficiency_policy="partially_ready",
    ),
    "cross_country_comparison": MacroAnalysisTemplate(
        template_id="cross_country_comparison",
        intent=(
            "Compare the same macro concept across countries on aligned common "
            "periods with an explicitly disclosed method (B9.8.2 §4)."
        ),
        required_concepts=[],
        optional_concepts=[
            "gdp", "cpi", "ppi", "personal_income", "unemployment_rate",
            "unemployment_level_bls", "nonfarm_payrolls", "policy_rate",
            "exports", "imports",
        ],
        countries=("us", "cn"),
        default_transforms=["yoy", "index_change"],
        analysis_steps=["data_quality", "correlation", "period_comparison"],
        evidence_requirements=MacroEvidenceRequirement(
            min_observations=2,
            required_disclosures=("unit", "frequency", "base_period", "seasonal_adjustment"),
        ),
        report_sections=[
            "comparison_method", "per_country", "aligned_comparison", "limitations",
        ],
        limitations=[
            "index levels of different countries are not comparable (different base periods); compare yoy rates instead",
            "real vs nominal growth rates are not directly comparable without deflation",
            "sub-annual rates are aggregated to the coarsest common frequency by calendar-period mean before alignment (disclosed)",
            "correlation is not causality",
        ],
        data_sufficiency_policy="partially_ready",
    ),
}


def get_template(template_id: str) -> MacroAnalysisTemplate:
    if template_id not in MACRO_TEMPLATES:
        raise MacroTemplateError(f"Unknown macro template: {template_id!r}")
    return MACRO_TEMPLATES[template_id]


def list_templates() -> list[str]:
    return sorted(MACRO_TEMPLATES)


# ResolvedIndicatorAsset is imported from models (shared with the
# cross-country comparison pipeline); re-exported here for compatibility.


@dataclass(frozen=True)
class TemplateRequirementResolution:
    """Unified template-requirement resolution (B9.8.2 §3.4).

    ONE resolution result shared by Data Sufficiency and Plan Builder — the
    two consumers must never re-query the registry independently and diverge.
    All identity handling goes through IndicatorAssetKey (via
    ``resolve_indicator_asset_key``); the ``entries`` dict is indexed by
    ``indicator_id`` which is unique and stable inside the catalog, while
    ``concept_index``/``concept_countries`` provide the multi-result,
    ambiguity-explicit concept convenience view (B9.8.2 §3.3).

    Requirement semantics (B9.8.2 §3.5):
    - ``required_concepts``: ALL must resolve;
    - ``optional_concepts``: included when available, never blocking;
    - ``any_of_concepts``: AND between groups, OR inside a group;
    - ``requested_countries``: bookkeeping per (concept, country); a concept
      is marked ``country_incomplete`` when the request spans multiple
      countries but some requested country has no data for it (the
      cross-country comparison pipeline consumes this in B9.8.2 stage 2).
    """

    template_id: str
    required_concepts: tuple[str, ...]
    optional_concepts: tuple[str, ...]
    entries: dict[str, ResolvedIndicatorAsset]
    keys: dict[str, IndicatorAssetKey]
    concept_index: dict[str, tuple[str, ...]]
    concept_countries: dict[str, dict[str, tuple[str, ...]]]
    requested_countries: tuple[str, ...]
    missing_required: tuple[str, ...]
    unsatisfied_any_of: tuple[tuple[str, ...], ...]
    country_incomplete: tuple[str, ...]
    thin_observations: tuple[str, ...]
    sufficiency: str
    missing: tuple[str, ...]

    def primary_indicator_id(self) -> str:
        """Deterministic primary asset: first required concept's sorted
        indicator, else first optional concept's, else sorted first (fixes
        the concept-keyed dict lookup that silently fell back to insertion
        order — B9.8.2 D7)."""
        candidates: list[str] = []
        for concept in list(self.required_concepts) + list(self.optional_concepts):
            candidates.extend(self.concept_index.get(concept, ()))
        for indicator_id in sorted(set(candidates)):
            if indicator_id in self.entries:
                return indicator_id
        return sorted(self.entries)[0]


def resolve_template_requirements(
    template: MacroAnalysisTemplate,
    resolved: dict[str, ResolvedIndicatorAsset],
    *,
    requested_countries: Sequence[str] = (),
) -> TemplateRequirementResolution:
    """Resolve template requirements against resolved indicator assets.

    The single source of truth consumed by ``check_data_sufficiency`` and
    ``build_template_plan`` (B9.8.2 §3.4).
    """
    keys: dict[str, IndicatorAssetKey] = {
        indicator_id: resolve_indicator_asset_key(item.indicator)
        for indicator_id, item in resolved.items()
    }
    concept_index: dict[str, list[str]] = {}
    concept_countries: dict[str, dict[str, list[str]]] = {}
    for indicator_id, item in sorted(resolved.items()):
        concept = item.indicator.concept
        concept_index.setdefault(concept, []).append(indicator_id)
        country_map = concept_countries.setdefault(concept, {})
        country_map.setdefault(item.indicator.country, []).append(indicator_id)

    frozen_index = {
        concept: tuple(sorted(ids)) for concept, ids in concept_index.items()
    }
    frozen_countries = {
        concept: {
            country: tuple(sorted(ids)) for country, ids in country_map.items()
        }
        for concept, country_map in concept_countries.items()
    }

    missing_required = tuple(
        concept
        for concept in template.required_concepts
        if concept not in frozen_index
    )
    # any_of semantics (B9.8.4): at least ONE group must be fully satisfied
    # (OR between groups, AND inside a group).  Individual unmet groups are
    # recorded for diagnostics but only block when NO group is satisfied.
    unsatisfied_any_of: list[tuple[str, ...]] = []
    if template.any_of_concepts:
        for group in template.any_of_concepts:
            if not all(concept in frozen_index for concept in group):
                unsatisfied_any_of.append(tuple(group))
        if len(unsatisfied_any_of) == len(template.any_of_concepts):
            # No group satisfied → requirement violated.
            pass
        else:
            unsatisfied_any_of = []

    # Multi-country requests: a concept whose data misses some requested
    # country is recorded as country_incomplete (comparison decides blocking).
    countries = tuple(dict.fromkeys(requested_countries))
    country_incomplete: list[str] = []
    if len(countries) >= 2:
        for concept, country_map in frozen_countries.items():
            absent = [c for c in countries if c not in country_map]
            if absent:
                country_incomplete.append(f"{concept}:{'+'.join(absent)}")

    thin: list[str] = []
    min_obs = template.evidence_requirements.min_observations
    for indicator_id, item in sorted(resolved.items()):
        if item.observation_count < min_obs:
            thin.append(indicator_id)

    missing: list[str] = []
    if missing_required:
        missing.append(f"missing required indicators: {list(missing_required)}")
    if unsatisfied_any_of:
        all_needed = sorted({c for group in unsatisfied_any_of for c in group})
        missing.append(f"requires at least one of: {all_needed}")
    if thin:
        missing.append(
            f"observation count below {min_obs}: {sorted(thin)}"
        )
    if country_incomplete:
        missing.append(
            f"no data for requested countries: {sorted(country_incomplete)}"
        )

    if missing_required or unsatisfied_any_of:
        sufficiency = "blocked"
    elif thin or country_incomplete:
        sufficiency = "partially_ready"
    else:
        sufficiency = "ready"

    return TemplateRequirementResolution(
        template_id=template.template_id,
        required_concepts=tuple(template.required_concepts),
        optional_concepts=tuple(template.optional_concepts),
        entries=dict(resolved),
        keys=keys,
        concept_index=frozen_index,
        concept_countries=frozen_countries,
        requested_countries=countries,
        missing_required=missing_required,
        unsatisfied_any_of=tuple(unsatisfied_any_of),
        country_incomplete=tuple(country_incomplete),
        thin_observations=tuple(sorted(thin)),
        sufficiency=sufficiency,
        missing=tuple(missing),
    )


def check_data_sufficiency(
    template: MacroAnalysisTemplate,
    resolved: dict[str, ResolvedIndicatorAsset],
    *,
    requested_countries: Sequence[str] = (),
) -> tuple[str, list[str]]:
    """Frozen data-sufficiency policy (B9 §5.8, B9.8.4 any_of, B9.8.2 §3.4).

    Delegates to the shared ``resolve_template_requirements`` so sufficiency
    and plan building can never diverge.
    """
    resolution = resolve_template_requirements(
        template, resolved, requested_countries=requested_countries
    )
    return resolution.sufficiency, list(resolution.missing)


def build_template_plan(
    template: MacroAnalysisTemplate,
    resolved: dict[str, ResolvedIndicatorAsset],
    *,
    question: str,
    aligned_asset_id: Optional[str] = None,
    requested_countries: Sequence[str] = (),
) -> GeneralAnalysisPlan:
    """Build a GeneralAnalysisPlan v2 from a template and resolved assets.

    Consumes the SAME ``resolve_template_requirements`` result as
    ``check_data_sufficiency`` (B9.8.2 §3.4); primary-asset selection is
    deterministic via ``primary_indicator_id()`` (D7 fix).

    Steps (all whitelisted operations):
      1. ``data_quality`` on the primary indicator asset;
      2. one ``macro_transform`` step per resolved indicator (first supported
         transform from the template defaults), each with its own
         ``output_name`` and downstream references;
      3. ``correlation`` on the aligned asset (multi-indicator) or ``trend`` on
         the primary transform output (single indicator);
      4. ``summarize`` on the aligned/primary asset.
    """
    resolution = resolve_template_requirements(
        template, resolved, requested_countries=requested_countries
    )
    if resolution.sufficiency == "blocked":
        raise MacroTemplateError(
            f"Template '{template.template_id}' blocked: "
            f"{'; '.join(resolution.missing)}"
        )

    # B9.8.4: filter by concept extracted from indicator objects, not dict keys.
    # The resolved dict is keyed by indicator_id (B9.8.3).
    used = {
        indicator_id: resolution.entries[indicator_id]
        for indicator_id in sorted(resolution.entries)
        if resolution.entries[indicator_id].indicator.concept
        in set(template.required_concepts) | set(template.optional_concepts)
    }
    if not used:
        raise MacroTemplateError("No resolved indicator assets match the template")

    steps: list[GeneralPlanStep] = []
    primary_id = resolution.primary_indicator_id()
    primary_concept = resolution.entries[primary_id].indicator.concept
    primary = used[primary_id]

    # 1. data quality on the primary asset
    steps.append(
        GeneralPlanStep(
            step_id=_step_id(f"dq:{primary_concept}"),
            operation="data_quality",
            input_refs=[primary.asset_id],
            parameters={"time_column": _time_column_for(primary.indicator)},
            expected_output="dataframe",
            depends_on=[],
        )
    )

    # 2. per-indicator macro transforms (deterministic step ids)
    transform_steps: list[GeneralPlanStep] = []
    for indicator_id, item in sorted(used.items()):
        indicator = item.indicator
        transform = next(
            (
                candidate
                for candidate in template.default_transforms
                if candidate in indicator.supported_transforms
            ),
            indicator.supported_transforms[0] if indicator.supported_transforms else None,
        )
        if transform is None:
            continue
        output_name = f"{indicator_id}_{transform}"
        steps.append(
            GeneralPlanStep(
                step_id=_step_id(f"xf:{indicator_id}:{transform}"),
                operation="macro_transform",
                input_refs=[item.asset_id],
                parameters={
                    "transform": transform,
                    "column": "value",
                    "time_column": _time_column_for(indicator),
                    "frequency": indicator.frequency,
                },
                expected_output="dataframe",
                output_name=output_name,
                depends_on=[],
            )
        )
        transform_steps.append(
            (indicator_id, steps[-1], indicator.frequency, transform)
        )

    # 3. analysis steps
    if aligned_asset_id is not None and len(transform_steps) >= 2:
        columns = [step.output_name for _, step, _, _ in transform_steps]
        depends = [step.step_id for _, step, _, _ in transform_steps]
        steps.append(
            GeneralPlanStep(
                step_id=_step_id("correlation:aligned"),
                operation="correlation",
                input_refs=[aligned_asset_id],
                parameters={"columns": columns},
                expected_output="dataframe",
                depends_on=depends,
            )
        )
    elif transform_steps:
        indicator_id, transform_step, _, transform = transform_steps[0]
        analysis_operation = (
            template.analysis_steps[0]
            if template.analysis_steps and template.analysis_steps[0] in {"data_quality", "trend"}
            else "trend"
        )
        steps.append(
            GeneralPlanStep(
                step_id=_step_id(f"analysis:{indicator_id}"),
                operation=analysis_operation,
                input_refs=[f"{transform_step.step_id}.{transform_step.output_name}"],
                parameters={
                    "column": "value",
                    "value_column": "value",
                    "time_column": "period",
                },
                expected_output="dataframe",
                depends_on=[transform_step.step_id],
            )
        )

    # 4. summarize over the aligned or primary asset
    summary_input = aligned_asset_id or (
        f"{transform_steps[0][1].step_id}.{transform_steps[0][1].output_name}"
        if transform_steps
        else primary.asset_id
    )
    summary_depends = [
        step.step_id for step in steps if step.operation in {"macro_transform", "correlation", "trend"}
    ]
    steps.append(
        GeneralPlanStep(
            step_id=_step_id("summary"),
            operation="summarize",
            input_refs=[summary_input],
            parameters={},
            expected_output="metric",
            depends_on=list(dict.fromkeys(summary_depends)),
        )
    )

    return GeneralAnalysisPlan(
        user_goal=question,
        input_asset_ids=sorted({item.asset_id for item in used.values()} | (
            {aligned_asset_id} if aligned_asset_id else set()
        )),
        steps=steps,
    )


def build_comparison_plan(
    template: MacroAnalysisTemplate,
    resolved: dict[str, ResolvedIndicatorAsset],
    *,
    question: str,
    comparison,  # ComparisonResolution (typed lazily to avoid import cycle)
    aligned_asset_id: Optional[str] = None,
    requested_countries: Sequence[str] = (),
) -> GeneralAnalysisPlan:
    """Build the cross-country comparison plan (B9.8.2 §4.4).

    Steps (all whitelisted operations):
      1. ``data_quality`` per country leg asset (per-country traceability);
      2. for every comparable concept: ``correlation`` of the aligned
         per-country rate columns on the registered aligned asset;
      3. for concepts blocked by semantic incompatibility (e.g. real vs
         nominal GDP): per-country ``macro_transform`` yoy rate steps (only
         for level/index legs — published rates are never re-diffed) plus a
         per-country ``trend``; NO cross-country step (task §4.5: block and
         disclose instead of comparing incomparable rates);
      4. ``summarize`` on the aligned asset (or on the primary leg when
         nothing is comparable).
    """
    resolution = resolve_template_requirements(
        template, resolved, requested_countries=requested_countries
    )
    if resolution.sufficiency == "blocked":
        raise MacroTemplateError(
            f"Template '{template.template_id}' blocked: "
            f"{'; '.join(resolution.missing)}"
        )

    steps: list[GeneralPlanStep] = []
    input_assets: set[str] = set()

    def _time_column(indicator: Optional[MacroIndicator]) -> str:
        return _time_column_for(indicator) if indicator is not None else "period"

    # 1. per-leg data quality — every country's raw asset is independently
    # traceable in the plan and downstream evidence.
    leg_order: list[tuple[str, str, str]] = []  # (concept, country, asset_id)
    for item in comparison.comparisons:
        for leg in item.legs:
            leg_order.append((leg.concept, leg.country, leg.asset_id))
    for concept, country, asset_id in leg_order:
        steps.append(
            GeneralPlanStep(
                step_id=_step_id(f"dq:{concept}:{country}"),
                operation="data_quality",
                input_refs=[asset_id],
                parameters={},
                expected_output="dataframe",
                depends_on=[],
            )
        )
        input_assets.add(asset_id)

    # 2. correlation on the aligned asset per comparable concept
    comparable = [item for item in comparison.comparisons if item.comparable]
    if comparable and aligned_asset_id:
        input_assets.add(aligned_asset_id)
        for item in comparable:
            steps.append(
                GeneralPlanStep(
                    step_id=_step_id(f"corr:{item.concept}"),
                    operation="correlation",
                    input_refs=[aligned_asset_id],
                    parameters={"columns": item.rate_columns},
                    expected_output="dataframe",
                    depends_on=[],
                )
            )

    # 3. blocked concepts: per-country rate production + trend, no cross step
    for item in comparison.comparisons:
        if item.comparable:
            continue
        for leg in item.legs:
            indicator = leg.indicator
            leg_time_column = _time_column(indicator)
            if indicator.level_kind == "growth_rate":
                steps.append(
                    GeneralPlanStep(
                        step_id=_step_id(f"trend:{leg.concept}:{leg.country}"),
                        operation="trend",
                        input_refs=[leg.asset_id],
                        parameters={
                            "column": "value",
                            "value_column": "value",
                            "time_column": leg_time_column,
                        },
                        expected_output="dataframe",
                        depends_on=[],
                    )
                )
                continue
            transform = (
                "yoy"
                if "yoy" in indicator.supported_transforms
                else indicator.supported_transforms[0]
            )
            output_name = f"{leg.concept}_{leg.country}_{transform}"
            steps.append(
                GeneralPlanStep(
                    step_id=_step_id(f"xf:{leg.concept}:{leg.country}:{transform}"),
                    operation="macro_transform",
                    input_refs=[leg.asset_id],
                    parameters={
                        "transform": transform,
                        "column": "value",
                        "time_column": leg_time_column,
                        "frequency": indicator.frequency,
                        "semantics": leg.semantics.model_dump(mode="json"),
                    },
                    expected_output="dataframe",
                    output_name=output_name,
                    depends_on=[],
                )
            )
            steps.append(
                GeneralPlanStep(
                    step_id=_step_id(f"trend:{leg.concept}:{leg.country}"),
                    operation="trend",
                    input_refs=[f"{steps[-1].step_id}.{output_name}"],
                    parameters={
                        "column": "value",
                        "value_column": "value",
                        "time_column": "period",
                    },
                    expected_output="dataframe",
                    depends_on=[steps[-1].step_id],
                )
            )

    # 4. summarize
    if comparable and aligned_asset_id:
        summary_input = aligned_asset_id
    elif any(step.operation == "macro_transform" for step in steps):
        last_transform = [s for s in steps if s.operation == "macro_transform"][-1]
        summary_input = f"{last_transform.step_id}.{last_transform.output_name}"
    else:
        summary_input = leg_order[0][2] if leg_order else ""
    summary_depends = [
        step.step_id
        for step in steps
        if step.operation in {"macro_transform", "correlation"}
    ]
    steps.append(
        GeneralPlanStep(
            step_id=_step_id("summary"),
            operation="summarize",
            input_refs=[summary_input],
            parameters={},
            expected_output="metric",
            depends_on=list(dict.fromkeys(summary_depends)),
        )
    )

    plan = GeneralAnalysisPlan(
        user_goal=question,
        input_asset_ids=sorted(input_assets),
        steps=steps,
    )
    return plan
