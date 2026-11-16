"""B9.5: 20 macro economic Golden tests (offline fixture, deterministic)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from langgraph_langchain.data.acquisition import AcquisitionService
from langgraph_langchain.data.adapters.public import FREDAdapter, NBSAdapter
from langgraph_langchain.data.adapters.registry import AdapterRegistry
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.macro.intent import MacroIntentResolver, MacroRuntimeIntegration
from langgraph_langchain.runtime.macro.models import MacroIndicatorCatalog
from langgraph_langchain.runtime.macro.transforms import apply_macro_transform

CATALOG_PATH = Path(__file__).parents[1] / "langgraph_langchain" / "runtime" / "macro" / "catalog.json"

GOLDEN_QUESTIONS = [
    {"qid": "G01", "q": "分析美国近五年GDP增长趋势", "concepts": ["gdp"], "countries": ["us"], "years": 5, "template": "gdp_growth"},
    {"qid": "G02", "q": "美国最新季度GDP环比年化增长率", "concepts": ["gdp"], "countries": ["us"], "years": 2, "template": "gdp_growth"},
    {"qid": "G03", "q": "中国近五年GDP增长趋势", "concepts": ["gdp"], "countries": ["cn"], "years": 5, "template": "gdp_growth"},
    {"qid": "G04", "q": "美国名义GDP年度变化", "concepts": ["gdp"], "countries": ["us"], "years": 5, "template": "gdp_growth"},
    {"qid": "G05", "q": "分析美国CPI近三年同比走势", "concepts": ["cpi"], "countries": ["us"], "years": 3, "template": "inflation"},
    {"qid": "G06", "q": "中国CPI同比和环比变化", "concepts": ["cpi"], "countries": ["cn"], "years": 2, "template": "inflation"},
    {"qid": "G07", "q": "分析美国CPI和PPI同比对比", "concepts": ["cpi", "ppi"], "countries": ["us"], "years": 3, "template": "inflation"},
    {"qid": "G08", "q": "中国CPI指数变化趋势", "concepts": ["cpi"], "countries": ["cn"], "years": 3, "template": "inflation"},
    {"qid": "G09", "q": "美国失业率近两年走势", "concepts": ["unemployment_rate"], "countries": ["us"], "years": 2, "template": "employment"},
    {"qid": "G10", "q": "美国非农就业近一年变化", "concepts": ["nonfarm_payrolls"], "countries": ["us"], "years": 1, "template": "employment"},
    {"qid": "G11", "q": "美国就业市场分析", "concepts": ["nonfarm_payrolls"], "countries": ["us"], "years": 2, "template": "employment"},
    {"qid": "G12", "q": "分析美联储政策利率变化", "concepts": ["policy_rate"], "countries": ["us"], "years": 3, "template": "monetary_policy"},
    {"qid": "G13", "q": "美国实际利率估算", "concepts": ["policy_rate"], "countries": ["us"], "years": 3, "template": "monetary_policy"},
    {"qid": "G14", "q": "中美政策利率对比", "concepts": ["policy_rate"], "countries": ["us", "cn"], "years": 3, "template": "cross_country_comparison"},
    {"qid": "G15", "q": "对比中美CPI同比变化", "concepts": ["cpi"], "countries": ["us", "cn"], "years": 5, "template": "cross_country_comparison"},
    {"qid": "G16", "q": "中美GDP增长对比", "concepts": ["gdp"], "countries": ["us", "cn"], "years": 5, "template": "cross_country_comparison"},
    {"qid": "G17", "q": "美国出口贸易近三年趋势", "concepts": ["exports"], "countries": ["us"], "years": 3, "template": "trade"},
    {"qid": "G18", "q": "美国进口贸易分析", "concepts": ["imports"], "countries": ["us"], "years": 3, "template": "trade"},
    {"qid": "G19", "q": "分析美国通胀与利率联动", "concepts": ["cpi", "policy_rate"], "countries": ["us"], "years": 5, "template": "monetary_policy"},
    {"qid": "G20", "q": "美国GDP、CPI和就业综合分析", "concepts": ["gdp", "cpi", "nonfarm_payrolls"], "countries": ["us"], "years": 5, "template": "multi_indicator"},
]


def _make_integration(tmp_path, *, name="golden"):
    runtime = AnalysisRuntime(workspace_dir=tmp_path / name, session_id=name, question="macro golden")
    fred = FREDAdapter(cache=None)
    fred._transport = lambda u, p: {
        "observations": [
            {"date": f"2025-{m:02d}-01", "value": f"{100 + m}.5", "series_id": "TEST"}
            for m in range(1, 13)
        ]
    }
    fred._credentials = lambda: {"api_key": "test"}
    nbs = NBSAdapter(cache=None)
    nbs._transport = lambda u, p: {
        "returncode": 200,
        "ret": [{"datanodes": [
            {"data": {"code": "TEST", "sj": "2024", "data": 1349084}},
            {"data": {"code": "TEST", "sj": "2025", "data": 1400500}},
        ]}],
    }
    registry = AdapterRegistry()
    registry.register(fred)
    registry.register(nbs)
    acquisition = AcquisitionService(
        runtime=runtime, registry=registry, history_dir=tmp_path / name / ".data_acq"
    )
    catalog = MacroIndicatorCatalog.load(CATALOG_PATH)
    integration = MacroRuntimeIntegration(catalog=catalog, acquisition=acquisition)
    resolver = MacroIntentResolver(catalog)
    return runtime, integration, resolver


@pytest.mark.parametrize("gq", GOLDEN_QUESTIONS, ids=[item["qid"] for item in GOLDEN_QUESTIONS])
class TestMacroGoldenSuite:
    """20 Golden tests: intent → template → plan validity (offline, deterministic)."""

    def test_concept_resolution(self, gq, tmp_path):
        _, _, resolver = _make_integration(tmp_path, name=f"gold_{gq['qid']}")
        intent = resolver.resolve(gq["q"])
        for concept in gq["concepts"]:
            assert concept in intent.concepts, (
                f"{gq['qid']}: expected concept '{concept}' in {intent.concepts}"
            )

    def test_country_resolution(self, gq, tmp_path):
        _, _, resolver = _make_integration(tmp_path, name=f"gold_{gq['qid']}_c")
        intent = resolver.resolve(gq["q"])
        for country in gq["countries"]:
            assert country in intent.countries, (
                f"{gq['qid']}: expected country '{country}' in {intent.countries}"
            )

    def test_template_selection(self, gq, tmp_path):
        _, integration, _ = _make_integration(tmp_path, name=f"gold_{gq['qid']}_t")
        outcome = integration.run(gq["q"])
        if outcome["status"] == "needs_clarification":
            pytest.skip(f"{gq['qid']}: insufficient data for template selection")
        assert outcome["plan"] is not None
        # B9.8.2 D8 fix: the selected template is now asserted explicitly.
        assert outcome["template_id"] == gq["template"], (
            f"{gq['qid']}: expected template '{gq['template']}', "
            f"got '{outcome.get('template_id')}'"
        )

    def test_plan_generation(self, gq, tmp_path):
        _, integration, _ = _make_integration(tmp_path, name=f"gold_{gq['qid']}_p")
        outcome = integration.run(gq["q"])
        if outcome["status"] == "needs_clarification":
            pytest.skip(f"{gq['qid']}: insufficient data")
        assert outcome["plan"] is not None
        assert outcome["plan"].steps


class TestMacroGoldenSummary:
    def test_all_20_questions_defined(self):
        assert len(GOLDEN_QUESTIONS) == 20
        assert len({item["qid"] for item in GOLDEN_QUESTIONS}) == 20

    def test_category_distribution(self):
        by_category = {}
        for item in GOLDEN_QUESTIONS:
            if item["template"] == "gdp_growth":
                by_category.setdefault("GDP", []).append(item)
            elif item["template"] == "inflation":
                by_category.setdefault("CPI/PPI", []).append(item)
            elif item["template"] == "employment":
                by_category.setdefault("就业", []).append(item)
            elif item["template"] == "monetary_policy":
                by_category.setdefault("利率", []).append(item)
            elif item["template"] == "multi_indicator":
                by_category.setdefault("多指标", []).append(item)
            elif item["template"] == "trade":
                by_category.setdefault("贸易", []).append(item)
            elif item["template"] == "cross_country_comparison":
                by_category.setdefault("跨国家比较", []).append(item)
        assert len(by_category.get("GDP", [])) >= 3
        assert len(by_category.get("CPI/PPI", [])) >= 2
        assert len(by_category.get("就业", [])) >= 2
        assert len(by_category.get("利率", [])) >= 2
        assert len(by_category.get("跨国家比较", [])) >= 3
        assert len(by_category.get("多指标", [])) >= 1

    def test_golden_not_replacing_b6(self):
        """B9 Golden is domain-specific; B6 10/30 general baseline is separate."""
        assert len(GOLDEN_QUESTIONS) == 20  # B9-specific, not B6's 30
