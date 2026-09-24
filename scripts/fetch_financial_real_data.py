"""Fetch and normalize a versioned real financial dataset for Runtime V9.3.

The script snapshots public Eastmoney F10 financial statement APIs, stores the
API responses under ``raw/``, and derives parsed Financial Facts under
``facts/``.  Missing API fields are intentionally not filled with zero.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from langgraph_langchain.data.assets import canonical_text_sha256


REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "tests" / "financial" / "real_data"
API_BASE = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
USER_AGENT = "data-analysis-agent-runtime-v9.3/1.0"

COMPANIES = [
    {
        "company_id": "600519",
        "company_name": "贵州茅台",
        "stock_code": "600519",
        "exchange": "SSE",
        "industry_id": "baijiu",
        "currency": "CNY",
    },
    {
        "company_id": "000858",
        "company_name": "五粮液",
        "stock_code": "000858",
        "exchange": "SZSE",
        "industry_id": "baijiu",
        "currency": "CNY",
    },
    {
        "company_id": "000568",
        "company_name": "泸州老窖",
        "stock_code": "000568",
        "exchange": "SZSE",
        "industry_id": "baijiu",
        "currency": "CNY",
    },
]
PERIODS = ["2021", "2022", "2023", "2024", "2025"]

STATEMENTS = {
    "income_statement": "RPT_F10_FINANCE_GINCOME",
    "balance_sheet": "RPT_F10_FINANCE_GBALANCE",
    "cash_flow_statement": "RPT_F10_FINANCE_GCASHFLOW",
}

# Eastmoney fields are in CNY.  Each metric keeps the original API field in
# provenance; absent fields are omitted rather than synthesized.
FIELD_MAPPING = {
    "income_statement": {
        "revenue": "TOTAL_OPERATE_INCOME",
        "cost_of_revenue": "OPERATE_COST",
        "operating_profit": "OPERATE_PROFIT",
        "net_profit": "NETPROFIT",
        "net_profit_attributable": "PARENT_NETPROFIT",
    },
    "balance_sheet": {
        "total_assets": "TOTAL_ASSETS",
        "total_liabilities": "TOTAL_LIABILITIES",
        "total_equity": "TOTAL_EQUITY",
        "cash": "MONETARYFUNDS",
        "accounts_receivable": "ACCOUNTS_RECE",
        "inventory": "INVENTORY",
        "fixed_assets": "FIXED_ASSET",
        "short_term_debt": "SHORT_LOAN",
        "long_term_debt": "LONG_LOAN",
        "current_assets": "TOTAL_CURRENT_ASSETS",
        "current_liabilities": "TOTAL_CURRENT_LIAB",
    },
    "cash_flow_statement": {
        "operating_cash_flow": "NETCASH_OPERATE",
        "investing_cash_flow": "NETCASH_INVEST",
        "financing_cash_flow": "NETCASH_FINANCE",
        "capital_expenditure": "CONSTRUCT_LONG_ASSET",
    },
}

REQUIRED_FIELDS = {
    "revenue",
    "operating_profit",
    "net_profit",
    "net_profit_attributable",
    "total_assets",
    "total_liabilities",
    "total_equity",
    "operating_cash_flow",
    "capital_expenditure",
}


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def statement_url(secucode: str, year: str, report_name: str) -> str:
    query = urllib.parse.urlencode({
        "reportName": report_name,
        "columns": "ALL",
        "filter": (
            f'(SECUCODE="{secucode}")(REPORT_TYPE="年报")'
            f"(REPORT_DATE='{year}-12-31')"
        ),
        "pageNumber": 1,
        "pageSize": 1,
        "sortColumns": "REPORT_DATE",
        "sortTypes": -1,
        "source": "HSF10",
        "client": "PC",
    })
    return f"{API_BASE}?{query}"


def fetch_json(url: str, *, attempts: int = 3) -> dict:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
                },
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status} for {url}")
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # pragma: no cover - network retry path
            last_error = exc
            if attempt < attempts:
                time.sleep(1.0 * attempt)
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def one_row(payload: dict, *, context: str) -> dict:
    if payload.get("code") != 0 or payload.get("message") != "ok":
        raise ValueError(f"API rejected {context}: {payload.get('message')}")
    result = payload.get("result") or {}
    rows = result.get("data") or []
    if len(rows) != 1:
        raise ValueError(f"Expected one row for {context}, got {len(rows)}")
    return rows[0]


def secucode(company: dict) -> str:
    suffix = ".SH" if company["exchange"] == "SSE" else ".SZ"
    return f"{company['stock_code']}{suffix}"


def build_fact(
    *,
    company: dict,
    source_id: str,
    original_period: str,
    metric_id: str,
    value: float,
    api_field: str,
) -> dict:
    return {
        "company_id": company["company_id"],
        "source_id": source_id,
        "original_period": original_period,
        "metric_id": metric_id,
        "value": float(value),
        "unit": "CNY",
        "original_metric_name": api_field,
        "source_label": f"eastmoney_f10.{api_field}",
    }


def fetch_company_period(
    company: dict,
    year: str,
    retrieved_at: str,
) -> tuple[dict, dict, dict]:
    secucode_value = secucode(company)
    source_id = f"{company['company_id']}_{year}_annual_report_api"
    raw_relative = f"raw/{source_id}.json"
    facts_relative = f"facts/{source_id}.json"
    responses: dict[str, object] = {}
    source_urls: dict[str, str] = {}
    rows: dict[str, dict] = {}
    facts: list[dict] = []

    for statement_key, report_name in STATEMENTS.items():
        url = statement_url(secucode_value, year, report_name)
        payload = fetch_json(url)
        row = one_row(payload, context=f"{secucode_value}/{year}/{report_name}")
        expected_date = f"{year}-12-31"
        if not str(row.get("REPORT_DATE", "")).startswith(expected_date):
            raise ValueError(
                f"Unexpected report date for {secucode_value}/{year}: "
                f"{row.get('REPORT_DATE')}"
            )
        if row.get("CURRENCY") not in (None, "CNY"):
            raise ValueError(
                f"Unexpected currency for {secucode_value}/{year}: "
                f"{row.get('CURRENCY')}"
            )
        source_urls[statement_key] = url
        responses[statement_key] = payload
        rows[statement_key] = row

        for metric_id, api_field in FIELD_MAPPING[statement_key].items():
            value = row.get(api_field)
            if value is None:
                continue
            facts.append(build_fact(
                company=company,
                source_id=source_id,
                original_period=f"{year}年报",
                metric_id=metric_id,
                value=value,
                api_field=api_field,
            ))

    covered_metrics = {item["metric_id"] for item in facts}
    missing = sorted(REQUIRED_FIELDS - covered_metrics)
    if missing:
        raise ValueError(
            f"Required fields missing for {secucode_value}/{year}: {missing}"
        )

    raw_payload = {
        "provider": "Eastmoney Data Center",
        "dataset_version": "financial-real-v1",
        "retrieved_at": retrieved_at,
        "secucode": secucode_value,
        "report_period": f"{year}年报",
        "source_urls": source_urls,
        "responses": responses,
    }
    facts_payload = {
        "source_id": source_id,
        "company_id": company["company_id"],
        "report_period": f"{year}年报",
        "facts": facts,
    }
    return raw_payload, facts_payload, {
        "source_id": source_id,
        "source_type": "annual_report_api_snapshot",
        "company_id": company["company_id"],
        "report_period": year,
        "document_name": (
            f"{company['company_name']}{year}年度报告财务数据快照"
        ),
        "document_url": (
            "https://emweb.securities.eastmoney.com/pc_hsf10/pages/"
            f"index.html?type=web&code={secucode_value}#/cwfx"
        ),
        "raw_file": raw_relative,
        "facts_file": facts_relative,
        "facts_file_hash": None,
        "source_hash": None,
        "published_at": str(rows["income_statement"].get("NOTICE_DATE", ""))[:10],
    }


def generate(retrieved_at: str | None) -> None:
    if retrieved_at is None:
        retrieved_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(
            timespec="seconds"
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    sources: list[dict] = []
    for company in COMPANIES:
        for year in PERIODS:
            raw_payload, facts_payload, source = fetch_company_period(
                company,
                year,
                retrieved_at,
            )
            raw_path = OUTPUT_DIR / source["raw_file"]
            facts_path = OUTPUT_DIR / source["facts_file"]
            write_json(raw_path, raw_payload)
            write_json(facts_path, facts_payload)
            source["source_hash"] = (
                "sha256-canonical-text:" + canonical_text_sha256(raw_path)
            )
            source["facts_file_hash"] = (
                "sha256-canonical-text:" + canonical_text_sha256(facts_path)
            )
            sources.append(source)
            print(f"fetched {source['source_id']}: {len(facts_payload['facts'])} facts")

    manifest = {
        "dataset_version": "financial-real-v1",
        "provider": "Eastmoney Data Center",
        "retrieved_at": retrieved_at,
        "source_note": (
            "Public Eastmoney F10 annual report API snapshot. API URLs and "
            "canonical SHA-256 hashes are retained for provenance."
        ),
        "companies": COMPANIES,
        "periods": PERIODS,
        "sources": sources,
    }
    write_json(OUTPUT_DIR / "manifest.json", manifest)
    print(f"wrote {len(sources)} sources to {OUTPUT_DIR}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--retrieved-at",
        help="Override ISO-8601 retrieval timestamp (useful for reproducible updates).",
    )
    args = parser.parse_args()
    generate(args.retrieved_at)


if __name__ == "__main__":
    main()
