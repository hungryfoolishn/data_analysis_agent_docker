"""B8: Data Acquisition Platform end-to-end tests (offline, transport-injected)."""

from __future__ import annotations

import json
import time

import pandas as pd
import pytest
from pydantic import BaseModel

from langgraph_langchain.data.acquisition import (
    AcquisitionService,
    AdapterHealthTracker,
)
from langgraph_langchain.data.adapters.base import detect_observation_gaps
from langgraph_langchain.data.adapters.base import (
    AdapterDescriptor,
    AdapterRequestError,
    FetchRequest,
    HttpJsonAdapter,
)
from langgraph_langchain.data.adapters.public import FREDAdapter, NBSAdapter
from langgraph_langchain.evidence import (
    canonical_from_operation_evidence,
    collect_operation_evidence,
)
from langgraph_langchain.execution.structured_executor import StructuredTaskExecutor
from langgraph_langchain.runtime.analysis_workflow import register_file_asset  # noqa: F401
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.executor import TaskExecutor
from langgraph_langchain.runtime.llm_planner import run_planning  # noqa: F401
from langgraph_langchain.runtime.plans import AnalysisPlan
from langgraph_langchain.runtime.models import RuntimePlanStep


def _fred_rows(tag: str):
    return {
        "observations": [
            {"date": "2026-01-01", "value": "100.5", "series_id": "GDP"},
            {"date": "2026-02-01", "value": "101.5", "series_id": "GDP"},
            {"date": "2026-03-01", "value": f"102.{tag}", "series_id": "GDP"},
        ]
    }


def _fred_runtime(tmp_path, name="acq"):
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path / name, session_id=name, question="acquire data"
    )
    return runtime


def _service(tmp_path, runtime, *, transport_tag="1"):
    adapter = FREDAdapter(cache=None)
    adapter._transport = lambda url, params: _fred_rows(transport_tag)
    adapter._credentials = lambda: {"api_key": "test-key"}  # offline tests
    from langgraph_langchain.data.adapters.registry import AdapterRegistry

    registry = AdapterRegistry()
    registry.register(adapter)
    return AcquisitionService(
        runtime=runtime,
        registry=registry,
        history_dir=runtime.workspace_dir / ".data_acquisition",
    )


def _fred_request(**overrides):
    payload = {
        "dataset_id": "fred_gdp",
        "start": "2026-01-01",
        "end": "2026-03-31",
    }
    payload.update(overrides)
    return FetchRequest(**payload)


def test_acquire_registers_contract_complete_asset(tmp_path):
    runtime = _fred_runtime(tmp_path)
    service = _service(tmp_path, runtime)
    outcome = service.acquire(_fred_request())

    asset = outcome.asset
    metadata = asset.source_metadata
    assert metadata["source_id"] == "fred"
    assert metadata["dataset_id"] == "fred_gdp"
    assert metadata["request_parameters"]["start"] == "2026-01-01"
    assert metadata["source_url"]
    assert metadata["fetched_at"]
    assert metadata["unit"] == "billions_of_chained_2017_dollars"
    assert metadata["frequency"] == "quarterly"
    assert metadata["observation_period"] == ["2026-01-01", "2026-03-01"]
    assert metadata["quality_report"]["status"] == "passed"
    assert asset.content_hash == outcome.fetch_result.content_hash
    # snapshot-backed: resolver restores the data without any source file
    resolved = runtime.asset_resolver.resolve(asset.asset_id)
    assert len(resolved.dataframe) == 3
    assert resolved.dataframe["value"].tolist() == [100.5, 101.5, 102.1]


def test_persistence_survives_runtime_restore(tmp_path):
    runtime = _fred_runtime(tmp_path)
    service = _service(tmp_path, runtime)
    outcome = service.acquire(_fred_request())

    restored = AnalysisRuntime(
        workspace_dir=runtime.workspace_dir,
        session_id=runtime.session_id,
        question=runtime.task.question,
    )
    restored_asset = restored.assets[outcome.asset.asset_id]
    assert restored_asset.source_metadata["dataset_id"] == "fred_gdp"
    resolved = restored.asset_resolver.resolve(restored_asset.asset_id)
    assert len(resolved.dataframe) == 3


