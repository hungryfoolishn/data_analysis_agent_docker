"""Real financial data loading and provenance validation for Runtime V9.3."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph_langchain.data.assets import canonical_text_sha256, hash_file

from .fact import FinancialFact
from .data_service import FinancialDataService
from .models import (
    BalanceSheetStatement,
    CashFlowStatement,
    Company,
    FinancialDataSource,
    IncomeStatement,
)
from .period import PeriodNormalizer
from .unit import UnitNormalizer


_INCOME_FIELDS = {
    "revenue",
    "cost_of_revenue",
    "gross_profit",
    "operating_profit",
    "net_profit",
    "net_profit_attributable",
}
_BALANCE_FIELDS = {
    "total_assets",
    "total_liabilities",
    "total_equity",
    "cash",
    "accounts_receivable",
    "inventory",
    "fixed_assets",
    "short_term_debt",
    "long_term_debt",
    "current_assets",
    "current_liabilities",
}
_CASH_FIELDS = {
    "operating_cash_flow",
    "investing_cash_flow",
    "financing_cash_flow",
    "capital_expenditure",
    "free_cash_flow",
}
_KNOWN_FIELDS = _INCOME_FIELDS | _BALANCE_FIELDS | _CASH_FIELDS


@dataclass(frozen=True)
class RealCompanyRecord:
    company_id: str
    company_name: str
    stock_code: str
    exchange: str = "CN"
    industry_id: str = "unknown"
    currency: str = "CNY"


@dataclass(frozen=True)
class RealSourceRecord:
    source_id: str
    source_type: str
    company_id: str
    report_period: str
    document_name: str
    raw_file: str
    source_hash: str
    facts_file: str | None = None
    facts_file_hash: str | None = None
    document_url: str | None = None
    published_at: str | None = None


@dataclass(frozen=True)
class RealFinancialManifest:
    dataset_version: str
    companies: list[RealCompanyRecord]
    periods: list[str]
    sources: list[RealSourceRecord]


@dataclass(frozen=True)
class RealFinancialDataset:
    manifest: RealFinancialManifest
    facts: list[FinancialFact]
    data_service: Any
    validation_errors: list[str] = field(default_factory=list)


class RealFinancialDataLoader:
    """Load and validate manifest, raw sources, facts, and statements.

    The loader intentionally does not synthesize missing facts.  Missing fields
    remain ``None`` so downstream metrics become ``UNAVAILABLE``.
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.period_normalizer = PeriodNormalizer()
        self.unit_normalizer = UnitNormalizer()

    def load(self, *, strict: bool = True) -> RealFinancialDataset:
        errors: list[str] = []
        manifest = self._load_manifest()
        companies = self._build_companies(manifest, errors)
        sources = self._build_sources(manifest, errors)
        facts = self._load_facts(manifest, sources, errors)
        self._validate_coverage(manifest, facts, errors)

        data_service = self._build_service(companies, facts, sources, errors)
        if strict and errors:
            raise ValueError("Real financial data validation failed:\n- " + "\n- ".join(errors))
        return RealFinancialDataset(
            manifest=manifest,
            facts=facts,
            data_service=data_service,
            validation_errors=errors,
        )

    def _load_manifest(self) -> RealFinancialManifest:
        manifest_path = self.directory / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Real financial manifest not found: {manifest_path}")
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        companies = [RealCompanyRecord(**item) for item in raw.get("companies", [])]
        sources = [RealSourceRecord(**item) for item in raw.get("sources", [])]
        return RealFinancialManifest(
            dataset_version=str(raw.get("dataset_version", "")),
            companies=companies,
            periods=[str(item) for item in raw.get("periods", [])],
            sources=sources,
        )

    def _build_companies(
        self,
        manifest: RealFinancialManifest,
        errors: list[str],
    ) -> list[Company]:
        companies: list[Company] = []
        seen_ids: set[str] = set()
        for record in manifest.companies:
            if record.company_id in seen_ids:
                errors.append(f"duplicate company_id: {record.company_id}")
                continue
            seen_ids.add(record.company_id)
            companies.append(Company(
                company_id=record.company_id,
                stock_code=record.stock_code,
                company_name=record.company_name,
                exchange=record.exchange,
                industry_id=record.industry_id,
                currency=record.currency,
            ))
        return companies

    def _build_sources(
        self,
        manifest: RealFinancialManifest,
        errors: list[str],
    ) -> dict[str, FinancialDataSource]:
        sources: dict[str, FinancialDataSource] = {}
        seen_ids: set[str] = set()
        for record in manifest.sources:
            if record.source_id in seen_ids:
                errors.append(f"duplicate source_id: {record.source_id}")
                continue
            seen_ids.add(record.source_id)
            raw_path = self.directory / record.raw_file
            if not raw_path.is_file():
                errors.append(f"missing raw source file: {record.raw_file}")
                continue
            if record.source_hash.startswith("sha256-canonical-text:"):
                actual_hash = canonical_text_sha256(raw_path)
                expected_hash = record.source_hash.removeprefix(
                    "sha256-canonical-text:"
                )
                actual_hash_label = f"sha256-canonical-text:{actual_hash}"
            elif record.source_hash.startswith("sha256:"):
                actual_hash = hash_file(raw_path)
                expected_hash = record.source_hash.removeprefix("sha256:")
                actual_hash_label = f"sha256:{actual_hash}"
            else:
                errors.append(
                    f"unsupported source hash for {record.source_id}: "
                    f"{record.source_hash}"
                )
                continue
            if actual_hash != expected_hash:
                errors.append(
                    f"source hash mismatch for {record.source_id}: "
                    f"expected {record.source_hash}, got {actual_hash_label}"
                )
                continue
            if record.facts_file:
                facts_path = self.directory / record.facts_file
                if not facts_path.is_file():
                    errors.append(f"missing parsed facts file: {record.facts_file}")
                    continue
                if record.facts_file_hash:
                    actual_facts_hash = canonical_text_sha256(facts_path)
                    expected_facts_hash = record.facts_file_hash.removeprefix(
                        "sha256-canonical-text:"
                    )
                    if actual_facts_hash != expected_facts_hash:
                        errors.append(
                            f"facts file hash mismatch for {record.source_id}: "
                            f"expected {record.facts_file_hash}, "
                            f"got sha256-canonical-text:{actual_facts_hash}"
                        )
                        continue
            try:
                self.period_normalizer.parse(record.report_period)
            except ValueError as exc:
                errors.append(f"invalid source period for {record.source_id}: {exc}")
                continue
            sources[record.source_id] = FinancialDataSource(
                source_id=record.source_id,
                source_type=record.source_type,
                company_id=record.company_id,
                report_period=self.period_normalizer.parse(record.report_period).normalized_period,
                document_name=record.document_name,
                document_url=record.document_url,
                source_hash=record.source_hash,
            )
        return sources

    def _load_facts(
        self,
        manifest: RealFinancialManifest,
        sources: dict[str, FinancialDataSource],
        errors: list[str],
    ) -> list[FinancialFact]:
        facts: list[FinancialFact] = []
        seen_fact_ids: set[str] = set()
        seen_keys: set[tuple[str, str, str]] = set()

        for source_record in manifest.sources:
            source = sources.get(source_record.source_id)
            if source is None:
                continue
            facts_path = (
                self.directory / source_record.facts_file
                if source_record.facts_file
                else self.directory / source_record.raw_file
            )
            try:
                payload = json.loads(facts_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(
                    f"cannot parse facts file for {source_record.source_id}: {exc}"
                )
                continue

            records = payload.get("facts", []) if isinstance(payload, dict) else payload
            if not isinstance(records, list):
                errors.append(f"raw source must contain a fact list: {source_record.source_id}")
                continue

            source_period = self.period_normalizer.parse(source_record.report_period)
            normalized_period = source_period.normalized_period
            for index, record in enumerate(records):
                metric_id = str(record.get("metric_id", "")).strip()
                if metric_id not in _KNOWN_FIELDS:
                    errors.append(
                        f"unknown metric in {source_record.source_id} #{index}: {metric_id}"
                    )
                    continue

                raw_value = record.get("value")
                original_unit = str(record.get("unit", "CNY"))
                try:
                    conversion = self.unit_normalizer.normalize(raw_value, original_unit)
                    value = conversion.normalized_value
                    status = "VALID" if conversion.status == "calculated" else conversion.status.upper()
                    if conversion.status != "calculated":
                        errors.append(
                            f"invalid fact in {source_record.source_id} #{index} "
                            f"{metric_id}: {conversion.reason}"
                        )
                except Exception as exc:
                    conversion = UnitConversion(
                        original_value=None if raw_value is None else float(raw_value),
                        original_unit=original_unit,
                        normalized_value=None,
                        normalized_unit="CNY",
                        conversion_rule="none",
                        status="invalid",
                        reason=str(exc),
                    )
                    value = None
                    status = "INVALID"
                    errors.append(
                        f"cannot normalize fact in {source_record.source_id} #{index} "
                        f"{metric_id}: {exc}"
                    )

                company_id = str(record.get("company_id", "")).strip()
                if company_id != source.company_id:
                    errors.append(
                        f"company mismatch in {source_record.source_id} #{index}: "
                        f"expected {source.company_id}, got {company_id}"
                    )
                    continue
                if record.get("source_id") != source_record.source_id:
                    errors.append(
                        f"source mismatch in {source_record.source_id} #{index}"
                    )
                    continue

                fact_id = f"fact_{company_id}_{normalized_period}_{metric_id}"
                if fact_id in seen_fact_ids:
                    errors.append(f"duplicate fact: {fact_id}")
                    continue
                key = (company_id, normalized_period, metric_id)
                if key in seen_keys:
                    errors.append(f"duplicate company/period/metric: {key}")
                    continue

                seen_fact_ids.add(fact_id)
                seen_keys.add(key)
                facts.append(FinancialFact(
                    fact_id=fact_id,
                    company_id=company_id,
                    period=normalized_period,
                    metric_id=metric_id,
                    value=value,
                    unit="CNY",
                    source_id=source.source_id,
                    source_label=str(record.get("source_label", "")) or None,
                    original_period=str(record.get("original_period", source_record.report_period)),
                    original_metric_name=str(record.get("original_metric_name", metric_id)),
                    original_value=None if raw_value is None else float(raw_value),
                    original_unit=original_unit,
                    conversion_rule=conversion.conversion_rule,
                    normalized_unit="CNY",
                    status=status,
                ))
        return facts

    def _validate_coverage(
        self,
        manifest: RealFinancialManifest,
        facts: list[FinancialFact],
        errors: list[str],
    ) -> None:
        if len(manifest.companies) < 3:
            errors.append(
                f"real financial dataset requires >=3 companies, got {len(manifest.companies)}"
            )
        if len(manifest.periods) < 5:
            errors.append(
                f"real financial dataset requires >=5 periods, got {len(manifest.periods)}"
            )
        normalized_manifest_periods = [
            self.period_normalizer.parse(item).normalized_period
            for item in manifest.periods
        ]
        covered = {
            (item.company_id, item.period)
            for item in facts
            if item.metric_id == "revenue"
        }
        for company in manifest.companies:
            for period in normalized_manifest_periods:
                if (company.company_id, period) not in covered:
                    errors.append(
                        f"missing company/period source: {company.company_id}/{period}"
                    )

    def _build_service(
        self,
        companies: list[Company],
        facts: list[FinancialFact],
        loaded_sources: dict[str, FinancialDataSource],
        errors: list[str],
    ):
        facts_by_key: dict[tuple[str, str, str], FinancialFact] = {
            (item.company_id, item.period, item.metric_id): item
            for item in facts
        }
        income: list[IncomeStatement] = []
        balance: list[BalanceSheetStatement] = []
        cash: list[CashFlowStatement] = []
        sources = loaded_sources

        for company in companies:
            company_periods = sorted(
                {
                    item.period for item in facts
                    if item.company_id == company.company_id
                },
                key=self.period_normalizer.order_key,
            )
            for period in company_periods:
                def fact_value(metric_id: str) -> float | None:
                    item = facts_by_key.get((company.company_id, period, metric_id))
                    return item.value if item and item.status == "VALID" else None

                source_id = next((
                    item.source_id for item in facts
                    if item.company_id == company.company_id and item.period == period
                ), None)
                if source_id is None:
                    errors.append(f"no source for {company.company_id}/{period}")
                    continue
                source = sources.get(source_id)
                if source is None:
                    errors.append(f"unknown source {source_id}")
                    continue
                sources[source_id] = source
                income.append(IncomeStatement(
                    company_id=company.company_id,
                    period=period,
                    source_id=source_id,
                    **{field: fact_value(field) for field in sorted(_INCOME_FIELDS)},
                ))
                balance.append(BalanceSheetStatement(
                    company_id=company.company_id,
                    period=period,
                    source_id=source_id,
                    **{field: fact_value(field) for field in sorted(_BALANCE_FIELDS)},
                ))
                cash.append(CashFlowStatement(
                    company_id=company.company_id,
                    period=period,
                    source_id=source_id,
                    **{field: fact_value(field) for field in sorted(_CASH_FIELDS)},
                ))
        return FinancialDataService(
            companies=companies,
            income_statements=income,
            balance_sheets=balance,
            cash_flows=cash,
        )
