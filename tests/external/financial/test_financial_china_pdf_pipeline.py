"""B10.9.2 external smoke: full production chain from real CNINFO PDF to Evidence."""
import os, sys, hashlib
from decimal import Decimal
from pathlib import Path
import pytest

pytestmark = pytest.mark.external_integration
_ENABLED = os.getenv("RUN_FINANCIAL_EXTERNAL_SMOKE") == "1"

_CN_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
_UA = "data_analysis_agent research@example.com"


@pytest.mark.skipif(not _ENABLED, reason="RUN_FINANCIAL_EXTERNAL_SMOKE is not enabled")
def test_china_pdf_production_pipeline_e2e(tmp_path):
    import requests
    import pandas as pd
    from langgraph_langchain.data.adapters.base import FetchRequest
    from langgraph_langchain.data.adapters.registry import AdapterRegistry
    from langgraph_langchain.data.acquisition import AcquisitionService
    from langgraph_langchain.evidence.registry import EvidenceRegistry
    from langgraph_langchain.runtime.context import AnalysisRuntime
    from langgraph_langchain.runtime.financial.china_pdf_adapter import ChinaPdfFilingsAdapter
    from langgraph_langchain.runtime.financial.asset_bridge import load_financial_context_from_snapshot
    from langgraph_langchain.runtime.financial.china_documents import parse_china_announcement_list
    from langgraph_langchain.runtime.financial.runtime_integration import FinancialDomainRuntime

    # 1. Discovery
    body = {
        "pageNum": 1, "pageSize": 10, "column": "szse", "tabName": "fulltext",
        "plate": "sh", "stock": "600000,gssh0600000", "searchkey": "年度报告",
        "secid": "", "category": "", "trade": "",
        "seDate": "2024-01-01~2025-06-30", "sortName": "", "sortType": "", "isHLtitle": "true",
    }
    resp = requests.post(_CN_QUERY_URL, data=body, headers={"User-Agent": _UA}, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    payload["source_url"] = _CN_QUERY_URL
    ann_list = parse_china_announcement_list(payload)
    ann = None
    for a in ann_list.announcements:
        t = a.title.replace("<em>", "").replace("</em>", "")
        if "半年度报告" in t and "摘要" not in t:
            ann = a; break
    if ann is None:
        pytest.skip("no half-year report found")
    clean_title = ann.title.replace("<em>", "").replace("</em>", "")

    # 2. Acquire through production AcquisitionService
    adapter = ChinaPdfFilingsAdapter()
    registry = AdapterRegistry()
    registry.register(adapter)
    ws = tmp_path / "workspace"
    runtime = AnalysisRuntime(workspace_dir=ws, session_id="cn-pdf-e2e-live", question="China PDF E2E live")
    service = AcquisitionService(runtime=runtime, registry=registry, history_dir=ws / "history")
    outcome = service.acquire(FetchRequest(dataset_id="cn_financial_pdf", parameters={
        "document_url": ann.document_url,
        "announcement_id": ann.announcement_id,
        "title": clean_title,
        "entity_id": "cik_600000",
        "published_at": ann.announcement_time,
    }))
    assert outcome.asset is not None
    assert outcome.asset.source_metadata["source_id"] == "financial_cn_company_pdf"

    # 3. Snapshot
    snap = outcome.asset.source_metadata["snapshot_reference"]
    assert snap["snapshot_id"] and snap["row_count"] > 0
    snap_path = ws / ".data_snapshots" / snap["snapshot_location"]
    assert snap_path.exists()

    # 4. Bridge to FinancialDomainRuntime
    ctx = load_financial_context_from_snapshot(
        snapshot_path=snap_path,
        asset=outcome.asset,
        entity_id="cik_600000",
        statement_type="income",
        fiscal_year=2024,
        fiscal_period="H1",
        currency="CNY",
        metric_id="net_profit",
    )
    assert ctx.input_asset_ids == (outcome.asset.asset_id,)

    # 5. Runtime + Evidence
    evidence_registry = EvidenceRegistry(assets={outcome.asset.asset_id: outcome.asset})
    dom = FinancialDomainRuntime(context=ctx, evidence_registry=evidence_registry, mode="platform_verified_execution")
    result = dom.run(ctx.intent, session_id="cn-pdf-e2e-live", run_id="cn-pdf-live-run", task_id="task_cn_pdf_live_001")
    assert result.indicator.status == "calculated"
    assert result.indicator.value > Decimal("0")
    assert result.evidence is not None
    assert list(result.evidence.input_asset_ids) == [outcome.asset.asset_id]

    report = {
        "source": "cninfo_pdf_production_pipeline",
        "company": "上海浦东发展银行股份有限公司",
        "stock_code": "600000",
        "announcement_id": ann.announcement_id,
        "title": clean_title[:100],
        "document_url": ann.document_url,
        "snapshot_id": snap["snapshot_id"],
        "snapshot_row_count": snap["row_count"],
        "asset_id": outcome.asset.asset_id,
        "evidence_input_asset_ids": list(result.evidence.input_asset_ids),
        "indicator_value": str(result.indicator.value),
        "indicator_status": result.indicator.status,
        "validation_result": "PASS",
    }
    (tmp_path / "cn_pdf_pipeline_report.json").write_text(
        Path(report.__str__()).read_text() if False else __import__("json").dumps(report, indent=2, ensure_ascii=False)
    )
