"""Opt-in external Financial source smoke tests.

These tests are never part of the offline suite unless
``RUN_FINANCIAL_EXTERNAL_SMOKE=1`` is set.  They are intentionally bounded to
one request per source and do not write fixtures.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.external_integration

_ENABLED = os.getenv("RUN_FINANCIAL_EXTERNAL_SMOKE") == "1"
_SEC_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/Revenues.json"
_SEC_ASSETS_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/Assets.json"
_CN_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
_USER_AGENT = "data_analysis_agent research@example.com"


@pytest.mark.skipif(not _ENABLED, reason="RUN_FINANCIAL_EXTERNAL_SMOKE is not enabled")
def test_sec_company_concept_live_smoke(tmp_path):
    import requests

    from langgraph_langchain.data.adapters.base import stable_json
    from langgraph_langchain.runtime.financial.sec_raw import parse_sec_company_concept

    response = requests.get(
        _SEC_URL,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        timeout=15,
    )
    response.raise_for_status()
    if len(response.content) > 2_000_000:
        pytest.fail("SEC response exceeded the smoke-test size limit")
    response_hash = hashlib.sha256(response.content).hexdigest()
    payload = response.json()
    parsed = parse_sec_company_concept(
        payload,
        taxonomy="us-gaap",
        concept="Revenues",
        preferred_unit="USD",
        source_url=_SEC_URL,
    )
    assert parsed.cik == "320193"
    assert parsed.entity_name
    duration_facts = [fact for fact in parsed.facts if fact.period_type == "duration"]
    instant_facts = [fact for fact in parsed.facts if fact.period_type == "instant"]
    # Company Concept may return either shape depending on concept; require at
    # least one fact, accession, and traceability.  The broader duration/instant
    # combination is verified by offline raw fixtures.
    assert parsed.facts
    assert duration_facts or instant_facts
    assert all(fact.accession_number for fact in parsed.facts)
    assert all(fact.filing_date for fact in parsed.facts)
    assert all(fact.source_response_hash == hashlib.sha256(stable_json(dict(payload)).encode("utf-8")).hexdigest() for fact in parsed.facts)

    assets_response = requests.get(
        _SEC_ASSETS_URL,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        timeout=15,
    )
    assets_response.raise_for_status()
    if len(assets_response.content) > 2_000_000:
        pytest.fail("SEC assets response exceeded the smoke-test size limit")
    assets_payload = assets_response.json()
    assets = parse_sec_company_concept(
        assets_payload,
        taxonomy="us-gaap",
        concept="Assets",
        preferred_unit="USD",
        source_url=_SEC_ASSETS_URL,
    )
    assert any(fact.period_type == "instant" for fact in assets.facts)
    assert all(fact.accession_number for fact in assets.facts)
    assert all(fact.filing_date for fact in assets.facts)
    assert all(
        fact.source_response_hash
        == hashlib.sha256(stable_json(dict(assets_payload)).encode("utf-8")).hexdigest()
        for fact in assets.facts
    )

    from langgraph_langchain.data.adapters.base import FetchRequest
    from langgraph_langchain.data.adapters.registry import AdapterRegistry
    from langgraph_langchain.data.acquisition import AcquisitionService
    from langgraph_langchain.runtime.context import AnalysisRuntime
    from langgraph_langchain.runtime.financial.sec import SecCompanyFactsAdapter

    responses = {
        _SEC_URL: payload,
        _SEC_ASSETS_URL: assets_payload,
    }
    adapter = SecCompanyFactsAdapter(transport=lambda url, params: responses[url])
    registry = AdapterRegistry()
    registry.register(adapter)
    runtime = AnalysisRuntime(
        workspace_dir=tmp_path / "workspace",
        session_id="sec-live-acquisition",
        question="live SEC company concept smoke",
    )
    service = AcquisitionService(
        runtime=runtime,
        registry=registry,
        history_dir=tmp_path / "history",
    )
    revenue_outcome = service.acquire(
        FetchRequest(
            dataset_id="sec_company_facts",
            parameters={
                "cik": "0000320193",
                "canonical_concept": "Revenues",
                "preferred_unit": "USD",
                "entity_id": "cik320193",
                "statement_type": "income",
                "reporting_basis": "consolidated",
                "statistic_kind": "absolute_amount",
            },
        )
    )
    assets_outcome = service.acquire(
        FetchRequest(
            dataset_id="sec_company_facts",
            parameters={
                "cik": "0000320193",
                "canonical_concept": "Assets",
                "preferred_unit": "USD",
                "entity_id": "cik320193",
                "statement_type": "balance",
                "reporting_basis": "consolidated",
                "statistic_kind": "absolute_amount",
            },
        )
    )
    assert revenue_outcome.asset.source_metadata["source_id"] == "financial_sec_company_facts"
    assert assets_outcome.asset.source_metadata["source_id"] == "financial_sec_company_facts"
    assert revenue_outcome.asset.source_metadata["snapshot_reference"]["snapshot_id"]
    assert assets_outcome.asset.source_metadata["snapshot_reference"]["snapshot_id"]

    report = {
        "source": "sec_companyconcept",
        "request_timestamp": response.headers.get("date"),
        "request_type": "GET",
        "response_status": response.status_code,
        "response_hash": response_hash,
        "parser_version": "b10.8.1",
        "normalized_record_count": len(parsed.facts) + len(assets.facts),
        "validation_result": "PASS",
    }
    (tmp_path / "sec_smoke_report.json").write_text(json.dumps(report, indent=2))


@pytest.mark.skipif(not _ENABLED, reason="RUN_FINANCIAL_EXTERNAL_SMOKE is not enabled")
def test_china_announcement_live_smoke_is_bounded_and_honest():
    import requests

    from langgraph_langchain.data.adapters.base import stable_json
    from langgraph_langchain.runtime.financial.china_documents import (
        parse_china_announcement_list,
    )

    body = {
        "pageNum": 1,
        "pageSize": 1,
        "column": "szse",
        "tabName": "fulltext",
        "plate": "sh",
        "stock": "600000,gssh0600000",
        "searchkey": "",
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": "2024-01-01~2024-12-31",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    response = requests.post(
        _CN_URL,
        data=body,
        headers={"User-Agent": _USER_AGENT},
        timeout=15,
    )
    response.raise_for_status()
    if len(response.content) > 1_000_000:
        pytest.fail("China response exceeded the smoke-test size limit")
    response_hash = hashlib.sha256(response.content).hexdigest()
    payload = response.json()
    payload["source_url"] = _CN_URL
    parsed = parse_china_announcement_list(payload)
    assert parsed.announcements
    assert all(item.announcement_id for item in parsed.announcements)
    assert all(item.document_url.startswith("https://") for item in parsed.announcements)
    assert all(item.source_response_hash == hashlib.sha256(stable_json(dict(payload)).encode("utf-8")).hexdigest() for item in parsed.announcements)

    # The announcement layer is live-validated, but the implemented lossless
    # financial parser does not yet consume PDF filing documents.  This is an
    # explicit BLOCKED gate, not a production-readiness pass.
    pytest.skip("China PDF filing-document parser is BLOCKED")
