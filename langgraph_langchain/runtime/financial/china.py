"""China company-filing acquisition adapter (B10.5).

The adapter uses the B8 HTTP JSON contract and preserves announcement metadata.
The default endpoint is treated as an official-source integration point; offline
tests inject a transport.  Real external-service validation remains separate.
"""

from __future__ import annotations

import json
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

__all__ = ["ChinaCompanyFilingsAdapter"]

_FINANCIAL_COLUMNS = (
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


class ChinaCompanyFilingsAdapter(HttpJsonAdapter):
    descriptor = AdapterDescriptor(
        adapter_id="financial_cn_company_filings",
        version="1.0.0",
        source_type="financial_cn_company_filings",
        description="China official filing adapter with announcement traceability.",
        requires_credentials=False,
        rate_limit_seconds=1.0,
    )
    datasets = {
        "cn_company_filings": DatasetSpec(
            dataset_id="cn_company_filings",
            name="China company financial filings",
            description="Canonical facts from official China disclosure documents.",
            unit="source_declared",
            frequency="filing_event",
            source_url="https://www.cninfo.com.cn/new/hisAnnouncement/query",
            required_columns=_FINANCIAL_COLUMNS,
            time_column="period_end",
            update_policy="filing_event",
        )
    }

    def _fetch_raw(self, request: FetchRequest, spec: DatasetSpec) -> Any:
        stock_code = request.parameters.get("stock_code")
        if not stock_code:
            raise AdapterRequestError("stock_code is required")
        params = {
            "stock": stock_code,
            "report_type": request.parameters.get("report_type", "financial"),
        }
        return self._transport(self.datasets["cn_company_filings"].source_url, params)

    def normalize(
        self,
        raw: Any,
        request: FetchRequest,
        spec: DatasetSpec,
    ) -> pd.DataFrame:
        rows = raw if isinstance(raw, list) else raw.get("filings", [])
        normalized = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            normalized.append({
                "market": row.get("market", request.parameters.get("market", "cn")),
                "entity_id": row.get("entity_id", request.parameters.get("entity_id", "")),
                "canonical_concept": row.get("canonical_concept"),
                "statement_type": row.get("statement_type"),
                "period_type": row.get("period_type"),
                "period_start": row.get("period_start"),
                "period_end": row.get("period_end"),
                "fiscal_year": row.get("fiscal_year"),
                "fiscal_period": row.get("fiscal_period"),
                "reporting_basis": row.get("reporting_basis"),
                "currency": row.get("currency"),
                "value": row.get("value"),
                "statistic_kind": row.get("statistic_kind"),
                "unit": row.get("unit"),
                "accession_number": row.get("accession_number"),
                "filing_date": row.get("filing_date"),
                "source_document_id": row.get("source_document_id"),
            })
        frame = pd.DataFrame(normalized, columns=_FINANCIAL_COLUMNS)
        if not frame.empty:
            frame["value"] = frame["value"].map(
                lambda item: str(item) if isinstance(item, Decimal) else str(Decimal(str(item)))
            )
        return frame.drop_duplicates(
            subset=[
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
                "accession_number",
                "source_document_id",
            ],
            keep="last",
        ).sort_values(
            ["period_end", "period_start", "canonical_concept", "accession_number"],
            kind="stable",
        ).reset_index(drop=True)

    def validate(self, frame: pd.DataFrame, spec: DatasetSpec) -> QualityReport:
        quality = super().validate(frame, spec)
        issues = list(quality.issues)
        if frame.empty:
            return quality
        for column in ("accession_number", "filing_date", "source_document_id"):
            if frame[column].isna().any() or (frame[column].astype(str).str.strip() == "").any():
                issues.append(f"missing filing traceability field: {column}")
        if set(frame["market"].dropna()) - {"cn"}:
            issues.append("China adapter supports market='cn' only")
        return QualityReport(
            status="failed" if issues else quality.status,
            issues=tuple(issues),
            warnings=quality.warnings,
        )
