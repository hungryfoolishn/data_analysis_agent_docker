"""B8.11.1-C: acquisition asset through the REAL runtime operation entry.

Unlike tests/test_b8_integration.py (which uses a scripted mini-tool), this
test drives the production chain end to end:

DatasetCatalog → AdapterRegistry → AcquisitionService → register_fetch_result
→ DataAsset → AssetResolver → DeterministicAnalysisPlanner (GeneralAnalysisPlan)
→ Runtime V2 graph (_run_runtime_v2_graph_stream) → real `run_analysis_operation`
tool from the production tool registry → ExecutionResult → OutputBinding
→ Canonical Evidence → Verified Report.

No real network is used; the adapter transport is transport-injected. The LLM
planner is replaced by the production deterministic planner (same component the
file workflow uses), because the integration target here is the Runtime V2
execution chain, not LLM planning.
"""

from __future__ import annotations

import asyncio

from langgraph_langchain.data.acquisition import AcquisitionService
from langgraph_langchain.data.adapters.base import FetchRequest
from langgraph_langchain.data.adapters.public import FREDAdapter
from langgraph_langchain.data.adapters.registry import AdapterRegistry
from langgraph_langchain.langgraph_agent import (
    _Session,
    _make_tools,
    _run_runtime_v2_graph_stream,
)
from langgraph_langchain.runtime.analysis_workflow import general_plan_to_runtime_plan
from langgraph_langchain.runtime.general_planner import DeterministicAnalysisPlanner


def _rows():
    return {
        "observations": [
            {"date": "2026-01-01", "value": "100.5", "series_id": "GDP"},
            {"date": "2026-02-01", "value": "101.5", "series_id": "GDP"},
            {"date": "2026-03-01", "value": "102.1", "series_id": "GDP"},
        ]
    }


def test_real_run_analysis_operation_consumes_acquired_asset(tmp_path):
    session = _Session(
        str(tmp_path / "workspace"),
        None,
        session_id="b8-real-operation",
        user_question="Summarize the acquired GDP series",
    )
    runtime = session.analysis_runtime
    assert runtime is not None

    # 1) Real acquisition path: adapter → FetchResult → snapshot → DataAsset.
    adapter = FREDAdapter(cache=None)
    adapter._transport = lambda url, params: _rows()
    adapter._credentials = lambda: {"api_key": "test-key"}
    registry = AdapterRegistry()
    registry.register(adapter)
    service = AcquisitionService(
        runtime=runtime,
        registry=registry,
        history_dir=runtime.workspace_dir / ".data_acquisition",
    )
    outcome = service.acquire(
        FetchRequest(dataset_id="fred_gdp", start="2026-01-01", end="2026-03-31")
    )
    asset = outcome.asset
    assert asset.asset_id in runtime.assets

    # 2) The acquired asset is restorable through the AssetResolver.
    resolved = runtime.asset_resolver.resolve(asset.asset_id)
    assert resolved.dataframe["value"].tolist() == [100.5, 101.5, 102.1]

    # 3) Production deterministic planner over the acquired asset.
    asset_columns = {
        asset.asset_id: [column.name for column in asset.schema_snapshot.columns]
    }
    planner_result = DeterministicAnalysisPlanner().plan(
        goal="Summarize the acquired GDP series",
        input_asset_ids=[asset.asset_id],
        asset_columns=asset_columns,
    )
    runtime_plan = general_plan_to_runtime_plan(planner_result.plan)
    step = runtime_plan.steps[0]
    # The step binds the acquired asset explicitly — no session fallback.
    assert step.parameters["asset_id"] == asset.asset_id
    assert session.current_asset_id is None
    runtime.initialize_plan(runtime_plan)

    # 4) Real production tool registry (includes run_analysis_operation).
    tools = _make_tools(session)
    assert any(getattr(tool, "name", "") == "run_analysis_operation" for tool in tools)

    async def collect():
        return [
            chunk
            async for chunk, _ in _run_runtime_v2_graph_stream(
                session, tools=tools, source_path=str(session.workspace_dir)
            )
        ]

    chunks = asyncio.run(collect())
    assert any("Runtime V2 structured plan completed" in chunk for chunk in chunks)

    # 5) Runtime outcome + verified report.
    assert session.runtime_outcome.status == "completed"
    assert session.report is not None

    # 6) Execution records: real tool, canonical execution id, succeeded.
    op_executions = [
        execution
        for execution in runtime.executions
        if execution.tool_name == "run_analysis_operation"
    ]
    assert op_executions and op_executions[-1].status == "succeeded"
    execution_id = op_executions[-1].execution_id
    assert execution_id
    assert op_executions[-1].input_asset_ids == [asset.asset_id]

    # 7) Output binding points at the registered output asset.  The registry
    # may also hold the report-artifact binding (separate execution id); the
    # analysis output must be bound under the tool's canonical execution id.
    bindings = runtime.output_bindings.list_all()
    assert bindings
    operation_bindings = [
        binding for binding in bindings if binding.execution_id == execution_id
    ]
    assert operation_bindings
    output_asset_ids = {binding.asset_id for binding in operation_bindings}
    assert output_asset_ids and output_asset_ids.isdisjoint({asset.asset_id})

    # 8) Verified canonical evidence traces both input and output assets.
    verified = list(runtime.evidence_registry.list(statuses={"verified"}))
    assert verified
    evidence_inputs = {
        input_id for evidence in verified for input_id in evidence.input_asset_ids
    }
    evidence_outputs = {
        output_id for evidence in verified for output_id in evidence.output_asset_ids
    }
    assert asset.asset_id in evidence_inputs
    assert output_asset_ids <= evidence_outputs

    # 9) Report findings disclose the input source asset and the output asset.
    report, report_markdown = runtime.build_verified_report()
    assert report is not None and report.findings
    referenced = {
        referenced_id
        for finding in report.findings
        for referenced_id in finding.referenced_asset_ids
    }
    assert asset.asset_id in referenced
    assert output_asset_ids & referenced

    # 10) The tool registered its output through the real runtime path.
    registered_output_ids = set(runtime.assets)
    assert output_asset_ids <= registered_output_ids
    assert outcome.transaction_id
