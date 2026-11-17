"""B10.9 China PDF ingestion tests."""
import hashlib
from decimal import Decimal

import pytest

from langgraph_langchain.runtime.financial.china_pdf import (
    ChinaPdfDownloadError,
    ChinaPdfDocument,
    ChinaPdfParseError,
    ChinaPdfUnsupportedError,
    ChinaPdfStatementBlock,
    ChinaPdfParseResult,
    download_china_announcement_pdf,
    parse_china_statement_pdf,
    map_china_pdf_rows,
    _parse_amount,
    _CN_SUBJECT_MAP,
    MAPPING_VERSION,
)


# ---------------------------------------------------------------------------
# Download tests
# ---------------------------------------------------------------------------


class TestPdfDownload:
    def test_download_valid_pdf(self):
        content = b"%PDF-1.4 minimal pdf content"
        doc = download_china_announcement_pdf(
            "https://static.cninfo.com.cn/test.pdf",
            transport=lambda url, timeout: content,
        )
        assert doc.is_pdf
        assert doc.file_size == len(content)
        assert doc.content_sha256 == hashlib.sha256(content).hexdigest()

    def test_download_rejects_non_pdf(self):
        with pytest.raises(ChinaPdfDownloadError, match="magic bytes"):
            download_china_announcement_pdf(
                "https://static.cninfo.com.cn/test.pdf",
                transport=lambda url, timeout: b"not a pdf",
            )

    def test_download_rejects_non_https(self):
        with pytest.raises(ChinaPdfDownloadError, match="non-https"):
            download_china_announcement_pdf(
                "http://evil.com/test.pdf",
                transport=lambda url, timeout: b"%PDF-1.4",
            )

    def test_download_rejects_empty(self):
        with pytest.raises(ChinaPdfDownloadError, match="too small"):
            download_china_announcement_pdf(
                "https://static.cninfo.com.cn/test.pdf",
                transport=lambda url, timeout: b"",
            )

    def test_download_rejects_oversize(self):
        big = b"%PDF-1.4" + b"x" * 100
        with pytest.raises(ChinaPdfDownloadError, match="size limit"):
            download_china_announcement_pdf(
                "https://static.cninfo.com.cn/test.pdf",
                transport=lambda url, timeout: big,
                max_bytes=50,
            )

    def test_download_rejects_transport_error(self):
        def failing_transport(url, timeout):
            raise ConnectionError("network unavailable")

        with pytest.raises(ChinaPdfDownloadError, match="download failed"):
            download_china_announcement_pdf(
                "https://static.cninfo.com.cn/test.pdf",
                transport=failing_transport,
            )


# ---------------------------------------------------------------------------
# Amount parsing tests
# ---------------------------------------------------------------------------


class TestParseAmount:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("100", Decimal("100")),
            ("1,000.50", Decimal("1000.50")),
            ("(500)", Decimal("-500")),
            ("-123.45", Decimal("-123.45")),
            ("0", Decimal("0")),
            ("", None),
            ("--", None),
            ("-", None),
            ("abc", None),
        ],
    )
    def test_parse_amount(self, raw, expected):
        assert _parse_amount(raw) == expected

    def test_thousand_separators(self):
        assert _parse_amount("1,234,567.89") == Decimal("1234567.89")

    def test_parentheses_negative(self):
        assert _parse_amount("(1,500.00)") == Decimal("-1500.00")


# ---------------------------------------------------------------------------
# Mapping table tests
# ---------------------------------------------------------------------------


