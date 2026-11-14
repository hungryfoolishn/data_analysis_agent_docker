"""B9.3: six macro template tests."""

from __future__ import annotations

import pandas as pd
import pytest

from langgraph_langchain.data.assets import SchemaSnapshot
from langgraph_langchain.runtime.macro.models import MacroIndicator
from langgraph_langchain.runtime.macro.templates import (
    MACRO_TEMPLATES,
    MacroTemplateError,
    ResolvedIndicatorAsset,
    build_template_plan,
    check_data_sufficiency,
    get_template,
    list_templates,
)

def _indicator(concept, country, dataset_id, source_id, frequency, **overrides):
    payload = {
        "indicator_id": f"{country}_{concept}_{frequency}",
        "concept": concept,
        "country": country,
        "source_id": source_id,
        "dataset_id": dataset_id,
        "unit": "test_unit",
        "frequency": frequency,
        "seasonal_adjustment": "sa",
        "price_basis": "index",
        "level_kind": "level",
        "supported_transforms": ("yoy", "mom", "qoq", "rolling_average", "period_comparison"),
    }
    payload.update(overrides)
    return MacroIndicator(**payload)


def _resolved(concept, country, dataset_id, source_id, frequency, count=13):
    indicator = _indicator(concept, country, dataset_id, source_id, frequency)
    snapshot = SchemaSnapshot.from_dataframe(
        pd.DataFrame({"period": [f"2026-{m:02d}" for m in range(1, min(count, 12) + 1)],
                      "value": [1.0] * min(count, 12)})
    ) if frequency == "monthly" else SchemaSnapshot.from_dataframe(
        pd.DataFrame({"period": ["2025Q1", "2025Q2"], "value": [1.0, 2.0]})
    )
    return concept, (indicator, f"asset_{concept}_{country}", count)


def _resolved_map(specs):
    resolved = {}
    for concept, country, dataset_id, source_id, frequency, count in specs:
        key, value = _resolved(concept, country, dataset_id, source_id, frequency, count)
        indicator, asset_id, observation_count = value
        resolved[concept] = ResolvedIndicatorAsset(
            indicator=indicator,
            asset_id=asset_id,
            observation_count=observation_count,
        )
    return resolved


def test_all_six_templates_registered():
    assert set(list_templates()) == {
        "gdp_growth", "inflation", "employment", "monetary_policy", "trade", "multi_indicator"
    }


def test_get_template_unknown_fails():
    with pytest.raises(MacroTemplateError):
        get_template("nope")


def test_gdp_template_builds_valid_plan(tmp_path):
    from langgraph_langchain.runtime.general_planner import validate_general_plan

    template = get_template("gdp_growth")
    resolved = _resolved_map([
        ("gdp", "us", "fred_gdp", "fred", "quarterly", 8),
    ])
    sufficiency, missing = check_data_sufficiency(template, resolved)
    assert sufficiency == "ready"
    plan = build_template_plan(template, resolved, question="US GDP growth")
    errors = validate_general_plan(plan, asset_columns={
        **{item.asset_id: ["period", "value"] for item in resolved.values()}
    })
    assert errors == []


def test_inflation_template_with_optional_ppi(tmp_path):
    from langgraph_langchain.runtime.general_planner import validate_general_plan

    template = get_template("inflation")
    resolved = _resolved_map([
        ("cpi", "us", "fred_cpi", "fred", "monthly", 13),
        ("ppi", "us", "fred_ppi", "fred", "monthly", 13),
    ])
    sufficiency, _ = check_data_sufficiency(template, resolved)
    assert sufficiency == "ready"
    plan = build_template_plan(
        template, resolved, question="US inflation", aligned_asset_id="asset_aligned"
    )
    errors = validate_general_plan(plan, asset_columns={
        "asset_aligned": ["us_cpi_yoy", "us_ppi_yoy"],
        **{item.asset_id: ["period", "value"] for item in resolved.values()}
    })
    assert errors == []
    operations = [step.operation for step in plan.steps]
    assert "macro_transform" in operations
    assert "correlation" in operations


def test_employment_template_us_only():
    template = get_template("employment")
    assert template.countries == ("us",)
    resolved = _resolved_map([
        ("nonfarm_payrolls", "us", "bls_employment", "bls", "monthly", 3),
    ])
    sufficiency, missing = check_data_sufficiency(template, resolved)
    # 3 observations < min_observations 6 -> partial readiness with disclosure
    assert sufficiency == "partially_ready"
    assert missing


def test_blocked_when_required_indicator_missing():
    template = get_template("monetary_policy")
    resolved = _resolved_map([
        ("cpi", "us", "fred_cpi", "fred", "monthly", 13),
    ])
    sufficiency, missing = check_data_sufficiency(template, resolved)
    assert sufficiency == "blocked"
    assert any("policy_rate" in item for item in missing)
    with pytest.raises(MacroTemplateError):
        build_template_plan(template, resolved, question="rates")


def test_multi_indicator_requires_two_for_correlation():
    from langgraph_langchain.runtime.general_planner import validate_general_plan

    template = get_template("multi_indicator")
    resolved = _resolved_map([
        ("gdp", "us", "fred_gdp", "fred", "quarterly", 13),
        ("cpi", "us", "fred_cpi", "fred", "monthly", 13),
    ])
    plan = build_template_plan(
        template, resolved, question="joint analysis", aligned_asset_id="asset_aligned"
    )
    from langgraph_langchain.runtime.general_planner import validate_general_plan

    asset_columns = {
        **{item.asset_id: ["period", "value"] for item in resolved.values()},
        "asset_aligned": ["us_gdp_yoy", "us_cpi_yoy"],
    }
    errors = validate_general_plan(plan, asset_columns=asset_columns)
    assert errors == []


def test_trade_template_documents_single_side_limitation():
    template = get_template("trade")
    assert any("single-side" in limitation for limitation in template.limitations)


def test_all_template_operations_whitelisted():
    from langgraph_langchain.runtime.general_planner import PLAN_OPERATIONS

    for template_id in list_templates():
        template = get_template(template_id)
        for operation in template.analysis_steps:
            assert operation in PLAN_OPERATIONS or operation in {"trend", "correlation", "period_comparison", "data_quality"}
