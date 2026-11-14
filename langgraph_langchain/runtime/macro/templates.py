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
from typing import Any, Optional

from langgraph_langchain.runtime.general_planner import (
    GeneralAnalysisPlan,
    GeneralPlanStep,
)
from langgraph_langchain.runtime.macro.models import (
    MacroAnalysisTemplate,
    MacroEvidenceRequirement,
    MacroIndicator,
)


class MacroTemplateError(ValueError):
    """Raised when a template cannot produce a valid plan."""


def _step_id(tag: str) -> str:
    return f"step_{uuid.uuid5(uuid.NAMESPACE_URL, f'macro:{tag}').hex}"


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
        required_concepts=[],
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
        any_of_concepts=[["nonfarm_payrolls", "unemployment_rate", "unemployment_level_bls"]],
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
        required_concepts=[],
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
}


def get_template(template_id: str) -> MacroAnalysisTemplate:
    if template_id not in MACRO_TEMPLATES:
        raise MacroTemplateError(f"Unknown macro template: {template_id!r}")
    return MACRO_TEMPLATES[template_id]


def list_templates() -> list[str]:
    return sorted(MACRO_TEMPLATES)


@dataclass
class ResolvedIndicatorAsset:
    """One resolved indicator asset inside a template run."""

    indicator: MacroIndicator
    asset_id: str
    observation_count: int


def check_data_sufficiency(
    template: MacroAnalysisTemplate,
    resolved: dict[str, ResolvedIndicatorAsset],
) -> tuple[str, list[str]]:
    """Frozen data-sufficiency policy (B9 §5.8) with any_of_concepts support (B9.8.4)."""
    # Extract concepts from resolved items (key type is indicator_id, B9.8.3).
    resolved_concepts = {item.indicator.concept for item in resolved.values()}
    # any_of_concepts: alternative indicator groups (OR logic).
    if template.any_of_concepts:
        any_satisfied = any(
            all(concept in resolved_concepts for concept in group)
            for group in template.any_of_concepts
        )
        if not any_satisfied:
            all_needed = sorted({c for group in template.any_of_concepts for c in group})
            return "blocked", [f"requires at least one of: {all_needed}"]
    missing = [concept for concept in template.required_concepts if concept not in resolved_concepts]
    if missing:
        return "blocked", [f"missing required indicators: {missing}"]
    thin = [
        concept
        for concept, item in resolved.items()
        if item.observation_count < template.evidence_requirements.min_observations
    ]
    if thin:
        return "partially_ready", [
            f"observation count below {template.evidence_requirements.min_observations}: {thin}"
        ]
    return "ready", []


def build_template_plan(
    template: MacroAnalysisTemplate,
    resolved: dict[str, ResolvedIndicatorAsset],
    *,
    question: str,
    aligned_asset_id: Optional[str] = None,
) -> GeneralAnalysisPlan:
    """Build a GeneralAnalysisPlan v2 from a template and resolved assets.

    Steps (all whitelisted operations):
      1. ``data_quality`` on the primary indicator asset;
      2. one ``macro_transform`` step per resolved indicator (first supported
         transform from the template defaults), each with its own
         ``output_name`` and downstream references;
      3. ``correlation`` on the aligned asset (multi-indicator) or ``trend`` on
         the primary transform output (single indicator);
      4. ``summarize`` on the aligned/primary asset.
    """
    sufficiency, missing = check_data_sufficiency(template, resolved)
    if sufficiency == "blocked":
        raise MacroTemplateError(
            f"Template '{template.template_id}' blocked: {'; '.join(missing)}"
        )

    used = {
        concept: resolved[concept]
        for concept in sorted(resolved)
        if concept in template.required_concepts
        or concept in template.optional_concepts
        or not (template.required_concepts or template.optional_concepts)
    }
    if not used:
        raise MacroTemplateError("No resolved indicator assets match the template")

    steps: list[GeneralPlanStep] = []
    primary_concept = (
        template.required_concepts[0]
        if template.required_concepts
        else sorted(used)[0]
    )
    primary = used[primary_concept]

    # 1. data quality on the primary asset
    steps.append(
        GeneralPlanStep(
            step_id=_step_id(f"dq:{primary_concept}"),
            operation="data_quality",
            input_refs=[primary.asset_id],
            parameters={"time_column": "period"},
            expected_output="dataframe",
            depends_on=[],
        )
    )

    # 2. per-indicator macro transforms (deterministic step ids)
    transform_steps: list[GeneralPlanStep] = []
    for concept, item in sorted(used.items()):
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
        output_name = f"{concept}_{transform}"
        steps.append(
            GeneralPlanStep(
                step_id=_step_id(f"xf:{concept}:{transform}"),
                operation="macro_transform",
                input_refs=[item.asset_id],
                parameters={
                    "transform": transform,
                    "column": "value",
                    "time_column": "period",
                    "frequency": indicator.frequency,
                },
                expected_output="dataframe",
                output_name=output_name,
                depends_on=[],
            )
        )
        transform_steps.append(
            (concept, steps[-1], indicator.frequency, transform)
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
        concept, transform_step, _, transform = transform_steps[0]
        analysis_operation = (
            template.analysis_steps[0]
            if template.analysis_steps and template.analysis_steps[0] in {"data_quality", "trend"}
            else "trend"
        )
        steps.append(
            GeneralPlanStep(
                step_id=_step_id(f"analysis:{concept}"),
                operation=analysis_operation,
                input_refs=[f"{transform_step.step_id}.{transform_step.output_name}"],
                parameters={
                    "column": "value",
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