class TestSubjectMapping:
    def test_mapping_has_minimum_coverage(self):
        # Balance sheet: at least 10 core subjects
        balance = [k for k, v in _CN_SUBJECT_MAP.items() if v[1] == "balance"]
        assert len(balance) >= 10
        # Income: at least 8
        income = [k for k, v in _CN_SUBJECT_MAP.items() if v[1] == "income"]
        assert len(income) >= 8
        # Cash: at least 5
        cash = [k for k, v in _CN_SUBJECT_MAP.items() if v[1] == "cash"]
        assert len(cash) >= 5

    def test_core_subjects_mapped(self):
        expected = {
            "货币资金": ("cash_and_equivalents", "balance"),
            "营业收入": ("revenue", "income"),
            "净利润": ("net_profit", "income"),
            "资产总计": ("total_assets", "balance"),
            "经营活动产生的现金流量净额": ("net_cash_from_operating_activities", "cash"),
        }
        for subject, (concept, stmt) in expected.items():
            assert _CN_SUBJECT_MAP[subject] == (concept, stmt)


# ---------------------------------------------------------------------------
# PDF parsing tests (requires fixture PDF)
# ---------------------------------------------------------------------------


def _load_test_pdf() -> bytes:
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "financial" / "china" / "test_annual_report.pdf"
    if not fixture.exists():
        pytest.skip("test annual report PDF fixture not found")
    return fixture.read_bytes()


class TestPdfParsing:
    def test_parse_multi_statement_pdf(self):
        pdf_bytes = _load_test_pdf()
        result = parse_china_statement_pdf(pdf_bytes)
        assert result.total_pages >= 2
        types = {s.statement_type for s in result.statements}
        assert types == {"balance", "income", "cash"}
        for stmt in result.statements:
            assert stmt.reporting_basis == "consolidated"
            assert len(stmt.raw_rows) > 0

    def test_pdf_has_document_hash(self):
        pdf_bytes = _load_test_pdf()
        result = parse_china_statement_pdf(pdf_bytes)
        assert result.document_sha256 == hashlib.sha256(pdf_bytes).hexdigest()

    def test_page_hashes_present(self):
        pdf_bytes = _load_test_pdf()
        result = parse_china_statement_pdf(pdf_bytes)
        assert len(result.page_hashes) == result.total_pages

    def test_parser_version(self):
        pdf_bytes = _load_test_pdf()
        result = parse_china_statement_pdf(pdf_bytes)
        assert result.parser_version == "b10.9.2"
        assert result.mapping_version == MAPPING_VERSION

    def test_rejects_non_pdf(self):
        with pytest.raises(ChinaPdfParseError):
            parse_china_statement_pdf(b"not a pdf at all")

    def test_rejects_scanned_pdf(self):
        # A PDF with no extractable text
        fake_scanned = b"%PDF-1.4 fake scan" + b"\x00" * 100
        with pytest.raises((ChinaPdfUnsupportedError, ChinaPdfParseError)):
            parse_china_statement_pdf(fake_scanned)


