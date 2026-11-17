"""China filing-document PDF download, parsing and accounting-subject mapping.

B10.9 adds the production PDF path that was explicitly BLOCKED at B10.8:
announcement -> PDF download -> text/table extraction -> financial statement
identification -> line-item parsing -> canonical concept mapping -> canonical
financial facts.

Scanned/OCR PDFs are out of scope (P2) and are detected and rejected rather
than silently mis-parsed.
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence

__all__ = [
    "ChinaPdfDownloadError",
    "ChinaPdfDocument",
    "ChinaPdfParseError",
    "ChinaPdfUnsupportedError",
    "ChinaPdfStatementBlock",
    "ChinaPdfParseResult",
    "download_china_announcement_pdf",
    "parse_china_statement_pdf",
    "map_china_pdf_rows",
    "MAPPING_VERSION",
]

_PDF_MAGIC = b"%PDF-"
_DEFAULT_MAX_PDF_BYTES = 20_000_000
_DEFAULT_TIMEOUT_SECONDS = 30

MAPPING_VERSION = "b10.9.3.cn_v1"

# ---------------------------------------------------------------------------
# Accounting subject mapping (B10.9.4 minimum coverage)
# ---------------------------------------------------------------------------

_CN_SUBJECT_MAP: dict[str, tuple[str, str]] = {
    # Balance sheet
    "\u8d27\u5e01\u8d44\u91d1": ("cash_and_equivalents", "balance"),
    "\u4ea4\u6613\u6027\u91d1\u878d\u8d44\u4ea7": ("trading_financial_assets", "balance"),
    "\u5e94\u6536\u7968\u636e": ("notes_receivable", "balance"),
    "\u5e94\u6536\u8d26\u6b3e": ("accounts_receivable", "balance"),
    "\u9884\u4ed8\u6b3e\u9879": ("prepayments", "balance"),
    "\u5176\u4ed6\u5e94\u6536\u6b3e": ("other_receivables", "balance"),
    "\u5b58\u8d27": ("inventory", "balance"),
    "\u56fa\u5b9a\u8d44\u4ea7": ("fixed_assets", "balance"),
    "\u5728\u5efa\u5de5\u7a0b": ("construction_in_progress", "balance"),
    "\u65e0\u5f62\u8d44\u4ea7": ("intangible_assets", "balance"),
    "\u5546\u8a89": ("goodwill", "balance"),
    "\u9012\u5ef6\u6240\u5f97\u7a0e\u8d44\u4ea7": ("deferred_tax_assets", "balance"),
    "\u77ed\u671f\u501f\u6b3e": ("short_term_borrowings", "balance"),
    "\u5e94\u4ed8\u7968\u636e": ("notes_payable", "balance"),
    "\u5e94\u4ed8\u8d26\u6b3e": ("accounts_payable", "balance"),
    "\u5408\u540c\u8d1f\u503a": ("contract_liabilities", "balance"),
    "\u5e94\u4ed8\u804c\u5de5\u85aa\u916c": ("employee_benefits_payable", "balance"),
    "\u5e94\u4ea4\u7a0e\u8d39": ("taxes_payable", "balance"),
    "\u957f\u671f\u501f\u6b3e": ("long_term_borrowings", "balance"),
    "\u5e94\u4ed8\u503a\u5238": ("bonds_payable", "balance"),
    "\u9012\u5ef6\u6240\u5f97\u7a0e\u8d1f\u503a": ("deferred_tax_liabilities", "balance"),
    "\u80a1\u672c": ("share_capital", "balance"),
    "\u8d44\u672c\u516c\u79ef": ("capital_reserve", "balance"),
    "\u76c8\u4f59\u516c\u79ef": ("surplus_reserve", "balance"),
    "\u672a\u5206\u914d\u5229\u6da6": ("retained_earnings", "balance"),
    "\u5f52\u5c5e\u4e8e\u6bcd\u516c\u53f8\u6240\u6709\u8005\u6743\u76ca\u5408\u8ba1": ("equity_attributable_to_parent", "balance"),
    "\u6240\u6709\u8005\u6743\u76ca\u5408\u8ba1": ("total_equity", "balance"),
    "\u8d1f\u503a\u5408\u8ba1": ("total_liabilities", "balance"),
    "\u8d44\u4ea7\u603b\u8ba1": ("total_assets", "balance"),
    "\u8d1f\u503a\u548c\u6240\u6709\u8005\u6743\u76ca\u603b\u8ba1": ("total_liabilities_and_equity", "balance"),
    # Income statement
    "\u8425\u4e1a\u6536\u5165": ("revenue", "income"),
    "\u8425\u4e1a\u6210\u672c": ("cost_of_revenue", "income"),
    "\u7a0e\u91d1\u53ca\u9644\u52a0": ("taxes_and_surcharges", "income"),
    "\u9500\u552e\u8d39\u7528": ("selling_expenses", "income"),
    "\u7ba1\u7406\u8d39\u7528": ("administrative_expenses", "income"),
    "\u7814\u53d1\u8d39\u7528": ("research_and_development_expenses", "income"),
    "\u8d22\u52a1\u8d39\u7528": ("finance_expenses", "income"),
    "\u8425\u4e1a\u5229\u6da6": ("operating_profit", "income"),
    "\u8425\u4e1a\u5916\u6536\u5165": ("non_operating_income", "income"),
    "\u8425\u4e1a\u5916\u652f\u51fa": ("non_operating_expenses", "income"),
    "\u5229\u6da6\u603b\u989d": ("total_profit", "income"),
    "\u6240\u5f97\u7a0e\u8d39\u7528": ("income_tax_expense", "income"),
    "\u51c0\u5229\u6da6": ("net_profit", "income"),
    "\u5f52\u5c5e\u4e8e\u6bcd\u516c\u53f8\u80a1\u4e1c\u7684\u51c0\u5229\u6da6": ("net_profit_attributable_to_parent", "income"),
    "\u57fa\u672c\u6bcf\u80a1\u6536\u76ca": ("eps_basic", "income"),
    "\u7a00\u91ca\u6bcf\u80a1\u6536\u76ca": ("eps_diluted", "income"),
    # Cash flow statement
    "\u9500\u552e\u5546\u54c1\u3001\u63d0\u4f9b\u52b3\u52a1\u6536\u5230\u7684\u73b0\u91d1": ("cash_received_from_sales", "cash"),
    "\u6536\u5230\u7684\u7a0e\u8d39\u8fd4\u8fd8": ("tax_refunds_received", "cash"),
    "\u7ecf\u8425\u6d3b\u52a8\u73b0\u91d1\u6d41\u5165\u5c0f\u8ba1": ("operating_cash_inflow_subtotal", "cash"),
    "\u7ecf\u8425\u6d3b\u52a8\u73b0\u91d1\u6d41\u51fa\u5c0f\u8ba1": ("operating_cash_outflow_subtotal", "cash"),
    "\u7ecf\u8425\u6d3b\u52a8\u4ea7\u751f\u7684\u73b0\u91d1\u6d41\u91cf\u51c0\u989d": ("net_cash_from_operating_activities", "cash"),
    "\u6295\u8d44\u6d3b\u52a8\u4ea7\u751f\u7684\u73b0\u91d1\u6d41\u91cf\u51c0\u989d": ("net_cash_from_investing_activities", "cash"),
    "\u7b79\u8d44\u6d3b\u52a8\u4ea7\u751f\u7684\u73b0\u91d1\u6d41\u91cf\u51c0\u989d": ("net_cash_from_financing_activities", "cash"),
    "\u6c47\u7387\u53d8\u52a8\u5bf9\u73b0\u91d1\u53ca\u73b0\u91d1\u7b49\u4ef7\u7269\u7684\u5f71\u54cd": ("effect_of_exchange_rate_changes", "cash"),
    "\u73b0\u91d1\u53ca\u73b0\u91d1\u7b49\u4ef7\u7269\u51c0\u589e\u52a0\u989d": ("net_increase_in_cash_and_equivalents", "cash"),
    "\u671f\u672b\u73b0\u91d1\u53ca\u73b0\u91d1\u7b49\u4ef7\u7269\u4f59\u989d": ("ending_cash_and_equivalents", "cash"),
}

# Statement titles that are table headers, not line items.
_STATEMENT_TITLES = {"\u8d44\u4ea7\u8d1f\u503a\u8868", "\u5229\u6da6\u8868", "\u73b0\u91d1\u6d41\u91cf\u8868"}

_BALANCE_KEYWORDS = ("\u8d44\u4ea7\u8d1f\u503a\u8868",)
_INCOME_KEYWORDS = ("\u5229\u6da6\u8868", "\u635f\u76ca\u8868")
_CASH_KEYWORDS = ("\u73b0\u91d1\u6d41\u91cf\u8868",)
_SCOPED_KEYWORDS = ("\u6bcd\u516c\u53f8\u8d44\u4ea7\u8d1f\u503a\u8868", "\u6bcd\u516c\u53f8\u5229\u6da6\u8868", "\u6bcd\u516c\u53f8\u73b0\u91d1\u6d41\u91cf\u8868")

_UNIT_MULTIPLIERS = {
    "\u5143": Decimal("1"),
    "\u5343\u5143": Decimal("1000"),
    "\u4e07\u5143": Decimal("10000"),
    "\u767e\u4e07\u5143": Decimal("1000000"),
    "\u4ebf\u5143": Decimal("100000000"),
}

_NEGATIVE_PAT = re.compile(r"^\(.*\)$|^[\uff08].*[\uff09]$")
_AMOUNT_PAT = re.compile(r"^\d+(,\d{3})*(\.\d+)?$")

# Additional subjects commonly found in Chinese bank annual reports
_EXTRA_SUBJECTS: dict[str, tuple[str, str]] = {
    "\u5229\u606f\u6536\u5165": ("interest_income", "income"),
    "\u5229\u606f\u652f\u51fa": ("interest_expense", "income"),
    "\u5229\u606f\u51c0\u6536\u5165": ("net_interest_income", "income"),
    "\u624b\u7eed\u8d39\u53ca\u4f63\u91d1\u6536\u5165": ("fee_and_commission_income", "income"),
    "\u624b\u7eed\u8d39\u53ca\u4f63\u91d1\u652f\u51fa": ("fee_and_commission_expense", "income"),
    "\u624b\u7eed\u8d39\u53ca\u4f63\u91d1\u51c0\u6536\u5165": ("net_fee_and_commission_income", "income"),
    "\u51c0\u4ea4\u6613\u635f\u76ca": ("net_trading_income", "income"),
    "\u91d1\u878d\u6295\u8d44\u51c0\u635f\u76ca": ("net_financial_investment_income", "income"),
    "\u5176\u4ed6\u8425\u4e1a\u51c0\u6536\u5165": ("other_operating_income", "income"),
    "\u8425\u4e1a\u8d39\u7528": ("operating_expenses", "income"),
    "\u8d44\u4ea7\u51cf\u503c\u635f\u5931": ("asset_impairment_loss", "income"),
    "\u7a0e\u524d\u5229\u6da6": ("total_profit", "income"),
    "\u53d1\u653e\u8d37\u6b3e\u548c\u57ab\u6b3e": ("loans_and_advances", "balance"),
    "\u5b58\u653e\u4e2d\u592e\u94f6\u884c\u6b3e\u9879": ("deposits_with_central_bank", "balance"),
    "\u73b0\u91d1\u53ca\u5b58\u653e\u4e2d\u592e\u94f6\u884c\u6b3e\u9879": ("cash_and_deposits_with_central_bank", "balance"),
    "\u5b58\u653e\u548c\u62c6\u653e\u540c\u4e1a\u53ca\u5176\u4ed6\u91d1\u878d\u673a\u6784\u6b3e\u9879": ("deposits_with_banks", "balance"),
    "\u8d35\u91d1\u5c5e": ("precious_metals", "balance"),
    "\u884d\u751f\u91d1\u878d\u8d44\u4ea7": ("derivative_financial_assets", "balance"),
    "\u4e70\u5165\u8fd4\u552e\u91d1\u878d\u8d44\u4ea7": ("financial_assets_held_for_resale", "balance"),
    "\u91d1\u878d\u6295\u8d44": ("financial_investments", "balance"),
    "\u4f7f\u7528\u6743\u8d44\u4ea7": ("right_of_use_assets", "balance"),
    "\u5176\u4ed6\u8d44\u4ea7": ("other_assets", "balance"),
    "\u5438\u6536\u5b58\u6b3e": ("customer_deposits", "balance"),
    "\u7ecf\u8425\u6d3b\u52a8\u4ea7\u751f\u7684\u73b0\u91d1\u6d41\u91cf\u51c0\u989d": ("net_cash_from_operating_activities", "cash"),
    "\u6295\u8d44\u6d3b\u52a8\u4ea7\u751f\u7684\u73b0\u91d1\u6d41\u91cf\u51c0\u989d": ("net_cash_from_investing_activities", "cash"),
    "\u7b79\u8d44\u6d3b\u52a8\u4ea7\u751f\u7684\u73b0\u91d1\u6d41\u91cf\u51c0\u989d": ("net_cash_from_financing_activities", "cash"),
}

_ALL_SUBJECTS = {**_CN_SUBJECT_MAP, **_EXTRA_SUBJECTS}

_NOTE_REF = re.compile(r"^\s*(?:[\u4e00-\u4e5d]+(?:\u3001\d+)?|\d+(?:\.\d+)?(?:\.\d+)?)\s+")
_FULLWIDTH_MAP = str.maketrans({
    "\uff0c": ",",
    "\uff0d": "-",
    "\uff08": "(",
    "\uff09": ")",
    "\u3000": " ",
})



class ChinaPdfDownloadError(Exception):
    """Raised when a PDF download fails integrity or safety checks."""


class ChinaPdfParseError(ValueError):
    """Raised when a filing-document PDF cannot be parsed safely."""


class ChinaPdfUnsupportedError(ChinaPdfParseError):
    """Raised when the PDF type (scanned, encrypted, etc.) is unsupported."""


@dataclass(frozen=True)
class ChinaPdfDocument:
    content: bytes
    content_sha256: str
    file_size: int
    url: str
    media_type: str

    @property
    def is_pdf(self) -> bool:
        return self.content[:5] == _PDF_MAGIC


@dataclass
class ChinaPdfStatementBlock:
    statement_type: str
    reporting_basis: str
    page_numbers: tuple[int, ...]
    raw_rows: list[list[str]] = field(default_factory=list)


@dataclass
class ChinaPdfParseResult:
    document_sha256: str
    total_pages: int
    parser_version: str
    mapping_version: str
    statements: tuple[ChinaPdfStatementBlock, ...]
    unmapped_subjects: tuple[str, ...]
    page_hashes: tuple[str, ...]


def download_china_announcement_pdf(
    url: str,
    *,
    transport: Any | None = None,
    max_bytes: int = _DEFAULT_MAX_PDF_BYTES,
    timeout: int = _DEFAULT_TIMEOUT_SECONDS,
) -> ChinaPdfDocument:
    if not url or not url.startswith("https://"):
        raise ChinaPdfDownloadError(f"refusing non-https URL: {url!r}")
    if transport is not None:
        try:
            content = transport(url, timeout)
        except ChinaPdfDownloadError:
            raise
        except Exception as exc:
            raise ChinaPdfDownloadError(f"PDF download failed: {exc}") from exc
        if not isinstance(content, bytes):
            raise ChinaPdfDownloadError("transport must return bytes")
    else:
        try:
            import requests

            resp = requests.get(url, timeout=timeout, stream=True)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "pdf" not in content_type.lower() and "octet-stream" not in content_type.lower():
                raise ChinaPdfDownloadError(f"unexpected content-type {content_type!r}")
            chunks: list[bytes] = []
            received = 0
            for chunk in resp.iter_content(chunk_size=65536):
                received += len(chunk)
                if received > max_bytes:
                    raise ChinaPdfDownloadError(f"PDF exceeds size limit ({max_bytes})")
                chunks.append(chunk)
            content = b"".join(chunks)
        except ChinaPdfDownloadError:
            raise
        except Exception as exc:
            raise ChinaPdfDownloadError(f"PDF download failed: {exc}") from exc
    if len(content) > max_bytes:
        raise ChinaPdfDownloadError(f"PDF exceeds size limit ({max_bytes})")
    if len(content) < 5:
        raise ChinaPdfDownloadError("content too small to be a PDF")
    if content[:5] != _PDF_MAGIC:
        raise ChinaPdfDownloadError("content does not have PDF magic bytes")
    sha = hashlib.sha256(content).hexdigest()
    return ChinaPdfDocument(content=content, content_sha256=sha, file_size=len(content), url=url, media_type="application/pdf")


def _parse_amount(raw: str) -> Decimal | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text or text in ("--", "\u2014", "-", "\u2013", "\u3000"):
        return None
    text = text.replace("\uff0c", ",").replace("\uff0d", "-").replace("\uff09", ")").replace("\uff08", "(")
    text = text.replace(",", "")
    is_negative = False
    if _NEGATIVE_PAT.match(text):
        is_negative = True
        text = text[1:-1].strip()
    elif text.startswith("-"):
        is_negative = True
        text = text[1:].strip()
    if not text or not re.match(r"^\d+(\.\d+)?$", text):
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if is_negative:
        value = -value
    return value


def _classify_table_title(cells: list[str]) -> str | None:
    joined = "".join(cells)
    if any(k in joined for k in _CASH_KEYWORDS):
        return "cash"
    if any(k in joined for k in _INCOME_KEYWORDS):
        return "income"
    if any(k in joined for k in _BALANCE_KEYWORDS):
        return "balance"
    return None


def _classify_scope(cells: list[str]) -> str:
    joined = "".join(cells)
    return "parent" if any(k in joined for k in _SCOPED_KEYWORDS) else "consolidated"


def _is_header_row(cells: Sequence[str]) -> bool:
    joined = "|".join(c.strip() for c in cells)
    if any(k in joined for k in ("\u9879\u76ee", "\u79d1\u76ee", "\u62a5\u8868\u9879\u76ee")):
        return True
    first = cells[0].strip() if cells else ""
    return first in _STATEMENT_TITLES


def _classify_page_text(text: str) -> str | None:
    if any(k in text for k in _CASH_KEYWORDS):
        return "cash"
    if any(k in text for k in _INCOME_KEYWORDS):
        return "income"
    if any(k in text for k in _BALANCE_KEYWORDS):
        return "balance"
    return None


def _classify_page_scope(text: str) -> str:
    return "parent" if any(k in text for k in _SCOPED_KEYWORDS) else "consolidated"


def _extract_rows_from_page(pdf_page: Any) -> list[list[list[str]]]:
    """Extract tables from a pdfplumber page; returns list of tables, each a list of rows."""
    tables = pdf_page.extract_tables()
    result: list[list[list[str]]] = []
    for table in tables:
        rows = [[str(c).strip() if c else "" for c in row] for row in table]
        result.append(rows)
    return result


def parse_china_statement_pdf(
    pdf_content: bytes,
    *,
    content_sha256: str | None = None,
    parser_version: str = "b10.9.2",
) -> ChinaPdfParseResult:
    if content_sha256 is None:
        content_sha256 = hashlib.sha256(pdf_content).hexdigest()
    try:
        import pdfplumber
    except ImportError as exc:
        raise ChinaPdfUnsupportedError("pdfplumber is not installed") from exc

    # Primary: table-based parsing (precise when bordered tables are detectable)
    page_hashes: list[str] = []
    table_statements: list[ChinaPdfStatementBlock] | None = None
    try:
        with pdfplumber.open(io.BytesIO(pdf_content)) as pdf:
            total_pages = len(pdf.pages)
            if total_pages == 0:
                raise ChinaPdfParseError("PDF has no pages")
            page_texts: list[str] = []
            page_tables: list[list[list[list[str]]]] = []
            for page in pdf.pages:
                text = page.extract_text() or ""
                page_hashes.append(hashlib.sha256(text.encode("utf-8")).hexdigest())
                page_texts.append(text)
                page_tables.append(_extract_rows_from_page(page))
    except ChinaPdfParseError:
        raise
    except Exception as exc:
        msg = str(exc)
        if "password" in msg.lower() or "encrypt" in msg.lower():
            raise ChinaPdfUnsupportedError("PDF is encrypted") from exc
        raise ChinaPdfParseError(f"PDF parsing failed: {exc}") from exc

    non_empty = sum(1 for t in page_texts if t.strip())
    if non_empty == 0:
        raise ChinaPdfUnsupportedError("PDF has no extractable text (likely scanned)")

    # Classify each table independently.
    statements: list[ChinaPdfStatementBlock] = []
    for page_num, tables in enumerate(page_tables, start=1):
        page_text = page_texts[page_num - 1]
        for table_rows in tables:
            if not table_rows:
                continue
            title_type = _classify_table_title(table_rows[0])
            content_rows = [r for r in table_rows if not _is_header_row(r) and r[0].strip()]
            if not content_rows:
                continue
            merged = False
            if title_type is None and statements:
                first_subject = _clean_subject(content_rows[0][0])
                mapping = _CN_SUBJECT_MAP.get(first_subject)
                if mapping:
                    ct = mapping[1]
                    for existing in reversed(statements):
                        if existing.statement_type == ct:
                            existing.page_numbers = tuple(set(existing.page_numbers) | {page_num})
                            existing.raw_rows.extend(content_rows)
                            merged = True
                            break
            if title_type is None and not merged:
                title_type = _classify_page_text(page_text)
            if title_type is None:
                continue
            scope = _classify_scope(table_rows[0]) if table_rows[0] else _classify_page_scope(page_text)
            if not merged:
                statements.append(ChinaPdfStatementBlock(
                    statement_type=title_type, reporting_basis=scope,
                    page_numbers=(page_num,), raw_rows=list(content_rows),
                ))

    if statements:
        # Validate that at least one block has mapped subjects before accepting
        # table-based results (management-discussion pages may produce empty tables).
        has_mapped = False
        for blk in statements:
            for row in blk.raw_rows:
                subj = _clean_subject(row[0])
                if _CN_SUBJECT_MAP.get(subj) or _EXTRA_SUBJECTS.get(subj):
                    has_mapped = True
                    break
            if has_mapped:
                break
        if has_mapped:
            return ChinaPdfParseResult(
            document_sha256=content_sha256,
            total_pages=total_pages,
            parser_version=parser_version,
            mapping_version=MAPPING_VERSION,
            statements=tuple(statements),
            unmapped_subjects=(),
            page_hashes=tuple(page_hashes),
        )

    # Fallback: text-based line parsing (for real annual reports without bordered tables)
    text_results = _parse_pdf_text_lines(pdf_content)
    if any(text_results.values()):
        text_statements: list[ChinaPdfStatementBlock] = []
        for stmt_type, items in text_results.items():
            if not items:
                continue
            rows = []
            for item in items:
                rows.append([item["original_concept"], str(item["value"]), ""])
            seen: set[str] = set()
            deduped = []
            for r in rows:
                if r[0] not in seen:
                    seen.add(r[0])
                    deduped.append(r)
            pages = sorted({item["page"] for item in items})
            text_statements.append(ChinaPdfStatementBlock(
                statement_type=stmt_type, reporting_basis="consolidated",
                page_numbers=tuple(pages), raw_rows=deduped,
            ))
        if text_statements:
            return ChinaPdfParseResult(
                document_sha256=content_sha256,
                total_pages=total_pages,
                parser_version=parser_version,
                mapping_version=MAPPING_VERSION,
                statements=tuple(text_statements),
                unmapped_subjects=(),
                page_hashes=tuple(page_hashes),
            )

    raise ChinaPdfParseError("no financial statement blocks were detected")

    
def _find_subject_in_line(line: str) -> tuple[str, str] | None:
    """Find the longest matching Chinese accounting subject at the start of a line.

    Returns ``(subject, remainder)`` or ``None``.
    """
    text = line.strip().translate(_FULLWIDTH_MAP)
    if not text or text[0].isdigit():
        return None
    best: tuple[str, str] | None = None
    for subject in _ALL_SUBJECTS:
        if text.startswith(subject):
            if best is None or len(subject) > len(best[0]):
                best = (subject, text[len(subject):])
    return best


def _extract_line_amounts(remainder: str) -> list[str]:
    """Extract numeric amounts from the remainder of a line, skipping note refs."""
    text = remainder.strip()
    m = _NOTE_REF.match(text)
    if m:
        text = text[m.end():].strip()
    text = re.sub(r"[（](\u4eba\u6c11\u5e01)[\u5143/\u80a1]*[）]", "", text).strip()
    tokens = text.split()
    amounts = []
    for t in tokens:
        if _parse_amount(t) is not None:
            amounts.append(t)
    return amounts


def _classify_page_text_type(text: str) -> str | None:
    """Classify a page by checking the first few lines (statement titles)."""
    lines = text.split("\n")[:5]
    for line in lines:
        if "\u5229\u6da6\u8868" in line and "\u6bcd\u516c\u53f8" not in line:
            return "income"
        if "\u8d22\u52a1\u72b6\u51b5\u8868" in line or "\u8d44\u4ea7\u8d1f\u503a\u8868" in line:
            return "balance"
        if "\u73b0\u91d1\u6d41\u91cf\u8868" in line:
            return "cash"
    return None


def _parse_pdf_text_lines(pdf_content: bytes) -> dict[str, list[dict[str, Any]]]:
    """Parse a PDF using text-based line matching for each page."""
    results: dict[str, list[dict[str, Any]]] = {"income": [], "balance": [], "cash": []}
    try:
        import pdfplumber
    except ImportError as exc:
        raise ChinaPdfUnsupportedError("pdfplumber is not installed") from exc
    try:
        with pdfplumber.open(io.BytesIO(pdf_content)) as pdf:
            for pnum, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                stmt_type = _classify_page_text_type(text)
                if stmt_type is None:
                    continue
                for line in text.split("\n"):
                    found = _find_subject_in_line(line)
                    if found is None:
                        continue
                    subject, remainder = found
                    mapping = _ALL_SUBJECTS.get(subject)
                    if mapping is None or mapping[1] != stmt_type:
                        continue
                    amounts = _extract_line_amounts(remainder)
                    if not amounts:
                        continue
                    val = _parse_amount(amounts[0])
                    if val is None:
                        continue
                    results[stmt_type].append({
                        "page": pnum + 1,
                        "original_concept": subject,
                        "canonical_concept": mapping[0],
                        "value": val,
                    })
    except ChinaPdfParseError:
        raise
    except Exception as exc:
        msg = str(exc)
        if "password" in msg.lower() or "encrypt" in msg.lower():
            raise ChinaPdfUnsupportedError("PDF is encrypted") from exc
        raise ChinaPdfParseError(f"PDF parsing failed: {exc}") from exc
    return results



def _clean_subject(raw: str) -> str:
    return raw.replace("\u3000", "").strip()


def _detect_value_column(rows: list[list[str]], statement_type: str) -> int:
    keywords = ("\u671f\u672b\u4f59\u989d", "\u671f\u672b\u6570") if statement_type == "balance" else ("\u672c\u671f\u91d1\u989d", "\u672c\u671f\u53d1\u751f\u989d", "\u672c\u671f\u6570")
    for row in rows[:5]:
        for idx, cell in enumerate(row):
            if cell and any(k in cell for k in keywords):
                return idx
    return 1


def map_china_pdf_rows(
    block: ChinaPdfStatementBlock,
    *,
    period_start: str,
    period_end: str,
    fiscal_year: int,
    fiscal_period: str,
    announcement_id: str,
    announcement_url: str,
    published_at: str,
    document_sha256: str,
    parser_version: str = "b10.9.2",
    mapping_version: str = MAPPING_VERSION,
    unit: str = "\u5143",
) -> tuple[list[dict[str, Any]], list[str]]:
    if block.statement_type not in ("income", "balance", "cash"):
        raise ChinaPdfParseError(f"unsupported statement type: {block.statement_type!r}")
    value_col = _detect_value_column(block.raw_rows, block.statement_type)
    multiplier = _UNIT_MULTIPLIERS.get(unit)
    if multiplier is None:
        raise ChinaPdfParseError(f"unsupported CNY quantity unit: {unit!r}")
    facts: list[dict[str, Any]] = []
    unmapped: list[str] = []
    period_type = "instant" if block.statement_type == "balance" else "duration"
    for row_num, row in enumerate(block.raw_rows, start=1):
        if not row or _is_header_row(row):
            continue
        subject = _clean_subject(row[0])
        if not subject or subject in _STATEMENT_TITLES:
            continue
        mapping = _CN_SUBJECT_MAP.get(subject)
        if mapping is None:
            unmapped.append(subject)
            continue
        canonical_concept, expected_type = mapping
        if expected_type != block.statement_type:
            continue
        raw_value = row[value_col] if value_col < len(row) else ""
        parsed = _parse_amount(raw_value)
        if parsed is None:
            continue
        parsed *= multiplier
        facts.append({
            "statement_type": block.statement_type,
            "reporting_basis": block.reporting_basis,
            "period_type": period_type,
            "period_start": None if period_type == "instant" else period_start,
            "period_end": period_end,
            "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period,
            "currency": "CNY",
            "unit": unit,
            "original_concept": subject,
            "canonical_concept": canonical_concept,
            "original_value": raw_value,
            "value": parsed,
            "row_number": row_num,
            "announcement_id": announcement_id,
            "source_url": announcement_url,
            "document_hash": document_sha256,
            "parser_version": parser_version,
            "mapping_version": mapping_version,
            "published_at": published_at,
        })
    return facts, unmapped
