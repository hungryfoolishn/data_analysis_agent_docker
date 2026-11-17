"""B10.9.2 China PDF filings adapter - connects china_pdf.py to B8 AcquisitionService.

This adapter implements the B8 HttpJsonAdapter contract so that real CNINFO
PDFs can flow through: AdapterRegistry -> AcquisitionService.acquire()
-> DataAsset -> SnapshotStore -> FinancialDomainRuntime -> EvidenceRegistry.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

import pandas as pd

from langgraph_langchain.data.adapters.base import (
    AdapterDescriptor,
    AdapterRequestError,
    DatasetSpec,
    FetchRequest,
    HttpJsonAdapter,
    QualityReport,
)
from .china_pdf import (
    download_china_announcement_pdf,
    parse_china_statement_pdf,
    map_china_pdf_rows,
)

__all__ = ["ChinaPdfFilingsAdapter"]

_PDF_COLUMNS = (
    "market",
    "entity_id",
    "canonical_concept",
    "statement_type",
    "period_type",
    "period_start",
    "period_end",
    "fiscal_year",
    "fiscal_period",
    "reporting_basis",
    "currency",
    "value",
    "statistic_kind",
    "unit",
    "accession_number",
    "filing_date",
    "source_document_id",
)


def _detect_period(title: str) -> tuple[str, str, int, str]:
    """Extract period info from the announcement title."""
    year_match = re.search(r"(\d{4})", title)
    year = int(year_match.group(1)) if year_match else 2024
    if "半年度" in title:
        return f"{year}-01-01", f"{year}-06-30", year, "H1"
    if "第一季度" in title:
        return f"{year}-01-01", f"{year}-03-31", year, "Q1"
    if "第三季度" in title:
        return f"{year}-07-01", f"{year}-09-30", year, "Q3"
    return f"{year}-01-01", f"{year}-12-31", year, "FY"


class ChinaPdfFilingsAdapter(HttpJsonAdapter):
    """Adapter that fetches and parses a China financial-report PDF."""

    descriptor = AdapterDescriptor(
        adapter_id="financial_cn_company_pdf",
        version="1.0.0",
        source_type="financial_cn_company_pdf",
        description="China official financial-report PDF adapter with full traceability.",
        requires_credentials=False,
        rate_limit_seconds=1.0,
    )
    datasets = {
        "cn_financial_pdf": DatasetSpec(
            dataset_id="cn_financial_pdf",
            name="China financial-report PDF facts",
            description="Canonical facts parsed from official China financial-report PDFs.",
            unit="CNY",
            frequency="filing_event",
            source_url="https://static.cninfo.com.cn/",
            required_columns=_PDF_COLUMNS,
            time_column="period_end",
            update_policy="filing_event",
        ),
    }

    def _fetch_raw(self, request: FetchRequest, spec: DatasetSpec) -> Any:
        """Download the PDF bytes from the document URL."""
        document_url = request.parameters.get("document_url")
        if not document_url or not isinstance(document_url, str):
            raise AdapterRequestError("document_url is required and must be a string")
        max_bytes = request.parameters.get("max_pdf_bytes", 20_000_000)
        timeout = request.parameters.get("pdf_timeout", 60)
        doc = download_china_announcement_pdf(
            document_url, max_bytes=int(max_bytes), timeout=int(timeout),
        )
        return {"pdf_bytes": doc.content, "content_sha256": doc.content_sha256,
                "file_size": doc.file_size, "url": doc.url}

    def normalize(self, raw: Any, request: FetchRequest, spec: DatasetSpec) -> pd.DataFrame:
        """Parse PDF bytes into canonical financial rows."""
        pdf_bytes = raw["pdf_bytes"]
        sha256 = raw["content_sha256"]
        title = str(request.parameters.get("title") or "")
        announcement_id = str(request.parameters.get("announcement_id") or "")
        entity_id = str(request.parameters.get("entity_id") or "")
        period_start, period_end, fiscal_year, fiscal_period = _detect_period(title)
        unit = str(request.parameters.get("unit") or "元")
        parser_version = request.parameters.get("parser_version", "b10.9.2")

        result = parse_china_statement_pdf(pdf_bytes, content_sha256=sha256,
                                           parser_version=str(parser_version))
        rows: list[dict[str, Any]] = []
        unmapped: list[str] = []
        for block in result.statements:
            facts, unmapped_blk = map_china_pdf_rows(
                block,
                period_start=period_start,
                period_end=period_end,
                fiscal_year=fiscal_year,
                fiscal_period=fiscal_period,
                announcement_id=announcement_id,
                announcement_url=raw.get("url") or "",
                published_at=str(request.parameters.get("published_at") or ""),
                document_sha256=sha256,
                parser_version=str(parser_version),
            )
            unmapped.extend(unmapped_blk)
            for f in facts:
                rows.append({
                    "market": "cn",
                    "entity_id": entity_id,
                    "canonical_concept": f["canonical_concept"],
                    "statement_type": f["statement_type"],
                    "period_type": f["period_type"],
                    "period_start": f["period_start"],
                    "period_end": f["period_end"],
                    "fiscal_year": f["fiscal_year"],
                    "fiscal_period": f["fiscal_period"],
                    "reporting_basis": f["reporting_basis"],
                    "currency": f["currency"],
                    "value": f["value"],
                    "statistic_kind": "absolute_amount",
                    "unit": f["unit"],
                    "accession_number": announcement_id,
                    "filing_date": request.parameters.get("published_at") or "",
                    "source_document_id": (
                        f"cn:{announcement_id}:{sha256[:12]}:{f['row_number']}"
                    ),
                })
        frame = pd.DataFrame(rows, columns=_PDF_COLUMNS)
        if not frame.empty:
            frame["value"] = frame["value"].map(
                lambda item: str(item) if isinstance(item, Decimal) else str(Decimal(str(item)))
            )
        return frame.drop_duplicates(
            subset=["market", "entity_id", "canonical_concept", "statement_type",
                    "period_type", "period_start", "period_end", "fiscal_year",
                    "fiscal_period", "reporting_basis", "currency", "accession_number",
                    "source_document_id"],
            keep="last",
        ).sort_values(
            ["period_end", "period_start", "canonical_concept", "accession_number"],
            kind="stable",
        ).reset_index(drop=True)

    def validate(self, frame: pd.DataFrame, spec: DatasetSpec) -> QualityReport:
        quality = super().validate(frame, spec)
        issues = list(quality.issues)
        if not frame.empty:
            for col in ("canonical_concept", "accession_number", "source_document_id"):
                if frame[col].isna().any() or (frame[col].astype(str).str.strip() == "").any():
                    issues.append(f"missing traceability field: {col}")
            if spec.dataset_id == "cn_financial_pdf":
                for _, row in frame.iterrows():
                    v = row["value"]
                    try:
                        Decimal(str(v))
                    except Exception:
                        issues.append(f"non-decimal value: {v!r}")
        return QualityReport(
            status="failed" if issues else quality.status,
            issues=tuple(issues),
            warnings=quality.warnings,
        )
