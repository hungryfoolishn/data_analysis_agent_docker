"""Generate the deterministic Runtime V9 financial golden fixture and cases.

The figures are intentionally synthetic and version-controlled for offline
regression.  They are not published company financials.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from langgraph_langchain.data.assets import canonical_text_sha256
from langgraph_langchain.runtime.financial.data_service import FinancialDataService
from langgraph_langchain.runtime.financial.metrics import (
    calculate_metric,
    financial_metric_registry,
)
from langgraph_langchain.runtime.financial.models import (
    BalanceSheetStatement,
    CashFlowStatement,
    Company,
    IncomeStatement,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "tests" / "financial" / "data" / "financial"
CASES_DIR = REPO_ROOT / "tests" / "financial" / "golden_cases"
YEARS = [2021, 2022, 2023, 2024, 2025]

COMPANY_SPECS = [
    ("白酒样本01", "600001", "baijiu", "SSE"),
    ("白酒样本02", "600002", "baijiu", "SSE"),
    ("白酒样本03", "600003", "baijiu", "SSE"),
    ("白酒样本04", "600004", "baijiu", "SSE"),
    ("家电样本01", "600011", "home_appliance", "SSE"),
    ("家电样本02", "600012", "home_appliance", "SSE"),
    ("家电样本03", "600013", "home_appliance", "SSE"),
    ("家电样本04", "600014", "home_appliance", "SSE"),
    ("银行样本01", "600021", "banking", "SSE"),
    ("银行样本02", "600022", "banking", "SSE"),
    ("银行样本03", "600023", "banking", "SSE"),
    ("银行样本04", "600024", "banking", "SSE"),
    ("新能源样本01", "600031", "new_energy", "SSE"),
    ("新能源样本02", "600032", "new_energy", "SSE"),
    ("新能源样本03", "600033", "new_energy", "SSE"),
    ("新能源样本04", "600034", "new_energy", "SSE"),
    ("医药样本01", "600041", "pharmaceutical", "SSE"),
    ("医药样本02", "600042", "pharmaceutical", "SSE"),
    ("医药样本03", "600043", "pharmaceutical", "SSE"),
    ("医药样本04", "600044", "pharmaceutical", "SSE"),
]

GROSS_MARGINS = {
    "baijiu": 0.84,
    "home_appliance": 0.28,
    "banking": 0.42,
    "new_energy": 0.24,
    "pharmaceutical": 0.55,
}

DEBT_RATIOS = {
    "baijiu": 0.24,
    "home_appliance": 0.58,
    "banking": 0.72,
    "new_energy": 0.52,
    "pharmaceutical": 0.31,
}

GROWTH_PATHS = [
    [0.08, -0.02, 0.12, 0.05, 0.18],
    [0.12, 0.04, -0.05, 0.09, 0.07],
    [0.05, 0.10, 0.03, -0.04, 0.11],
    [0.18, 0.06, 0.09, 0.02, -0.03],
]


def build_companies() -> list[Company]:
    return [
        Company(
            company_id=f"financial_{index:02d}",
            stock_code=stock_code,
            company_name=name,
            exchange=exchange,
            industry_id=industry,
            listing_date=f"19{90 + index % 10}-01-01",
        )
        for index, (name, stock_code, industry, exchange) in enumerate(COMPANY_SPECS)
    ]


def build_statements(companies: list[Company]) -> tuple[
    list[IncomeStatement],
    list[BalanceSheetStatement],
    list[CashFlowStatement],
]:
    income: list[IncomeStatement] = []
    balance: list[BalanceSheetStatement] = []
    cash: list[CashFlowStatement] = []

    for company_index, company in enumerate(companies):
        industry = company.industry_id
        revenue = 2_000_000_000.0 * (1 + company_index * 0.07)
        for year_offset, period in enumerate(YEARS):
            growth = GROWTH_PATHS[company_index % len(GROWTH_PATHS)][year_offset]
            revenue *= 1 + growth
            gross_margin = GROSS_MARGINS[industry] + (company_index % 3) * 0.005
            gross_profit = revenue * gross_margin
            operating_profit = gross_profit * 0.68
            net_profit = operating_profit * 0.82
            debt_ratio = min(0.88, DEBT_RATIOS[industry] + (company_index % 4) * 0.015)

            total_assets = revenue * 2.2
            total_liabilities = total_assets * debt_ratio
            total_equity = total_assets - total_liabilities
            current_assets = total_assets * 0.48
            current_liabilities = total_liabilities * 0.46
            accounts_receivable = revenue * (0.05 + (company_index % 3) * 0.008)
            inventory = revenue * (0.04 + (company_index % 4) * 0.012)

            ocf_ratio = 0.42 if company_index % 7 == 0 and year_offset >= 3 else 1.15
            operating_cash_flow = net_profit * ocf_ratio
            capital_expenditure = net_profit * 0.38

            income.append(IncomeStatement(
                company_id=company.company_id,
                period=str(period),
                revenue=round(revenue, 2),
                cost_of_revenue=round(revenue - gross_profit, 2),
                gross_profit=round(gross_profit, 2),
                operating_profit=round(operating_profit, 2),
                net_profit=round(net_profit, 2),
                net_profit_attributable=round(net_profit * 0.97, 2),
                eps=round(net_profit / 1_000_000_000, 6),
                source_id=f"synthetic_income_{company.company_id}_{period}",
            ))
            balance.append(BalanceSheetStatement(
                company_id=company.company_id,
                period=str(period),
                total_assets=round(total_assets, 2),
                total_liabilities=round(total_liabilities, 2),
                total_equity=round(total_equity, 2),
                cash=round(current_assets * 0.42, 2),
                accounts_receivable=round(accounts_receivable, 2),
                inventory=round(inventory, 2),
                fixed_assets=round(total_assets * 0.27, 2),
                short_term_debt=round(total_liabilities * 0.38, 2),
                long_term_debt=round(total_liabilities * 0.62, 2),
                current_assets=round(current_assets, 2),
                current_liabilities=round(current_liabilities, 2),
                source_id=f"synthetic_balance_{company.company_id}_{period}",
            ))
            cash.append(CashFlowStatement(
                company_id=company.company_id,
                period=str(period),
                operating_cash_flow=round(operating_cash_flow, 2),
                investing_cash_flow=round(-capital_expenditure, 2),
                financing_cash_flow=round(-net_profit * 0.18, 2),
                capital_expenditure=round(capital_expenditure, 2),
                free_cash_flow=round(operating_cash_flow - capital_expenditure, 2),
                source_id=f"synthetic_cash_{company.company_id}_{period}",
            ))
    return income, balance, cash


def write_csv(path: Path, rows: list[object]) -> None:
    if not rows:
        raise ValueError(f"No rows for {path}")
    fieldnames = list(rows[0].model_dump().keys())
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.model_dump())


def metric_value(
    service: FinancialDataService,
    company: Company,
    period: str,
    metric_id: str,
) -> float:
    previous_period = service.previous_period(company.company_id, period)
    income, balance, cash_flow, previous_income = service.statements(
        company.company_id,
        period,
        previous_period,
    )
    previous_balance = service.previous_balance(company.company_id, period)
    value, _ = calculate_metric(
        metric_id,
        income=income,
        balance=balance,
        cash_flow=cash_flow,
        previous_income=previous_income,
        previous_balance=previous_balance,
    )
    return round(float(value), 6)


def expectation(
    service: FinancialDataService,
    company: Company,
    period: str,
    metric_id: str,
) -> dict:
    definition = financial_metric_registry.get(metric_id)
    return {
        "metric": metric_id,
        "value": metric_value(service, company, period, metric_id),
        "tolerance": 0.0005,
        "period": period,
        "dimension": "company",
        "group": company.company_name,
        "filters": {"company": company.company_name},
        "unit": definition.unit,
    }


LOWER_IS_BETTER = {
    "cost_of_revenue",
    "total_liabilities",
    "short_term_debt",
    "long_term_debt",
    "current_liabilities",
    "debt_to_asset",
    "capital_expenditure",
}


def comparison_expectations(
    service: FinancialDataService,
    companies: list[Company],
    period: str,
    metric_id: str,
) -> list[dict]:
    values = {
        company.company_name: metric_value(service, company, period, metric_id)
        for company in companies
    }
    higher_is_better = metric_id not in LOWER_IS_BETTER
    ordered = sorted(
        values.items(),
        key=lambda item: item[1],
        reverse=higher_is_better,
    )
    leader_name, leader_value = ordered[0]
    laggard_name, laggard_value = ordered[-1]
    difference = leader_value - laggard_value
    relative_difference = (
        difference / abs(laggard_value) if laggard_value else 0.0
    )
    definition = financial_metric_registry.get(metric_id)
    group = f"{leader_name}|{laggard_name}"
    return [
        {
            "metric": f"peer_difference:{metric_id}",
            "value": round(difference, 6),
            "tolerance": 0.0005,
            "period": period,
            "dimension": "peer",
            "group": group,
            "filters": {
                "leader": leader_name,
                "laggard": laggard_name,
            },
            "unit": definition.unit,
        },
        {
            "metric": f"peer_relative_difference:{metric_id}",
            "value": round(relative_difference, 6),
            "tolerance": 0.0005,
            "period": period,
            "dimension": "peer",
            "group": group,
            "filters": {
                "leader": leader_name,
                "laggard": laggard_name,
            },
            "unit": "ratio",
        },
    ]


def report_tokens(case_companies: list[Company], metric_ids: list[str], period: str) -> list[str]:
    tokens = ["核心指标", "计算过程", "核心发现", "证据", "不构成投资建议"]
    tokens.extend(company.company_name for company in case_companies)
    tokens.append(period)
    tokens.extend(financial_metric_registry.get(metric_id).name for metric_id in metric_ids)
    return list(dict.fromkeys(tokens))


def peer_report_tokens(case_companies: list[Company], metric_ids: list[str], period: str) -> list[str]:
    return list(dict.fromkeys([
        *report_tokens(case_companies, metric_ids, period),
        "同业比较",
        "差异",
        "相对差异",
    ]))


def make_case(
    *,
    case_id: str,
    category: str,
    task_type: str,
    question: str,
    companies: list[Company],
    period: str,
    metric_ids: list[str],
    service: FinancialDataService,
    manifest_hash: str,
    tags: list[str],
    critical: bool = False,
) -> dict:
    return {
        "case_id": case_id,
        "name": f"{category}: {companies[0].company_name} {period}",
        "question": question,
        "dataset": "../data/financial/financial_dataset.json",
        "dataset_sha256": manifest_hash,
        "task_type": task_type,
        "executor_type": "structured",
        "skill_name": "financial-analysis-workflow",
        "expected_metrics": [
            expectation(service, company, period, metric_id)
            for company in companies
            for metric_id in metric_ids
        ] + (
            [
                item
                for metric_id in metric_ids
                for item in comparison_expectations(service, companies, period, metric_id)
            ] if category == "peer" else []
        ),
        "required_evidence": True,
        "minimum_verified_evidence": 1,
        "required_sql": False,
        "required_finding": True,
        "required_report": True,
        "report_must_contain": (
            peer_report_tokens(companies, metric_ids, period)
            if category == "peer"
            else report_tokens(companies, metric_ids, period)
        ),
        "report_must_not_contain": [],
        "tags": ["financial", "runtime-v9", category, *tags],
        "critical": critical,
    }


def build_cases(
    service: FinancialDataService,
    companies: list[Company],
    manifest_hash: str,
) -> list[dict]:
    cases: list[dict] = []

    for index in range(5):
        company = companies[index]
        cases.append(make_case(
            case_id=f"financial_basic_{index + 1:03d}",
            category="basic",
            task_type="COMPANY_OVERVIEW",
            question=f"{company.company_name} 2025 年公司概况如何？",
            companies=[company],
            period="2025",
            metric_ids=["revenue", "net_profit"],
            service=service,
            manifest_hash=manifest_hash,
            tags=["overview"],
            critical=index == 0,
        ))

    for index in range(8):
        company = companies[index]
        cases.append(make_case(
            case_id=f"financial_trend_{index + 1:03d}",
            category="trend",
            task_type="FINANCIAL_TREND",
            question=f"2021 年至 2025 年 {company.company_name} 的经营趋势变化如何？",
            companies=[company],
            period="2025",
            metric_ids=["revenue_growth", "net_profit_growth"],
            service=service,
            manifest_hash=manifest_hash,
            tags=["trend"],
        ))

    for index in range(5):
        company = companies[index]
        cases.append(make_case(
            case_id=f"financial_revenue_{index + 1:03d}",
            category="revenue",
            task_type="REVENUE_ANALYSIS",
            question=f"{company.company_name} 2025 年营业收入分析",
            companies=[company],
            period="2025",
            metric_ids=["revenue", "revenue_growth"],
            service=service,
            manifest_hash=manifest_hash,
            tags=["income"],
        ))

    for index in range(5):
        company = companies[index]
        cases.append(make_case(
            case_id=f"financial_profit_{index + 1:03d}",
            category="profit",
            task_type="PROFIT_ANALYSIS",
            question=f"{company.company_name} 2025 年净利润分析",
            companies=[company],
            period="2025",
            metric_ids=["net_profit", "net_profit_growth"],
            service=service,
            manifest_hash=manifest_hash,
            tags=["income"],
        ))

    for index in range(5):
        company = companies[index]
        cases.append(make_case(
            case_id=f"financial_profitability_{index + 1:03d}",
            category="profitability",
            task_type="PROFITABILITY_ANALYSIS",
            question=f"{company.company_name} 2025 年盈利能力分析",
            companies=[company],
            period="2025",
            metric_ids=["gross_margin", "operating_margin", "net_margin", "roe"],
            service=service,
            manifest_hash=manifest_hash,
            tags=["profitability"],
        ))

    for index in range(5):
        company = companies[index]
        cases.append(make_case(
            case_id=f"financial_cashflow_{index + 1:03d}",
            category="cashflow",
            task_type="CASHFLOW_ANALYSIS",
            question=f"{company.company_name} 2025 年现金流分析",
            companies=[company],
            period="2025",
            metric_ids=["operating_cash_flow", "free_cash_flow", "ocf_to_net_income"],
            service=service,
            manifest_hash=manifest_hash,
            tags=["cashflow"],
        ))

    for index in range(4):
        company = companies[index]
        cases.append(make_case(
            case_id=f"financial_solvency_{index + 1:03d}",
            category="solvency",
            task_type="SOLVENCY_ANALYSIS",
            question=f"{company.company_name} 2025 年偿债能力分析",
            companies=[company],
            period="2025",
            metric_ids=["debt_to_asset", "current_ratio", "quick_ratio"],
            service=service,
            manifest_hash=manifest_hash,
            tags=["balance_sheet"],
        ))

    for index in range(3):
        company = companies[index]
        cases.append(make_case(
            case_id=f"financial_operating_{index + 1:03d}",
            category="operating",
            task_type="OPERATING_ANALYSIS",
            question=f"{company.company_name} 2025 年营运效率分析",
            companies=[company],
            period="2025",
            metric_ids=["receivable_turnover", "inventory_turnover", "asset_turnover"],
            service=service,
            manifest_hash=manifest_hash,
            tags=["operating"],
        ))

    peer_pairs = [(0, 1), (2, 3), (4, 5), (6, 7), (8, 9), (10, 11), (12, 13)]
    for index, (left, right) in enumerate(peer_pairs):
        left_company, right_company = companies[left], companies[right]
        cases.append(make_case(
            case_id=f"financial_peer_{index + 1:03d}",
            category="peer",
            task_type="PEER_COMPARISON",
            question=f"比较{left_company.company_name}和{right_company.company_name} 2025 年的收入增长和净利率",
            companies=[left_company, right_company],
            period="2025",
            metric_ids=["revenue", "revenue_growth", "net_margin"],
            service=service,
            manifest_hash=manifest_hash,
            tags=["peer"],
            critical=index == 0,
        ))

    for index in range(2):
        company = companies[index]
        cases.append(make_case(
            case_id=f"financial_anomaly_{index + 1:03d}",
            category="anomaly",
            task_type="ANOMALY_ANALYSIS",
            question=f"2021 年至 2025 年 {company.company_name} 有哪些异常波动？",
            companies=[company],
            period="2022",
            metric_ids=["revenue_growth", "net_profit_growth"],
            service=service,
            manifest_hash=manifest_hash,
            tags=["anomaly"],
        ))

    company = companies[0]
    cases.append(make_case(
        case_id="financial_comprehensive_001",
        category="comprehensive",
        task_type="COMPREHENSIVE_ANALYSIS",
        question=f"{company.company_name} 2025 年综合基本面分析",
        companies=[company],
        period="2025",
        metric_ids=[
            "revenue", "net_profit", "gross_margin", "roe",
            "debt_to_asset", "operating_cash_flow",
        ],
        service=service,
        manifest_hash=manifest_hash,
        tags=["comprehensive"],
        critical=True,
    ))

    for index in range(5):
        company = companies[index]
        cases.append(make_case(
            case_id=f"financial_risk_{index + 1:03d}",
            category="risk",
            task_type="RISK_ANALYSIS",
            question=f"{company.company_name} 2025 年风险分析",
            companies=[company],
            period="2025",
            metric_ids=["revenue_growth", "net_profit_growth", "debt_to_asset", "ocf_to_net_income"],
            service=service,
            manifest_hash=manifest_hash,
            tags=["risk"],
        ))

    return cases


def main() -> None:
    companies = build_companies()
    income, balance, cash = build_statements(companies)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(DATA_DIR / "companies.csv", companies)
    write_csv(DATA_DIR / "income_statement.csv", income)
    write_csv(DATA_DIR / "balance_sheet.csv", balance)
    write_csv(DATA_DIR / "cash_flow.csv", cash)

    manifest = {
        "dataset": "runtime_v9_financial_golden",
        "version": "1.0.0",
        "kind": "synthetic_regression_fixture",
        "source_note": "确定生成的离线回归数据，不是真实上市公司财务数据。",
        "files": [
            "companies.csv",
            "income_statement.csv",
            "balance_sheet.csv",
            "cash_flow.csv",
        ],
        "company_count": len(companies),
        "periods": [str(year) for year in YEARS],
        "industry_count": len({item.industry_id for item in companies}),
    }
    manifest_path = DATA_DIR / "financial_dataset.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    service = FinancialDataService.from_csv_directory(DATA_DIR)
    cases = build_cases(service, companies, canonical_text_sha256(manifest_path))
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    (CASES_DIR / "financial_golden_cases.json").write_text(
        json.dumps({"version": "1.0.0", "cases": cases}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"generated {len(cases)} cases for {len(companies)} companies")


if __name__ == "__main__":
    main()
