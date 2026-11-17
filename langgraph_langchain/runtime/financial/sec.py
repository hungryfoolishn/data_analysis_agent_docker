"""SEC company-facts acquisition adapter (B10.5).

This adapter follows the B8 ``HttpJsonAdapter`` contract and keeps canonical
filing metadata in the normalized DataFrame.  Offline tests inject a transport;
real EDGAR validation is a separate external-service gate.
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

__all__ = ["SecCompanyFactsAdapter"]

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


class SecCompanyFactsAdapter(HttpJsonAdapter):
    descriptor = AdapterDescriptor(
        adapter_id="financial_sec_company_facts",
        version="1.0.0",
        source_type="financial_sec_company_facts",
        description="SEC EDGAR company XBRL facts with canonical filing metadata.",
        requires_credentials=False,
    )
    datasets = {
        "sec_company_facts": DatasetSpec(
            dataset_id="sec_company_facts",
            name="SEC company facts",
            description="Canonical XBRL facts and filing metadata.",
            unit="source_declared",
            frequency="filing_event",
            source_url="https://data.sec.gov/api/xbrl/companyconcept/",
            required_columns=_FINANCIAL_COLUMNS,
            time_column="period_end",
            update_policy="filing_event",
        )
    }

    def _fetch_raw(self, request: FetchRequest, spec: DatasetSpec) -> Any:
        cik = request.parameters.get("cik")
        concept = request.parameters.get("canonical_concept")
        if not cik or not concept:
            raise AdapterRequestError("cik and canonical_concept are required")
        digits = re.sub(r"\D", "", str(cik))
        if not digits:
            raise AdapterRequestError("cik must contain digits")
        taxonomy = request.parameters.get("taxonomy", "us-gaap")
        url = (
            f"https://data.sec.gov/api/xbrl/companyconcept/"
            f"CIK{digits.zfill(10)}/{taxonomy}/{concept}.json"
        )
        return self._transport(url, {})

    def normalize(
        self,
        raw: Any,
        request: FetchRequest,
        spec: DatasetSpec,
    ) -> pd.DataFrame:
        rows = raw if isinstance(raw, list) else raw.get("facts", [])
        normalized = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            normalized.append({
                "market": row.get("market", request.parameters.get("market", "us")),
                "entity_id": row.get("entity_id", request.parameters.get("entity_id", "")),
                "canonical_concept": row.get("canonical_concept", request.parameters.get("canonical_concept")),
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
        if set(frame["market"].dropna()) - {"us"}:
            issues.append("SEC adapter supports market='us' only")
        return QualityReport(
            status="failed" if issues else quality.status,
            issues=tuple(issues),
            warnings=quality.warnings,
        )
