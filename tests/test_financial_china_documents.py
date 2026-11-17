import json
from decimal import Decimal
from pathlib import Path

import pytest

from langgraph_langchain.data.adapters.base import AdapterRequestError, FetchRequest
from langgraph_langchain.data.adapters.registry import AdapterRegistry
from langgraph_langchain.data.acquisition import AcquisitionService
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.financial.china import ChinaCompanyFilingsAdapter
from langgraph_langchain.runtime.financial.china_documents import (
    ChinaAnnouncementParseError,
    ChinaDocumentUnsupportedError,
    ChinaStatementParseError,
    ChinaStatementPeriod,
    china_fact_to_row,
    detect_china_document_type,
    parse_china_announcement_list,
    parse_china_statement_csv,
)

FIXTURES = Path(__file__).parent / "fixtures" / "financial" / "china"


def _announcement():
    payload = json.loads((FIXTURES / "announcement_list.json").read_text(encoding="utf-8"))
    return parse_china_announcement_list(payload).announcements[0]


def _period():
    return ChinaStatementPeriod(
        period_start="2024-01-01",
        period_end="2024-03-31",
        fiscal_year=2024,
        fiscal_period="Q1",
        reporting_basis="consolidated",
    )


def _mapping():
    return {
        "营业收入": "revenue",
        "营业成本": "cost_of_sales",
        "净利润": "net_profit",
        "资产总计": "total_assets",
        "负债合计": "total_liabilities",
        "经营活动产生的现金流量净额": "operating_cash_flow",
        "投资活动产生的现金流量净额": "investing_cash_flow",
    }


def test_announcement_metadata_is_not_financial_data():
    payload = json.loads((FIXTURES / "announcement_list.json").read_text(encoding="utf-8"))
    parsed = parse_china_announcement_list(payload)
    assert len(parsed.announcements) == 1
    item = parsed.announcements[0]
    assert item.announcement_id == "ann-2024-0001"
    assert item.document_url == "https://static.cninfo.com.cn/finalpage/600000/2024/ann-2024-0001.pdf"
    assert not hasattr(item, "canonical_concept")
    assert not hasattr(item, "value")


def test_correction_announcement_is_preserved_as_a_distinct_version():
    payload = json.loads((FIXTURES / "announcement_detail.json").read_text(encoding="utf-8"))
    parsed = parse_china_announcement_list(payload)
    ids = [item.announcement_id for item in parsed.announcements]
    assert ids == ["ann-2024-0001", "ann-2024-0001-C"]
    assert parsed.announcements[1].announcement_type == "correction"


def test_announcement_missing_traceability_is_rejected():
    payload = json.loads((FIXTURES / "announcement_list.json").read_text(encoding="utf-8"))
    payload["announcements"][0]["announcementId"] = ""
    with pytest.raises(ChinaAnnouncementParseError, match="announcement id"):
        parse_china_announcement_list(payload)


def test_document_type_detection_rejects_pdf_without_text_scraping():
    assert detect_china_document_type(b"%PDF-1.7\n") == "pdf"
    assert detect_china_document_type(b"PK\x03\x04") == "zip"
    with pytest.raises(ChinaDocumentUnsupportedError, match="pdf"):
        parse_china_statement_csv(
            b"%PDF-1.7\n",
            announcement=_announcement(),
            period=_period(),
            statement_type="income",
            canonical_concepts=_mapping(),
        )


@pytest.mark.parametrize(
    "filename,statement_type,expected",
    [
        ("statement_income_sample.csv", "income", [("revenue", Decimal("1234.56")), ("cost_of_sales", Decimal("-987.65")), ("net_profit", None)]),
        ("statement_balance_sample.csv", "balance", [("total_assets", Decimal("5000.00")), ("total_liabilities", None)]),
        ("statement_cashflow_sample.csv", "cash", [("operating_cash_flow", Decimal("600.00")), ("investing_cash_flow", Decimal("-100.00"))]),
    ],
)
def test_statement_csv_is_parsed_with_semantics(filename, statement_type, expected):
    document = (FIXTURES / filename).read_bytes()
    facts = parse_china_statement_csv(
        document,
        announcement=_announcement(),
        period=_period(),
        statement_type=statement_type,
        canonical_concepts=_mapping(),
        unit="元",
        value_column=("期末余额" if statement_type == "balance" else "本期金额"),
    )
    assert [(fact.canonical_concept, fact.value) for fact in facts] == expected
    assert all(fact.currency == "CNY" for fact in facts)
    assert all(fact.document_hash for fact in facts)
    assert all(fact.source_document_id.startswith("cn:ann-2024-0001:") for fact in facts)


