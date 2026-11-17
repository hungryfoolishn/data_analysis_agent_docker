"""B10.9 opt-in external China PDF smoke test.

Requires ``RUN_FINANCIAL_EXTERNAL_SMOKE=1``. One bounded request per source.
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest

pytestmark = pytest.mark.external_integration

_ENABLED = os.getenv("RUN_FINANCIAL_EXTERNAL_SMOKE") == "1"
_CN_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
_USER_AGENT = "data_analysis_agent research@example.com"


@pytest.mark.skipif(not _ENABLED, reason="RUN_FINANCIAL_EXTERNAL_SMOKE is not enabled")
def test_china_annual_report_pdf_live_smoke(tmp_path):
    import requests

    from langgraph_langchain.runtime.financial.china_pdf import (
        download_china_announcement_pdf,
        parse_china_statement_pdf,
        map_china_pdf_rows,
    )
    from langgraph_langchain.runtime.financial.china_documents import (
        parse_china_announcement_list,
    )

    # 1. Discovery
    body = {
        "pageNum": 1, "pageSize": 10, "column": "szse", "tabName": "fulltext",
        "plate": "sh", "stock": "600000,gssh0600000", "searchkey": "年度报告",
        "secid": "", "category": "", "trade": "",
        "seDate": "2024-01-01~2025-06-30", "sortName": "", "sortType": "", "isHLtitle": "true",
    }
    resp = requests.post(_CN_QUERY_URL, data=body, headers={"User-Agent": _USER_AGENT}, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    payload["source_url"] = _CN_QUERY_URL
    ann_list = parse_china_announcement_list(payload)
    assert ann_list.announcements

    ann = None
    for a in ann_list.announcements:
        t = a.title.replace("<em>", "").replace("</em>", "")
        if "半年度报告" in t and "摘要" not in t:
            ann = a
            break
    if ann is None:
        pytest.skip("no half-year report found")

    # 2. Download
    doc = download_china_announcement_pdf(ann.document_url, max_bytes=10_000_000, timeout=60)
    assert doc.is_pdf
    assert doc.file_size > 100_000

    # 3. Parse
    result = parse_china_statement_pdf(doc.content)
    assert result.statements
    statement_types = {s.statement_type for s in result.statements}
    assert len(statement_types) >= 1

    # 4. Map
    total_facts = 0
    for block in result.statements:
        facts, _ = map_china_pdf_rows(
            block, period_start="2024-01-01", period_end="2024-06-30",
            fiscal_year=2024, fiscal_period="H1",
            announcement_id=ann.announcement_id,
            announcement_url=ann.document_url,
            published_at=ann.announcement_time,
            document_sha256=doc.content_sha256,
        )
        total_facts += len(facts)

    assert total_facts > 0, "no facts were mapped from the real PDF"

    report = {
        "source": "cninfo_half_year_report_pdf",
        "company": "上海浦东发展银行股份有限公司",
        "stock_code": "600000",
        "announcement_id": ann.announcement_id,
        "document_url": ann.document_url,
        "http_status": resp.status_code,
        "file_size": doc.file_size,
        "content_sha256": doc.content_sha256,
        "parser_version": "b10.9.2",
        "statements_found": sorted(statement_types),
        "total_facts": total_facts,
        "validation_result": "PASS",
    }
    (tmp_path / "cninfo_pdf_smoke_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
