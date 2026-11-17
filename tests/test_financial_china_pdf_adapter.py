"""B10.9.2 E2E test: China PDF -> AcquisitionService -> DataAsset -> SnapshotStore -> Runtime -> Evidence."""
import hashlib
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, ".")

from langgraph_langchain.data.adapters.base import FetchRequest
from langgraph_langchain.data.adapters.registry import AdapterRegistry
from langgraph_langchain.data.acquisition import AcquisitionService
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.financial.china_pdf_adapter import ChinaPdfFilingsAdapter


def _load_test_pdf():
    fixture = Path(__file__).parent / "fixtures" / "financial" / "china" / "test_annual_report.pdf"
    if not fixture.exists():
        pytest.skip("test annual report PDF fixture not found")
    return fixture.read_bytes()


_PARAMS = {
    "document_url": "https://static.cninfo.com.cn/test.pdf",
    "announcement_id": "test001",
    "title": "2024年度报告",
    "entity_id": "cik_test_cn",
    "published_at": "2025-01-01",
}


def _make_service(tmp_path):
    pdf_bytes = _load_test_pdf()
    adapter = ChinaPdfFilingsAdapter()
    adapter._fetch_raw = lambda request, spec: {
        "pdf_bytes": pdf_bytes,
        "content_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "file_size": len(pdf_bytes),
        "url": "https://static.cninfo.com.cn/test.pdf",
    }
    registry = AdapterRegistry()
    registry.register(adapter)
    ws = tmp_path / "workspace"
    runtime = AnalysisRuntime(workspace_dir=ws, session_id="cn-pdf-e2e", question="China PDF E2E test")
    service = AcquisitionService(runtime=runtime, registry=registry, history_dir=ws / "history")
    return service, pdf_bytes, ws


class TestChinaPdfAcquisition:
    def test_acquisition_produces_dataasset_with_snapshot(self, tmp_path):
        service, _, ws = _make_service(tmp_path)
        outcome = service.acquire(FetchRequest(dataset_id="cn_financial_pdf", parameters=dict(_PARAMS)))
        asset = outcome.asset
        assert asset.source_metadata["source_id"] == "financial_cn_company_pdf"
        snap = asset.source_metadata["snapshot_reference"]
        assert snap["snapshot_id"]
        assert snap["row_count"] > 5
        assert snap["column_count"] >= 17
        assert snap["snapshot_hash"]
        assert snap["content_hash"]

    def test_snapshot_parquet_has_decimal_values(self, tmp_path):
        import pandas as pd
        service, _, ws = _make_service(tmp_path)
        outcome = service.acquire(FetchRequest(dataset_id="cn_financial_pdf", parameters=dict(_PARAMS)))
        snap = outcome.asset.source_metadata["snapshot_reference"]
        snap_path = ws / ".data_snapshots" / snap["snapshot_location"]
        assert snap_path.exists(), f"snapshot not found: {snap_path}"
        frame = pd.read_parquet(snap_path)
        assert "revenue" in frame["canonical_concept"].values
        assert "total_assets" in frame["canonical_concept"].values
        rev = frame[frame["canonical_concept"] == "revenue"]
        assert Decimal(str(rev.iloc[0]["value"])) == Decimal("800000.00")
        assert Decimal(str(frame[frame["canonical_concept"] == "total_assets"].iloc[0]["value"])) == Decimal("500000.00")

    def test_duplicate_acquisition_dedup(self, tmp_path):
        service, _, _ = _make_service(tmp_path)
        o1 = service.acquire(FetchRequest(dataset_id="cn_financial_pdf", parameters=dict(_PARAMS)))
        o2 = service.acquire(FetchRequest(dataset_id="cn_financial_pdf", parameters=dict(_PARAMS)))
        assert o1.asset.asset_id == o2.asset.asset_id

    def test_history_records_version(self, tmp_path):
        service, _, _ = _make_service(tmp_path)
        service.acquire(FetchRequest(dataset_id="cn_financial_pdf", parameters=dict(_PARAMS)))
        latest = service.history.latest("cn_financial_pdf")
        assert latest is not None
        assert latest.get("adapter_id") == "financial_cn_company_pdf"