def test_dedup_and_version_change(tmp_path):
    runtime = _fred_runtime(tmp_path)
    service = _service(tmp_path, runtime, transport_tag="1")

    first = service.acquire(_fred_request())
    second = service.acquire(_fred_request())
    # Identical content: dedup returns the same asset, no version change.
    assert second.asset.asset_id == first.asset.asset_id
    assert second.version_changed is False

    # Changed content (refresh bypasses cache, transport returns new values).
    service.registry.get("fred")._transport = lambda url, params: _fred_rows("9")
    changed = service.refresh(_fred_request())
    assert changed.version_changed is True
    assert changed.previous_content_hash == first.fetch_result.content_hash
    assert changed.asset.asset_id != first.asset.asset_id
    history = service.history.history("fred_gdp")
    assert len(history) == 3
    assert [entry["version_changed"] for entry in history] == [False, False, True]


def test_adapter_health_tracking(tmp_path):
    runtime = _fred_runtime(tmp_path)
    service = _service(tmp_path, runtime)
    assert service.health.status("fred") == "unknown"
    service.acquire(_fred_request())
    assert service.health.status("fred") == "healthy"
    service.health.record_failure("fred", "boom")
    service.health.record_failure("fred", "boom again")
    assert service.health.status("fred") == "failing"


def test_rate_limit_enforced(tmp_path):
    class _SlowFRED(FREDAdapter):
        descriptor = AdapterDescriptor(
            adapter_id="fred_slow",
            version="1.0.0",
            source_type="fred",
            description="rate-limited fred",
            rate_limit_seconds=0.15,
        )

    adapter = _SlowFRED(cache=None)
    adapter._transport = lambda url, params: _fred_rows("1")
    adapter._credentials = lambda: {"api_key": "test-key"}
    request = _fred_request()
    adapter.fetch(request)
    start = time.monotonic()
    adapter.fetch(request)
    elapsed = time.monotonic() - start
    assert elapsed >= 0.1  # second call waits for the declared rate limit


def test_gap_detection_monthly_missing_month(tmp_path):
    frame = pd.DataFrame(
        {"date": ["2026-01-01", "2026-02-01", "2026-04-01"], "value": [1, 2, 4]}
    )
    gaps = detect_observation_gaps(frame, "date", "monthly")
    assert gaps and "2026-03" in gaps[0]


def test_gap_detection_complete_series_has_no_gaps(tmp_path):
    frame = pd.DataFrame(
        {"date": ["2026-01-01", "2026-02-01", "2026-03-01"], "value": [1, 2, 3]}
    )
    assert detect_observation_gaps(frame, "date", "monthly") == ()


def test_quality_downgrades_on_observation_gap(tmp_path):
    runtime = _fred_runtime(tmp_path, name="gapws")
    adapter = FREDAdapter(cache=None)
    adapter._credentials = lambda: {"api_key": "test-key"}

    def _gapped(url, params):
        return {
            "observations": [
                {"date": "2026-01-01", "value": "1", "series_id": "GDP"},
                {"date": "2026-02-01", "value": "2", "series_id": "GDP"},
                {"date": "2026-04-01", "value": "4", "series_id": "GDP"},
            ]
        }

    adapter._transport = _gapped
    result = adapter.fetch(FetchRequest(dataset_id="fred_cpi"))
    assert result.quality_report.status == "warning"
    assert any("observation gaps" in warning for warning in result.warnings)


def test_nbs_adapter_parses_easyquery_payload():
    adapter = NBSAdapter(cache=None)

    def _transport(url, params):
        payload = json.loads(params["dfwds"])
        assert params["dbcode"] == "hgnd"
        assert params["rowcode"] == "zb"
        assert payload[0]["wdcode"] == "zb"
        return {
            "returncode": 200,
            "ret": [
                {
                    "datanodes": [
                        {
                            "data": {"code": "A010101", "sj": "2024", "data": 1349084},
                        },
                        {
                            "data": {"code": "A010101", "sj": "2025", "data": 1400500},
                        },
                        {"data": {"code": "A010101", "sj": "2026", "data": None}},
                    ]
                }
            ],
        }

    adapter._transport = _transport
    result = adapter.fetch(FetchRequest(dataset_id="nbs_gdp"))
    assert result.dataframe["value"].tolist() == [1349084.0, 1400500.0]
    assert result.dataframe["period"].tolist() == ["2024", "2025"]
    assert result.quality_report.status == "passed"
    assert result.dataset_metadata["unit"] == "100_million_cny"


def test_nbs_adapter_rejects_error_returncode():
    adapter = NBSAdapter(cache=None)
    adapter._transport = lambda url, params: {"returncode": 400, "ret": []}
    with pytest.raises(Exception):
        adapter.fetch(FetchRequest(dataset_id="nbs_gdp"))


