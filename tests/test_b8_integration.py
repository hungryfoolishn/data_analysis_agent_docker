"""B8.11: full offline acquisition chain acceptance for all five adapters.

Chain per adapter:
DatasetCatalog → AdapterRegistry → Adapter.fetch → FetchResult → QualityReport
→ AcquisitionService → AcquisitionHistory → SnapshotStore → DataAsset
→ AssetResolver → Runtime V2 → Binding → Evidence → Verified Report.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from langgraph_langchain.data.acquisition import AcquisitionService
from langgraph_langchain.data.adapters.base import FetchRequest
from langgraph_langchain.data.adapters.public import (
    BEAAdapter,
    BLSAdapter,
    FREDAdapter,
    NBSAdapter,
    SECAdapter,
)
from langgraph_langchain.data.adapters.registry import AdapterRegistry
from langgraph_langchain.evidence import (
    canonical_from_operation_evidence,
    collect_operation_evidence,
)
from langgraph_langchain.execution.structured_executor import StructuredTaskExecutor
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.executor import TaskExecutor
from langgraph_langchain.runtime.models import RuntimePlanStep
from langgraph_langchain.runtime.plans import AnalysisPlan


class _ScriptedArgs(BaseModel):
    asset_id: str
    operation: str
    parameters_json: str = "{}"
    output_name: str = "result"
    execution_id: str = ""


class _ScriptedOperationTool:
    """Faithful mini run_analysis_operation (same contract as the real tool)."""

    args_schema = _ScriptedArgs

    def __init__(self, runtime, *, step_id: str):
        self.runtime = runtime
        self.step_id = step_id
        self.calls = []

    def invoke(self, arguments):
        self.calls.append(dict(arguments))
        from langgraph_langchain.runtime.analysis_operations import (
            execute_analysis_operation,
        )

        asset_id = arguments["asset_id"]
        operation = arguments["operation"]
        parameters = json.loads(arguments.get("parameters_json", "{}"))
        execution_id = arguments.get("execution_id", "")
        resolved = self.runtime.asset_resolver.resolve(asset_id)
        output = execute_analysis_operation(
            resolved.dataframe, operation=operation, parameters=parameters
        )
        output_path = self.runtime.workspace_dir / f"acq_output_{len(self.calls)}.parquet"
        output.to_parquet(output_path, index=False)
        output_asset = self.runtime.register_dataframe(
            dataframe=output,
            source_path=output_path,
            source_type="parquet",
            source_metadata={
                "operation": operation,
                "parameters": parameters,
                "input_asset_ids": [asset_id],
                "execution_id": execution_id,
            },
        )
        artifact = self.runtime.register_artifact(
            output_path,
            created_by_tool="run_analysis_operation",
            execution_id=execution_id,
            step_id=self.step_id,
            input_asset_ids=[asset_id],
        )
        evidence = collect_operation_evidence(
            execution_id=execution_id,
            operation=operation,
            parameters=parameters,
            input_asset_ids=[asset_id],
            output_asset=output_asset,
            numeric_values={
                "row_count": float(len(output)),
                "column_count": float(len(output.columns)),
            },
        )
        self.runtime.record_execution(
            tool_name="run_analysis_operation",
            status="succeeded",
            execution_id=execution_id,
            step_id=self.step_id,
            input_asset_ids=[asset_id],
            output_artifact_ids=[artifact.artifact_id],
        )
        canonical = canonical_from_operation_evidence(
            evidence, task_id=self.step_id, artifact_ids=[artifact.artifact_id]
        )
        self.runtime.register_canonical_evidence(
            canonical, artifact_ids=[artifact.artifact_id]
        )
        return json.dumps(
            {
                "status": "succeeded",
                "execution_id": execution_id,
                "output_asset_id": output_asset.asset_id,
                "output_assets": {
                    arguments.get("output_name", "result"): output_asset.asset_id
                },
            }
        )


# ---------------------------------------------------------------------------
# per-adapter fixtures: (adapter_id, dataset_id, transport, request, checks)
# ---------------------------------------------------------------------------

def _fred(monkeypatch=None):
    adapter = FREDAdapter(cache=None)
    adapter._transport = lambda url, params: {
        "observations": [
            {"date": "2026-01-01", "value": "100.5", "series_id": "GDP"},
            {"date": "2026-02-01", "value": "101.5", "series_id": "GDP"},
        ]
    }
    adapter._credentials = lambda: {"api_key": "test-key"}
    return (
        adapter,
        FetchRequest(dataset_id="fred_gdp", start="2026-01-01", end="2026-03-31"),
        {"unit": "billions_of_chained_2017_dollars", "frequency": "quarterly"},
        ["2026-01-01", "2026-02-01"],
    )


def _bls(monkeypatch=None):
    adapter = BLSAdapter(cache=None)
    adapter._transport = lambda url, params: {
        "Results": {
            "series": [
                {
                    "seriesID": "CES0000000001",
                    "data": [
                        {"year": "2024", "period": "M01", "value": "158000"},
                        {"year": "2024", "period": "M02", "value": "158100"},
                    ],
                }
            ]
        }
    }
    return (
        adapter,
        FetchRequest(dataset_id="bls_employment", start="2024-01-01", end="2024-12-31"),
        {"unit": "thousands_of_persons", "frequency": "monthly"},
        ["2024-M01", "2024-M02"],
    )


def _bea(monkeypatch):
    monkeypatch.setenv("BEA_API_KEY", "test-bea-key")
    adapter = BEAAdapter(cache=None)
    adapter._transport = lambda url, params: {
        "BEAAPI": {
            "Results": {
                "Data": [
                    {"TimePeriod": "2023", "DataValue": "25,000.0", "LineDescription": "Gross domestic product"},
                    {"TimePeriod": "2022", "DataValue": "24,000.0", "LineDescription": "Gross domestic product"},
                ]
            }
        }
    }
    return (
        adapter,
        FetchRequest(dataset_id="bea_gdp", parameters={"year": "ALL"}),
        {"unit": "billions_of_current_dollars", "frequency": "annual"},
        ["2022", "2023"],
    )


def _sec(monkeypatch=None):
    adapter = SECAdapter(cache=None)
    adapter._transport = lambda url, params: {
        "units": {
            "USD": [
                {"start": "2025-01-01", "end": "2025-03-31", "val": "1000", "form": "10-Q", "accn": "0001"},
            ]
        }
    }
    return (
        adapter,
        FetchRequest(dataset_id="sec_company_revenues", parameters={"cik": "0000320193"}),
        {"unit": "USD", "frequency": "filing_event"},
        None,
    )


def _nbs(monkeypatch=None):
    adapter = NBSAdapter(cache=None)
    adapter._transport = lambda url, params: {
        "returncode": 200,
        "ret": [
            {"datanodes": [
                {"data": {"sj": "2015", "data": 613619.0, "code": "A010101"}},
                {"data": {"sj": "2016", "data": 744127.0, "code": "A010101"}},
            ]}
        ],
    }
    return (
        adapter,
        FetchRequest(dataset_id="nbs_gdp", start="2015", end="2016"),
        {"unit": "100_million_cny", "frequency": "annual"},
        ["2015", "2016"],
    )


def _catalog_of(adapter):
    registry = AdapterRegistry()
    registry.register(adapter)
    return registry


def _run_v2_chain(runtime, asset_id: str):
    step_id = "step_" + "b" * 32
    step = RuntimePlanStep(
        step_id=step_id,
        objective="Summarize acquired dataset",
        method="run_analysis_operation",
        expected_outputs=["metric_result"],
        required_inputs=[asset_id],
        parameters={
            "asset_id": asset_id,
            "operation": "summarize",
            "parameters_json": json.dumps({}),
        },
    )
    runtime.initialize_plan(AnalysisPlan(goal="summarize acquired data", steps=[step]))
    tool = _ScriptedOperationTool(runtime, step_id=step_id)
    runtime.configure_executor(
        TaskExecutor(structured=StructuredTaskExecutor(tool_resolver=lambda name: tool))
    )
    result = runtime.execute_next_task()
    assert result is not None and result.status == "succeeded"
    assert tool.calls[0]["asset_id"] == asset_id
    report, markdown = runtime.build_verified_report()
    assert report is not None and report.findings and markdown
    referenced = {
        asset_id_
        for finding in report.findings
        for asset_id_ in finding.referenced_asset_ids
    }
    return referenced


@pytest.mark.parametrize(
    "dataset_id",
    ["fred_gdp", "bls_employment", "bea_gdp", "sec_company_revenues", "nbs_gdp"],
)
def test_five_adapter_full_offline_chain(tmp_path, monkeypatch, dataset_id):
    builders = {
        "fred_gdp": _fred,
        "bls_employment": _bls,
        "bea_gdp": _bea,
        "sec_company_revenues": _sec,
        "nbs_gdp": _nbs,
    }
    adapter, request, meta_checks, periods = builders[dataset_id](monkeypatch)
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path / "chain", session_id="chain", question="acquire data"
    )
    registry = _catalog_of(adapter)

    # Catalog: DatasetSpec discoverable through the registry.
    entries = [entry for entry in registry.catalog() if entry.dataset_id == dataset_id]
    assert len(entries) == 1
    assert entries[0].adapter_id == adapter.descriptor.adapter_id

    service = AcquisitionService(
        runtime=runtime,
        registry=registry,
        history_dir=runtime.workspace_dir / ".data_acquisition",
    )
    outcome = service.acquire(request)

    # Metadata semantics: source/unit/frequency and hash lineage.
    metadata = outcome.asset.source_metadata
    assert metadata["source_id"] == adapter.descriptor.adapter_id
    assert metadata["dataset_id"] == dataset_id
    assert metadata["unit"] == meta_checks["unit"]
    assert metadata["frequency"] == meta_checks["frequency"]
    assert metadata["fetched_at"]
    assert metadata["quality_report"]["status"] in {"passed", "warning"}
    assert outcome.asset.content_hash == outcome.fetch_result.content_hash

    # History + snapshot + resolver.
    record = service.history.latest(dataset_id)
    assert record["asset_id"] == outcome.asset.asset_id
    assert record["content_hash"] == outcome.asset.content_hash
    resolved = runtime.asset_resolver.resolve(outcome.asset.asset_id)
    assert not resolved.dataframe.empty

    if periods is not None:
        # observation_period is derived from unambiguous ISO-like labels only;
        # BLS period labels are not calendar dates and must stay unknown.
        observation_expectations = {
            "fred_gdp": ["2026-01-01", "2026-02-01"],
            "bea_gdp": ["2022-01-01", "2023-01-01"],
            "nbs_gdp": ["2015-01-01", "2016-01-01"],
        }
        assert metadata["observation_period"] == observation_expectations.get(dataset_id)
        column = {
            "fred_gdp": "date",
            "bls_employment": "period",
            "bea_gdp": "period",
            "nbs_gdp": "period",
        }.get(dataset_id)
        if column:
            assert resolved.dataframe[column].astype(str).tolist() == periods

    # Runtime V2 → Binding → Evidence → Verified Report disclosure.
    referenced = _run_v2_chain(runtime, outcome.asset.asset_id)
    assert outcome.asset.asset_id in referenced
    # Evidence → original source traceability: verified evidence must reference
    # the acquired input asset (and the analysis output asset).
    verified = list(runtime.evidence_registry.list(statuses={"verified"}))
    assert verified
    evidence_inputs = {
        asset_id_
        for evidence in verified
        for asset_id_ in evidence.input_asset_ids
    }
    assert outcome.asset.asset_id in evidence_inputs


def test_multi_adapters_run_independently(tmp_path):
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path / "multi", session_id="multi", question="acquire data"
    )
    registry = AdapterRegistry()
    fred, fred_req, _, _ = _fred()
    nbs, nbs_req, _, _ = _nbs()
    registry.register(fred)
    registry.register(nbs)
    service = AcquisitionService(
        runtime=runtime,
        registry=registry,
        history_dir=runtime.workspace_dir / ".data_acquisition",
    )
    fred_outcome = service.acquire(fred_req)
    nbs_outcome = service.acquire(nbs_req)
    assert fred_outcome.asset.asset_id != nbs_outcome.asset.asset_id
    assert len(service.history.versions("fred_gdp")) == 1
    assert len(service.history.versions("nbs_gdp")) == 1
    assert len(runtime.assets) == 2


def test_content_change_and_restorable_snapshot_lineage(tmp_path):
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path / "versions", session_id="versions", question="acquire data"
    )
    adapter, request, _, _ = _fred()
    registry = _catalog_of(adapter)
    service = AcquisitionService(
        runtime=runtime,
        registry=registry,
        history_dir=runtime.workspace_dir / ".data_acquisition",
    )
    first = service.acquire(request)
    adapter._transport = lambda url, params: {
        "observations": [
            {"date": "2026-01-01", "value": "100.5", "series_id": "GDP"},
            {"date": "2026-02-01", "value": "999.9", "series_id": "GDP"},
        ]
    }
    second = service.refresh(request)
    assert second.version_changed is True

    # Both versions remain restorable through their own snapshots.
    first_resolved = runtime.asset_resolver.resolve(first.asset.asset_id)
    second_resolved = runtime.asset_resolver.resolve(second.asset.asset_id)
    assert first_resolved.dataframe["value"].iloc[-1] == 101.5
    assert second_resolved.dataframe["value"].iloc[-1] == 999.9
    # The asset→history reverse lookup resolves to distinct content versions.
    assert (
        service.history.find_by_asset_id(first.asset.asset_id)["content_version"] == "v1"
    )
    assert (
        service.history.find_by_asset_id(second.asset.asset_id)["content_version"] == "v2"
    )


def test_concurrent_registration_deduplicates_identical_content(tmp_path):
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path / "concurrent", session_id="concurrent", question="acquire data"
    )
    adapter, request, _, _ = _fred()
    registry = _catalog_of(adapter)
    import threading

    results = []

    def worker():
        results.append(adapter.fetch(request))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    registered = [runtime.register_fetch_result(result) for result in results]
    assert len({asset.asset_id for asset in registered}) == 1
    assert len(runtime.assets) == 1