def test_quantity_unit_conversion_is_not_fx_conversion():
    document = (FIXTURES / "statement_income_sample.csv").read_bytes()
    facts = parse_china_statement_csv(
        document,
        announcement=_announcement(),
        period=_period(),
        statement_type="income",
        canonical_concepts=_mapping(),
        unit="万元",
    )
    assert facts[0].value == Decimal("12345600")
    assert facts[0].unit == "万元"


def test_unknown_mapping_and_invalid_number_are_rejected():
    document = (FIXTURES / "statement_income_sample.csv").read_bytes()
    with pytest.raises(ChinaStatementParseError, match="no canonical mapping"):
        parse_china_statement_csv(
            document,
            announcement=_announcement(),
            period=_period(),
            statement_type="income",
            canonical_concepts={"营业收入": "revenue"},
        )
    with pytest.raises(ChinaStatementParseError, match="invalid numeric"):
        parse_china_statement_csv(
            document,
            announcement=_announcement(),
            period=_period(),
            statement_type="income",
            canonical_concepts=_mapping(),
            value_column="报表项目",
        )


def test_china_announcement_adapter_acquires_existing_data_asset(tmp_path):
    payload = json.loads((FIXTURES / "announcement_detail.json").read_text(encoding="utf-8"))
    adapter = ChinaCompanyFilingsAdapter(transport=lambda url, params: payload)
    result = adapter.fetch(
        FetchRequest(dataset_id="cn_announcements", parameters={"stock_code": "600000"})
    )
    assert result.quality_report.is_usable
    assert len(result.dataframe) == 2
    assert result.dataframe.iloc[0]["announcement_id"] == "ann-2024-0001"
    assert result.dataframe.iloc[1]["announcement_id"] == "ann-2024-0001-C"

    runtime = AnalysisRuntime(
        workspace_dir=tmp_path / "workspace",
        session_id="china-announcement-acq",
        question="discover China announcement metadata",
    )
    registry = AdapterRegistry()
    registry.register(adapter)
    service = AcquisitionService(runtime=runtime, registry=registry, history_dir=tmp_path / "history")
    outcome = service.acquire(
        FetchRequest(dataset_id="cn_announcements", parameters={"stock_code": "600000"})
    )
    assert outcome.asset.source_metadata["source_id"] == "financial_cn_company_filings"
    assert outcome.asset.source_metadata["snapshot_reference"]["row_count"] == 2


def test_parsed_document_rows_acquire_through_existing_b8_service(tmp_path):
    document = (FIXTURES / "statement_income_sample.csv").read_bytes()
    facts = parse_china_statement_csv(
        document,
        announcement=_announcement(),
        period=_period(),
        statement_type="income",
        canonical_concepts=_mapping(),
        unit="元",
    )
    rows = [china_fact_to_row(fact, entity_id="ent_cn") for fact in facts if fact.value is not None]
    adapter = ChinaCompanyFilingsAdapter(transport=lambda url, params: rows)
    result = adapter.fetch(
        FetchRequest(dataset_id="cn_company_filings", parameters={"stock_code": "600000"})
    )
    assert result.quality_report.is_usable
    assert len(result.dataframe) == 2
    values = result.dataframe.sort_values("canonical_concept")["value"].tolist()
    assert values == ["-987.65", "1234.56"]

    runtime = AnalysisRuntime(
        workspace_dir=tmp_path / "workspace",
        session_id="china-document-acq",
        question="acquire parsed China document facts",
    )
    registry = AdapterRegistry()
    registry.register(adapter)
    service = AcquisitionService(runtime=runtime, registry=registry, history_dir=tmp_path / "history")
    outcome = service.acquire(
        FetchRequest(dataset_id="cn_company_filings", parameters={"stock_code": "600000"})
    )
    assert outcome.asset.source_metadata["source_id"] == "financial_cn_company_filings"
    assert outcome.asset.source_metadata["snapshot_reference"]["row_count"] == 2


def test_http_failure_is_explicit():
    def failing_transport(url, params):
        raise TimeoutError("CNINFO timeout")

    adapter = ChinaCompanyFilingsAdapter(transport=failing_transport)
    with pytest.raises(Exception, match="CNINFO timeout"):
        adapter.fetch(
            FetchRequest(dataset_id="cn_announcements", parameters={"stock_code": "600000"})
        )