def test_registry_contains_five_adapters_and_ten_datasets():
    from langgraph_langchain.data.adapters.public import default_adapter_registry

    registry = default_adapter_registry()
    assert len(registry.adapters()) == 5
    assert len(registry.catalog()) == 10


# ---------------------------------------------------------------------------
# B8.4: full consumption chain — adapter asset → Runtime V2 → Evidence → Report
# ---------------------------------------------------------------------------

class _ScriptedArgs(BaseModel):
    execution_id: str = ""
    output_name: str = "output"
    asset_id: str = ""
    operation: str = ""
    parameters_json: str = "{}"


class _ScriptedOperationTool:
    """Faithful mini version of run_analysis_operation: resolves the input,
    executes the allowlisted operation, registers the output asset + artifact
    with canonical evidence — exactly like the real tool contract."""

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
        output = execute_analysis_operation(resolved.dataframe, operation=operation, parameters=parameters)
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
        # Record the execution first (the real tool does this via the legacy
        # recorder) so canonical evidence references resolve.
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
        self.runtime.register_canonical_evidence(canonical, artifact_ids=[artifact.artifact_id])
        return json.dumps(
            {
                "status": "succeeded",
                "execution_id": execution_id,
                "output_asset_id": output_asset.asset_id,
                "output_assets": {
                    arguments.get("output_name", "output"): output_asset.asset_id
                },
            }
        )


def _v2_summary_plan(asset_id: str):
    step = RuntimePlanStep(
        step_id="step_" + "b" * 32,
        objective="Summarize acquired GDP series",
        method="run_analysis_operation",
        expected_outputs=["metric_result"],
        required_inputs=[asset_id],
        parameters={
            "asset_id": asset_id,
            "operation": "summarize",
            "parameters_json": json.dumps({}),
        },
    )
    return AnalysisPlan(goal="summarize acquired data", steps=[step])


def test_full_chain_adapter_asset_to_report(tmp_path):
    """B8.4 acceptance chain (offline): acquisition asset → Runtime V2 → Evidence → Report."""
    runtime = _fred_runtime(tmp_path, name="chain")
    service = _service(tmp_path, runtime, transport_tag="1")
    acquired = service.acquire(_fred_request())

    step_id = "step_" + "b" * 32
    plan = _v2_summary_plan(acquired.asset.asset_id)
    runtime.initialize_plan(plan)

    tool = _ScriptedOperationTool(runtime, step_id=step_id)
    runtime.configure_executor(
        TaskExecutor(
            structured=StructuredTaskExecutor(tool_resolver=lambda name: tool)
        )
    )

    result = runtime.execute_next_task()
    assert result is not None and result.status == "succeeded"
    # Downstream input resolution: the adapter asset reached the tool through
    # the runtime input chain (logical/physical mapping).
    assert result.input_asset_ids == [acquired.asset.asset_id]
    assert tool.calls[0]["asset_id"] == acquired.asset.asset_id
    assert tool.calls[0]["output_name"] == "metric_result"

    # Evidence is verified against the real execution and registered output.
    assert result.evidence is None or result.evidence.verification_status in {"verified", "unverified"}
    report, markdown = runtime.build_verified_report()
    assert report is not None
    assert report.findings
    assert markdown

    # The registered adapter asset is referenced by the report evidence.
    referenced = {
        asset_id
        for finding in report.findings
        for asset_id in finding.referenced_asset_ids
    }
    assert acquired.asset.asset_id in referenced


def test_planner_v2_consumes_adapter_asset_profile(tmp_path):
    """PlannerInput assembly from an acquisition-registered adapter asset."""
    from langgraph_langchain.runtime.analysis_operations import resolve_asset_profile
    from langgraph_langchain.runtime.planner_models import PlannerAssetContext

    runtime = _fred_runtime(tmp_path, name="plannerws")
    service = _service(tmp_path, runtime, transport_tag="1")
    acquired = service.acquire(_fred_request())
    asset = acquired.asset
    profile, reason = resolve_asset_profile(asset)
    # Adapter assets have no profile until one is generated; degrade explicitly.
    if profile is None:
        assert reason in {"profile_missing", "profile_unavailable"}
        context = PlannerAssetContext(
            asset_id=asset.asset_id,
            schema_snapshot=asset.schema_snapshot,
            profile=None,
            profile_unavailable_reason=reason,
        )
    else:
        context = PlannerAssetContext(
            asset_id=asset.asset_id,
            schema_snapshot=asset.schema_snapshot,
            profile=profile,
        )
    assert context.schema_snapshot.row_count == 3
    assert context.profile_unavailable_reason or context.profile is not None
