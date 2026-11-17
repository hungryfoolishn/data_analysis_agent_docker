"""B10.9.2 E2E test: China PDF -> Acquisition -> Snapshot -> Runtime -> Evidence."""
import hashlib
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, ".")

from langgraph_langchain.data.adapters.base import FetchRequest
from langgraph_langchain.data.adapters.registry import AdapterRegistry
from langgraph_langchain.data.acquisition import AcquisitionService
from langgraph_langchain.evidence.registry import EvidenceRegistry
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.financial.asset_bridge import (
    FinancialAssetBridgeError,
    load_financial_context_from_snapshot,
)
from langgraph_langchain.runtime.financial.china_pdf_adapter import ChinaPdfFilingsAdapter
from langgraph_langchain.runtime.financial.runtime_integration import FinancialDomainRuntime

_PARAMS = {
    "document_url": "https://static.cninfo.com.cn/test.pdf",
    "announcement_id": "test001",
    "title": "2024年度报告",
    "entity_id": "cik_test_cn",
    "published_at": "2025-01-01",
}


def _load_test_pdf():
    fixture = Path(__file__).parent / "fixtures" / "financial" / "china" / "test_annual_report.pdf"
    if not fixture.exists():
        pytest.skip("test annual report PDF fixture not found")
    return fixture.read_bytes()


def _acquire_and_bridge(tmp_path):
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
    runtime = AnalysisRuntime(workspace_dir=ws, session_id="cn-pdf-runtime", question="China PDF E2E")
    service = AcquisitionService(runtime=runtime, registry=registry, history_dir=ws / "history")
    outcome = service.acquire(FetchRequest(dataset_id="cn_financial_pdf", parameters=dict(_PARAMS)))
    snap = outcome.asset.source_metadata["snapshot_reference"]
    snap_path = ws / ".data_snapshots" / snap["snapshot_location"]
    ctx = load_financial_context_from_snapshot(
        snapshot_path=snap_path,
        asset=outcome.asset,
        entity_id="cik_test_cn",
        statement_type="income",
        fiscal_year=2024,
        fiscal_period="FY",
        currency="CNY",
        metric_id="revenue",
    )
    return outcome, snap_path, ctx


class TestAssetBackedRuntime:
    def test_context_loads_from_snapshot(self, tmp_path):
        outcome, _, ctx = _acquire_and_bridge(tmp_path)
        assert ctx.input_asset_ids == (outcome.asset.asset_id,)
        assert len(ctx.statements) == 1
        stmt = ctx.statements[0]
        assert stmt.statement_type == "income"
        assert stmt.entity_id == "cik_test_cn"
        assert stmt.currency == "CNY"
        assert len(stmt.line_items) > 0
        concepts = {li.canonical_concept for li in stmt.line_items}
        assert "revenue" in concepts

    def test_context_decimal_precision_preserved(self, tmp_path):
        _, _, ctx = _acquire_and_bridge(tmp_path)
        stmt = ctx.statements[0]
        rev = next(li for li in stmt.line_items if li.canonical_concept == "revenue")
        assert rev.value == Decimal("800000.00")
        assert isinstance(rev.value, Decimal)

    def test_runtime_platform_mode_produces_evidence(self, tmp_path):
        outcome, _, ctx = _acquire_and_bridge(tmp_path)
        evidence_registry = EvidenceRegistry(assets={outcome.asset.asset_id: outcome.asset})
        dom_runtime = FinancialDomainRuntime(
            context=ctx,
            evidence_registry=evidence_registry,
            mode="platform_verified_execution",
        )
        result = dom_runtime.run(
            ctx.intent,
            session_id="cn-pdf-runtime",
            run_id="cn-pdf-run-001",
            task_id="task_cn_pdf_e2e_001",
        )
        assert result.indicator.status == "calculated"
        assert result.indicator.value == Decimal("800000.00")
        assert result.evidence is not None
        assert list(result.evidence.input_asset_ids) == [outcome.asset.asset_id]

    def test_runtime_offline_mode_does_not_require_evidence(self, tmp_path):
        _, _, ctx = _acquire_and_bridge(tmp_path)
        dom_runtime = FinancialDomainRuntime(context=ctx, mode="offline_calculation")
        result = dom_runtime.run(ctx.intent, session_id="offline-test", run_id="offline-run")
        assert result.indicator.status == "calculated"
        assert result.evidence is None

    def test_bridge_rejects_missing_snapshot(self, tmp_path):
        outcome, _, _ = _acquire_and_bridge(tmp_path)
        with pytest.raises(FinancialAssetBridgeError, match="snapshot not found"):
            load_financial_context_from_snapshot(
                snapshot_path=tmp_path / "nonexistent.parquet",
                asset=outcome.asset,
                entity_id="cik_test_cn",
                statement_type="income",
                fiscal_year=2024,
                fiscal_period="FY",
                currency="CNY",
                metric_id="revenue",
            )

    def test_bridge_rejects_unknown_entity(self, tmp_path):
        outcome, snap_path, _ = _acquire_and_bridge(tmp_path)
        with pytest.raises(FinancialAssetBridgeError):
            load_financial_context_from_snapshot(
                snapshot_path=snap_path,
                asset=outcome.asset,
                entity_id="nonexistent_entity",
                statement_type="income",
                fiscal_year=2024,
                fiscal_period="FY",
                currency="CNY",
                metric_id="revenue",
            )
