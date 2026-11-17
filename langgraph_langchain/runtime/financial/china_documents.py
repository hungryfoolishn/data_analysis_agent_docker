"""China announcement and filing-document parsing layers (B10.8.2).

The announcement metadata layer and the financial-document parser are explicit.
An announcement is never treated as a financial statement; only rows parsed from
a supported document become Financial facts.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import datetime, timezone
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from langgraph_langchain.data.adapters.base import stable_json

__all__ = [
    "ChinaAnnouncement",
    "ChinaAnnouncementList",
    "ChinaDocumentUnsupportedError",
    "ChinaFinancialFact",
    "ChinaStatementPeriod",
    "ChinaStatementParseError",
    "detect_china_document_type",
    "parse_china_announcement_list",
    "parse_china_statement_csv",
]

_UNIT_MULTIPLIERS = {
    "元": Decimal("1"),
    "千元": Decimal("1000"),
    "万元": Decimal("10000"),
    "百万元": Decimal("1000000"),
    "亿元": Decimal("100000000"),
}


class ChinaAnnouncementParseError(ValueError):
    """Raised when announcement metadata cannot be traced."""


class ChinaStatementParseError(ValueError):
    """Raised when a filing document cannot be parsed safely."""


class ChinaDocumentUnsupportedError(ChinaStatementParseError):
    """Raised when a document format has no implemented lossless parser."""


@dataclass(frozen=True)
class ChinaAnnouncement:
    stock_code: str
    stock_name: str
    announcement_id: str
    title: str
    announcement_time: str
    announcement_type: str
    document_url: str
    source_url: str
    source_response_hash: str

    @property
    def document_hash(self) -> str:
        """Announcement-level hash; document bytes have their own hash."""
        return hashlib.sha256(
            f"{self.announcement_id}|{self.document_url}".encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class ChinaAnnouncementList:
    announcements: tuple[ChinaAnnouncement, ...]
    total_announcement: int | None
    has_more: bool | None
    source_url: str
    source_response_hash: str


@dataclass(frozen=True)
class ChinaStatementPeriod:
    period_start: str
    period_end: str
    fiscal_year: int
    fiscal_period: str
    reporting_basis: str


@dataclass(frozen=True)
class ChinaFinancialFact:
    statement_type: str
    reporting_basis: str
    period_start: str
    period_end: str
    fiscal_year: int
    fiscal_period: str
    currency: str
    unit: str
    original_concept: str
    canonical_concept: str
    original_value: str | None
    value: Decimal | None
    row_number: int
    announcement_id: str
    source_url: str
    document_hash: str
    parser_version: str
    mapping_version: str
    published_at: str

    @property
    def source_document_id(self) -> str:
        return f"cn:{self.announcement_id}:{self.document_hash[:12]}:{self.row_number}"


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ChinaAnnouncementParseError(f"announcement {field_name} is required")
    return value.strip()


def _announcement_time(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        milliseconds = float(value)
        if milliseconds >= 1_000_000_000_000:
            return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        if milliseconds >= 1_000_000_000:
            return datetime.fromtimestamp(milliseconds, timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
    raise ChinaAnnouncementParseError("announcement announcement time is required")


def _document_url(raw_url: object) -> str:
    value = _required_string(raw_url, "document URL")
    if re.fullmatch(r"https?://.+", value):
        return value
    return "https://static.cninfo.com.cn/" + value.lstrip("/")


def _announcement_from_payload(
    item: Mapping[str, Any],
    *,
    source_url: str,
    source_response_hash: str,
) -> ChinaAnnouncement:
    announcement_id = _required_string(
        item.get("announcementId") or item.get("announcement_id"), "announcement id"
    )
    return ChinaAnnouncement(
        stock_code=_required_string(item.get("secCode") or item.get("stock_code"), "stock code"),
        stock_name=str(item.get("secName") or item.get("stock_name") or ""),
        announcement_id=announcement_id,
        title=_required_string(item.get("announcementTitle") or item.get("title"), "title"),
        announcement_time=_announcement_time(
            item.get("announcementTime") or item.get("announcement_time")
        ),
        announcement_type=str(item.get("announcementType") or item.get("announcement_type") or ""),
        document_url=_document_url(item.get("adjunctUrl") or item.get("document_url")),
        source_url=source_url,
        source_response_hash=source_response_hash,
    )


def parse_china_announcement_list(payload: Any) -> ChinaAnnouncementList:
    """Parse CNINFO-style announcement metadata without inventing statements."""
    if not isinstance(payload, Mapping):
        raise ChinaAnnouncementParseError("announcement payload must be a JSON object")
    announcements_payload = payload.get("announcements")
    if not isinstance(announcements_payload, list):
        raise ChinaAnnouncementParseError("announcement payload must contain announcements array")
    source_response_hash = hashlib.sha256(
        stable_json(dict(payload)).encode("utf-8")
    ).hexdigest()
    source_url = str(payload.get("source_url") or "")
    announcements = []
    for item in announcements_payload:
        if not isinstance(item, Mapping):
            raise ChinaAnnouncementParseError("every announcement must be a JSON object")
        announcements.append(
            _announcement_from_payload(
                item,
                source_url=source_url,
                source_response_hash=source_response_hash,
            )
        )
    return ChinaAnnouncementList(
        announcements=tuple(announcements),
        total_announcement=payload.get("totalAnnouncement"),
        has_more=payload.get("hasMore"),
        source_url=source_url,
        source_response_hash=source_response_hash,
    )


def detect_china_document_type(document: bytes) -> str:
    if document.startswith(b"%PDF-"):
        return "pdf"
    if document.startswith(b"PK\x03\x04"):
        return "zip"
    try:
        document.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ChinaDocumentUnsupportedError("document is not valid UTF-8 text") from exc
    return "csv"


def _parse_china_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"—", "-", "不适用"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    text = text.replace(",", "")
    try:
        result = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ChinaStatementParseError(f"invalid numeric value: {value!r}") from exc
    if not result.is_finite():
        raise ChinaStatementParseError(f"non-finite numeric value: {value!r}")
    return -result if negative else result


def parse_china_statement_csv(
    document: bytes,
    *,
    announcement: ChinaAnnouncement,
    period: ChinaStatementPeriod,
    statement_type: str,
    canonical_concepts: Mapping[str, str],
    value_column: str = "本期金额",
    concept_column: str = "报表项目",
    unit: str = "元",
    parser_version: str = "b10.8.2",
    mapping_version: str = "b10.8.2",
) -> tuple[ChinaFinancialFact, ...]:
    """Parse an explicitly delimited filing-document export.

    The production PDF reader is not implemented.  This parser is for official
    text/CSV exports used in deterministic tests; PDF is detected and rejected
    as unsupported rather than silently text-scraped.
    """
    document_type = detect_china_document_type(document)
    if document_type != "csv":
        raise ChinaDocumentUnsupportedError(
            f"unsupported China filing document type: {document_type}"
        )
    if period.reporting_basis not in {"consolidated", "parent"}:
        raise ChinaStatementParseError(
            f"unsupported reporting basis: {period.reporting_basis!r}"
        )
    if statement_type not in {"income", "balance", "cash"}:
        raise ChinaStatementParseError(
            f"unsupported statement type: {statement_type!r}"
        )
    multiplier = _UNIT_MULTIPLIERS.get(unit)
    if multiplier is None:
        raise ChinaStatementParseError(f"unsupported CNY quantity unit: {unit!r}")

    text = document.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    rows = [row for row in rows if any(str(cell).strip() for cell in row)]
    if not rows:
        raise ChinaStatementParseError("filing document has no table rows")
    header = [cell.strip() for cell in rows[0]]
    if concept_column not in header or value_column not in header:
        raise ChinaStatementParseError(
            "filing document must contain explicit concept and value columns"
        )
    concept_index = header.index(concept_column)
    value_index = header.index(value_column)
    document_hash = hashlib.sha256(document).hexdigest()

    facts = []
    for row_number, row in enumerate(rows[1:], start=2):
        if len(row) <= max(concept_index, value_index):
            raise ChinaStatementParseError(
                f"row {row_number} does not match the declared table header"
            )
        original_concept = row[concept_index].strip()
        if not original_concept:
            continue
        canonical_concept = canonical_concepts.get(original_concept)
        if canonical_concept is None:
            raise ChinaStatementParseError(
                f"row {row_number} concept {original_concept!r} has no canonical mapping"
            )
        original_value = row[value_index].strip() or None
        parsed_value = _parse_china_decimal(original_value)
        if parsed_value is not None:
            parsed_value *= multiplier
        facts.append(
            ChinaFinancialFact(
                statement_type=statement_type,
                reporting_basis=period.reporting_basis,
                period_start=period.period_start,
                period_end=period.period_end,
                fiscal_year=period.fiscal_year,
                fiscal_period=period.fiscal_period,
                currency="CNY",
                unit=unit,
                original_concept=original_concept,
                canonical_concept=canonical_concept,
                original_value=original_value,
                value=parsed_value,
                row_number=row_number,
                announcement_id=announcement.announcement_id,
                source_url=announcement.document_url,
                document_hash=document_hash,
                parser_version=parser_version,
                mapping_version=mapping_version,
                published_at=announcement.announcement_time,
            )
        )
    return tuple(facts)
def china_fact_to_row(fact: ChinaFinancialFact, *, entity_id: str, market: str = "cn") -> dict[str, object]:
    """Map a parsed filing-document fact into the B10.5 canonical acquisition row shape."""
    return {
        "market": market,
        "entity_id": entity_id,
        "canonical_concept": fact.canonical_concept,
        "statement_type": fact.statement_type,
        "period_type": "duration",
        "period_start": fact.period_start,
        "period_end": fact.period_end,
        "fiscal_year": fact.fiscal_year,
        "fiscal_period": fact.fiscal_period,
        "reporting_basis": fact.reporting_basis,
        "currency": fact.currency,
        "value": fact.value,
        "statistic_kind": "absolute_amount",
        "unit": fact.currency,
        "accession_number": fact.announcement_id,
        "filing_date": fact.published_at,
        "source_document_id": fact.source_document_id,
    }

__all__ = [*__all__, "china_fact_to_row", "ChinaAnnouncementParseError"]
