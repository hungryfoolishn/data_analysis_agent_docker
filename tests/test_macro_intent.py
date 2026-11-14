"""B9.4: MacroIntentResolver and Runtime V2 integration tests (offline)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from langgraph_langchain.data.acquisition import AcquisitionService
from langgraph_langchain.data.adapters.public import FREDAdapter, NBSAdapter
from langgraph_langchain.data.adapters.registry import AdapterRegistry
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.executor import TaskExecutor
from langgraph_langchain.execution.structured_executor import StructuredTaskExecutor
from langgraph_langchain.runtime.macro.intent import MacroIntentResolver, MacroRuntimeIntegration
from langgraph_langchain.runtime.macro.models import MacroIndicatorCatalog
from langgraph_langchain.runtime.analysis_workflow import general_plan_to_runtime_plan

CATALOG_PATH = Path(__file__).parents[1] / "langgraph_langchain" / "runtime" / "macro" / "catalog.json"


def _make_integration(tmp_path, *, name="b94"):
    runtime = AnalysisRuntime(workspace_dir=tmp_path / name, session_id=name, question="macro")
    adapter = FREDAdapter(cache=None)
    adapter._transport = lambda u, p: {
        "observations": [
            {"date": f"2025-{m:02d}-01", "value": f"{100 + m}.5", "series_id": "GDP"}
            for m in range(1, 13)
        ]
    }
    adapter._credentials = lambda: {"api_key": "test"}
    nbs = NBSAdapter(cache=None)
    nbs._transport = lambda u, p: {
        "returncode": 200,
        "ret": [{"datanodes": [
            {"data": {"code": "A010101", "sj": "2024", "data": 1349084}},
        ]}],
    }
    registry = AdapterRegistry()
    registry.register(adapter)
    registry.register(nbs)
    acquisition = AcquisitionService(
        runtime=runtime, registry=registry, history_dir=tmp_path / name / ".data_acq"
    )
    catalog = MacroIndicatorCatalog.load(CATALOG_PATH)
    integration = MacroRuntimeIntegration(catalog=catalog, acquisition=acquisition)
    return runtime, integration


def _load_catalog():
    return MacroIndicatorCatalog.load(CATALOG_PATH)


def test_intent_resolver_gdp_us():
    resolver = MacroIntentResolver(_load_catalog())
    intent = resolver.resolve("分析近五年美国GDP增长趋势")
    assert "gdp" in intent.concepts
    assert "us" in intent.countries
    assert intent.years == 5


def test_intent_resolver_cpi_cn():
    resolver = MacroIntentResolver(_load_catalog())
    intent = resolver.resolve("中国CPI同比变化")
    assert "cpi" in intent.concepts
    assert "cn" in intent.countries


def test_intent_resolver_comparison_multi_concept():
    resolver = MacroIntentResolver(_load_catalog())
    intent = resolver.resolve("对比美国GDP和CPI")
    assert "gdp" in intent.concepts
    assert "cpi" in intent.concepts


def test_intent_resolver_unknown_concept():
    resolver = MacroIntentResolver(_load_catalog())
    intent = resolver.resolve("分析气候变迁对农业的影响")
    assert not intent.concepts


def test_full_macro_run_planned(tmp_path):
    runtime, integration = _make_integration(tmp_path)
    outcome = integration.run("近五年美国GDP增长趋势")
    assert outcome["status"] == "planned"
    assert outcome["plan"] is not None
    assert outcome["plan"].steps


def test_macro_run_unknown_concept_needs_clarification(tmp_path):
    runtime, integration = _make_integration(tmp_path, name="b94_amb")
    outcome = integration.run("分析气候变迁对农业的影响")
    assert outcome["status"] == "needs_clarification"


def test_macro_run_full_v2_execution(tmp_path):
    """Full chain: intent → acquisition → plan → validate → Runtime V2 execute."""
    runtime, integration = _make_integration(tmp_path, name="b94_exec")
    outcome = integration.run("近五年美国GDP增长趋势")
    assert outcome["status"] == "planned"

    plan = outcome["plan"]
    runtime_plan = general_plan_to_runtime_plan(plan)
    runtime.initialize_plan(runtime_plan)

    tool = _ScriptedTool(runtime)
    runtime.configure_executor(
        TaskExecutor(structured=StructuredTaskExecutor(tool_resolver=lambda n: tool))
    )
    result = runtime.execute_next_task()
    assert result is not None and result.status == "succeeded"


def test_macro_planning_blocked_when_no_concepts(tmp_path):
    runtime, integration = _make_integration(tmp_path, name="b94_blk")
    outcome = integration.run("今天天气怎么样")
    assert outcome["status"] == "needs_clarification"
    assert outcome["plan"] is None


class _ScriptedArgs(BaseModel):
    execution_id: str = ""
    output_name: str = "output"
    asset_id: str = ""
    operation: str = ""
    parameters_json: str = "{}"


class _ScriptedTool:
    args_schema = _ScriptedArgs

    def __init__(self, runtime):
        self.runtime = runtime

    def invoke(self, arguments):
        from langgraph_langchain.runtime.analysis_operations import execute_analysis_operation

        asset_id = arguments["asset_id"]
        params = json.loads(arguments.get("parameters_json", "{}"))
        resolved = self.runtime.asset_resolver.resolve(asset_id)
        output = execute_analysis_operation(
            resolved.dataframe, operation=arguments["operation"], parameters=params
        )
        p = self.runtime.workspace_dir / f"o_{arguments.get('output_name', 'x')}.parquet"
        output.to_parquet(p, index=False)
        out_asset = self.runtime.register_dataframe(
            dataframe=output, source_path=p, source_type="parquet",
            source_metadata={"execution_id": arguments.get("execution_id", ""),
                             "input_asset_ids": [asset_id]}
        )
        return json.dumps({
            "status": "succeeded",
            "execution_id": arguments.get("execution_id", ""),
            "output_asset_id": out_asset.asset_id,
            "output_assets": {arguments.get("output_name", "output"): out_asset.asset_id},
        })
