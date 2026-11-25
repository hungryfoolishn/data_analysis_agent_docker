from __future__ import annotations

import pytest

from langgraph_langchain.tools import _discover_tools
from langgraph_langchain.tools.registry import registry
from langgraph_langchain.runtime.financial.pack import build_financial_pack_contract


EXPECTED_TOOLS = {
    "analysis": {
        "analyze_concentration",
        "analyze_correlation",
        "analyze_funnel",
        "analyze_retention",
        "analyze_time_trend",
        "calculate_ratio",
        "compare_groups",
        "compare_periods",
        "declare_assumption",
        "declare_metric",
        "decompose_contribution",
        "delegate_analysis",
        "detect_anomalies",
        "eda_profile",
        "finish_report",
        "load_data",
        "profile_distribution",
        "python_repl",
        "record_finding",
        "run_analysis_operation",
        "run_sensitivity_check",
        "run_sqlite_query",
        "test_group_difference",
    },
    "memory": {"memory"},
    "skills": {"skill_reference", "skill_view"},
}


def test_capability_inventory_matches_real_tool_registry():
    _discover_tools()
    observed = {}
    for name, entry in registry._tools.items():
        observed.setdefault(entry.toolset, set()).add(name)
    assert observed == EXPECTED_TOOLS


def test_capability_inventory_has_no_name_collisions():
    _discover_tools()
    names = registry.get_tool_names()
    assert len(names) == len(set(names))


def test_financial_domain_pack_contract_is_code_anchored():
    contract = build_financial_pack_contract()
    assert contract.pack_id == "financial"
    assert contract.pack_version == "0.2.0"
    assert set(contract.supported_capabilities) == {
        "financial_models",
        "financial_normalization",
    }
    assert contract.dependency_boundary == ("langgraph_langchain.runtime.macro",)
    assert contract.normalization_contract["unknown_policy"] == "reject (never guess)"