class TestMapping:
    def test_map_all_statements(self):
        pdf_bytes = _load_test_pdf()
        result = parse_china_statement_pdf(pdf_bytes)
        total_facts = 0
        for block in result.statements:
            facts, unmapped = map_china_pdf_rows(
                block,
                period_start="2024-01-01",
                period_end="2024-12-31",
                fiscal_year=2024,
                fiscal_period="FY",
                announcement_id="test001",
                announcement_url="https://static.cninfo.com.cn/test.pdf",
                published_at="2025-01-01",
                document_sha256=result.document_sha256,
            )
            total_facts += len(facts)
            assert all(f["value"] is not None for f in facts)
            assert all(isinstance(f["value"], Decimal) for f in facts)
            assert all(f["announcement_id"] == "test001" for f in facts)
            assert all(f["document_hash"] == result.document_sha256 for f in facts)
        assert total_facts > 10

    def test_balance_period_type_is_instant(self):
        pdf_bytes = _load_test_pdf()
        result = parse_china_statement_pdf(pdf_bytes)
        balance = next(b for b in result.statements if b.statement_type == "balance")
        facts, _ = map_china_pdf_rows(
            balance,
            period_start="2024-01-01",
            period_end="2024-12-31",
            fiscal_year=2024,
            fiscal_period="FY",
            announcement_id="test001",
            announcement_url="https://static.cninfo.com.cn/test.pdf",
            published_at="2025-01-01",
            document_sha256=result.document_sha256,
        )
        for f in facts:
            assert f["period_type"] == "instant"
            assert f["period_start"] is None

    def test_income_period_type_is_duration(self):
        pdf_bytes = _load_test_pdf()
        result = parse_china_statement_pdf(pdf_bytes)
        income = next(b for b in result.statements if b.statement_type == "income")
        facts, _ = map_china_pdf_rows(
            income,
            period_start="2024-01-01",
            period_end="2024-12-31",
            fiscal_year=2024,
            fiscal_period="FY",
            announcement_id="test001",
            announcement_url="https://static.cninfo.com.cn/test.pdf",
            published_at="2025-01-01",
            document_sha256=result.document_sha256,
        )
        for f in facts:
            assert f["period_type"] == "duration"
            assert f["period_start"] == "2024-01-01"

    def test_negative_values_parsed(self):
        pdf_bytes = _load_test_pdf()
        result = parse_china_statement_pdf(pdf_bytes)
        cash = next(b for b in result.statements if b.statement_type == "cash")
        facts, _ = map_china_pdf_rows(
            cash,
            period_start="2024-01-01",
            period_end="2024-12-31",
            fiscal_year=2024,
            fiscal_period="FY",
            announcement_id="test001",
            announcement_url="https://static.cninfo.com.cn/test.pdf",
            published_at="2025-01-01",
            document_sha256=result.document_sha256,
        )
        investing = next(
            f for f in facts if f["canonical_concept"] == "net_cash_from_investing_activities"
        )
        assert investing["value"] == Decimal("-50000.00") * Decimal("1")

    def test_decimal_precision_preserved(self):
        pdf_bytes = _load_test_pdf()
        result = parse_china_statement_pdf(pdf_bytes)
        income = next(b for b in result.statements if b.statement_type == "income")
        facts, _ = map_china_pdf_rows(
            income,
            period_start="2024-01-01",
            period_end="2024-12-31",
            fiscal_year=2024,
            fiscal_period="FY",
            announcement_id="test001",
            announcement_url="https://static.cninfo.com.cn/test.pdf",
            published_at="2025-01-01",
            document_sha256=result.document_sha256,
        )
        revenue = next(f for f in facts if f["canonical_concept"] == "revenue")
        assert revenue["value"] == Decimal("800000.00") * Decimal("1")
        assert not isinstance(revenue["value"], float)


class TestConsistencyChecks:
    def test_balance_sheet_equation(self):
        pdf_bytes = _load_test_pdf()
        result = parse_china_statement_pdf(pdf_bytes)
        balance = next(b for b in result.statements if b.statement_type == "balance")
        facts, _ = map_china_pdf_rows(
            balance,
            period_start="2024-01-01",
            period_end="2024-12-31",
            fiscal_year=2024,
            fiscal_period="FY",
            announcement_id="test001",
            announcement_url="https://static.cninfo.com.cn/test.pdf",
            published_at="2025-01-01",
            document_sha256=result.document_sha256,
        )
        by_concept = {f["canonical_concept"]: f["value"] for f in facts}
        if all(c in by_concept for c in ("total_assets", "total_liabilities", "total_equity")):
            assert by_concept["total_assets"] == by_concept["total_liabilities"] + by_concept["total_equity"]

    def test_income_gross_check(self):
        pdf_bytes = _load_test_pdf()
        result = parse_china_statement_pdf(pdf_bytes)
        income = next(b for b in result.statements if b.statement_type == "income")
        facts, _ = map_china_pdf_rows(
            income,
            period_start="2024-01-01",
            period_end="2024-12-31",
            fiscal_year=2024,
            fiscal_period="FY",
            announcement_id="test001",
            announcement_url="https://static.cninfo.com.cn/test.pdf",
            published_at="2025-01-01",
            document_sha256=result.document_sha256,
        )
        by_concept = {f["canonical_concept"]: f["value"] for f in facts}
        if "revenue" in by_concept and "cost_of_revenue" in by_concept:
            assert by_concept["revenue"] > by_concept["cost_of_revenue"]
