"""Generate independently calculated Formula Golden cases for the real dataset.

Expected values are derived directly from the normalized facts using the
documented formulas.  ``compute_metric`` is intentionally not imported.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from langgraph_langchain.data.assets import canonical_text_sha256
from langgraph_langchain.runtime.financial.real_data import RealFinancialDataLoader


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "tests" / "financial" / "real_data"
GOLDEN_DIR = DATA_DIR / "formula_golden"
GOLDEN_PATH = GOLDEN_DIR / "core_metrics.json"


def fact_value(dataset, company_id: str, period: str, metric_id: str) -> float | None:
    for fact in dataset.facts:
        if (
            fact.company_id == company_id
            and fact.period == period
            and fact.metric_id == metric_id
            and fact.status == "VALID"
        ):
            return fact.value
    return None


def case(
    *,
    case_id: str,
    metric_id: str,
    company_id: str,
    period: str,
    inputs: dict[str, float],
    expected: float,
    unit: str,
    formula: str,
    formula_version: str,
) -> dict:
    return {
        "case_id": case_id,
        "metric_id": metric_id,
        "company_id": company_id,
        "period": period,
        "inputs": {key: round(value, 6) for key, value in inputs.items()},
        "expected": round(expected, 6),
        "unit": unit,
        "formula": formula,
        "formula_version": formula_version,
        "tolerance": 0.000001,
    }


def main() -> None:
    dataset = RealFinancialDataLoader(DATA_DIR).load(strict=True)
    if dataset.validation_errors:
        raise ValueError("; ".join(dataset.validation_errors))

    periods = list(dataset.manifest.periods)
    cases: list[dict] = []

    for company in dataset.manifest.companies:
        company_id = company.company_id
        for period in periods:
            values = {
                metric_id: fact_value(dataset, company_id, period, metric_id)
                for metric_id in (
                    "revenue",
                    "net_profit",
                    "total_assets",
                    "total_liabilities",
                    "total_equity",
                    "operating_cash_flow",
                    "capital_expenditure",
                )
            }
            required = (
                "revenue",
                "net_profit",
                "total_assets",
                "total_liabilities",
                "total_equity",
                "operating_cash_flow",
                "capital_expenditure",
            )
            missing = [key for key in required if values[key] is None]
            if missing:
                raise ValueError(
                    f"Missing facts for {company_id}/{period}: {missing}"
                )

            revenue = float(values["revenue"])
            net_profit = float(values["net_profit"])
            total_assets = float(values["total_assets"])
            total_liabilities = float(values["total_liabilities"])
            total_equity = float(values["total_equity"])
            ocf = float(values["operating_cash_flow"])
            capex = float(values["capital_expenditure"])

            common = {
                "company_id": company_id,
                "period": period,
            }
            cases.append(case(
                case_id=f"real_{company_id}_{period}_revenue",
                metric_id="revenue",
                inputs={"value": revenue},
                expected=revenue,
                unit="CNY",
                formula="reported:income_statement.revenue",
                formula_version="revenue-reported-v1",
                **common,
            ))
            cases.append(case(
                case_id=f"real_{company_id}_{period}_net_profit",
                metric_id="net_profit",
                inputs={"value": net_profit},
                expected=net_profit,
                unit="CNY",
                formula="reported:income_statement.net_profit",
                formula_version="net-profit-reported-v1",
                **common,
            ))
            cases.append(case(
                case_id=f"real_{company_id}_{period}_operating_cash_flow",
                metric_id="operating_cash_flow",
                inputs={"value": ocf},
                expected=ocf,
                unit="CNY",
                formula="reported:cash_flow_statement.operating_cash_flow",
                formula_version="operating-cash-flow-reported-v1",
                **common,
            ))
            cases.append(case(
                case_id=f"real_{company_id}_{period}_free_cash_flow",
                metric_id="free_cash_flow",
                inputs={
                    "operating_cash_flow": ocf,
                    "capital_expenditure": capex,
                },
                expected=ocf - capex,
                unit="CNY",
                formula="operating_cash_flow - capital_expenditure",
                formula_version="free-cash-flow-v1",
                **common,
            ))
            cases.append(case(
                case_id=f"real_{company_id}_{period}_net_margin",
                metric_id="net_margin",
                inputs={
                    "numerator": net_profit,
                    "denominator": revenue,
                },
                expected=net_profit / revenue * 100,
                unit="%",
                formula="net_profit / revenue * 100",
                formula_version="net-margin-v1",
                **common,
            ))
            cases.append(case(
                case_id=f"real_{company_id}_{period}_debt_to_asset",
                metric_id="debt_to_asset",
                inputs={
                    "numerator": total_liabilities,
                    "denominator": total_assets,
                },
                expected=total_liabilities / total_assets * 100,
                unit="%",
                formula="total_liabilities / total_assets * 100",
                formula_version="debt-to-asset-v1",
                **common,
            ))
            cases.append(case(
                case_id=f"real_{company_id}_{period}_ocf_to_net_income",
                metric_id="ocf_to_net_income",
                inputs={
                    "numerator": ocf,
                    "denominator": net_profit,
                },
                expected=ocf / net_profit,
                unit="x",
                formula="operating_cash_flow / net_profit",
                formula_version="ocf-to-net-income-v1",
                **common,
            ))

            period_index = periods.index(period)
            if period_index == 0:
                continue
            previous_period = periods[period_index - 1]

            previous_revenue = fact_value(
                dataset, company_id, previous_period, "revenue"
            )
            previous_net_profit = fact_value(
                dataset, company_id, previous_period, "net_profit"
            )
            previous_total_assets = fact_value(
                dataset, company_id, previous_period, "total_assets"
            )
            previous_total_equity = fact_value(
                dataset, company_id, previous_period, "total_equity"
            )
            if None in (
                previous_revenue,
                previous_net_profit,
                previous_total_assets,
                previous_total_equity,
            ):
                raise ValueError(
                    f"Missing previous-period facts for {company_id}/{previous_period}"
                )

            cases.append(case(
                case_id=f"real_{company_id}_{period}_revenue_growth",
                metric_id="revenue_growth",
                inputs={
                    "current": revenue,
                    "previous": float(previous_revenue),
                },
                expected=(
                    (revenue - float(previous_revenue))
                    / abs(float(previous_revenue))
                    * 100
                ),
                unit="%",
                formula="(current_revenue - previous_revenue) / abs(previous_revenue) * 100",
                formula_version="growth-v1",
                **common,
            ))
            cases.append(case(
                case_id=f"real_{company_id}_{period}_net_profit_growth",
                metric_id="net_profit_growth",
                inputs={
                    "current": net_profit,
                    "previous": float(previous_net_profit),
                },
                expected=(
                    (net_profit - float(previous_net_profit))
                    / abs(float(previous_net_profit))
                    * 100
                ),
                unit="%",
                formula=(
                    "(current_net_profit - previous_net_profit) / "
                    "abs(previous_net_profit) * 100"
                ),
                formula_version="growth-v1",
                **common,
            ))

            average_assets = (
                total_assets + float(previous_total_assets)
            ) / 2
            cases.append(case(
                case_id=f"real_{company_id}_{period}_roa",
                metric_id="roa",
                inputs={
                    "numerator": net_profit,
                    "current_denominator": total_assets,
                    "previous_denominator": float(previous_total_assets),
                    "average_denominator": average_assets,
                },
                expected=net_profit / average_assets * 100,
                unit="%",
                formula="net_profit / average(total_assets) * 100",
                formula_version="average-balance-v1",
                **common,
            ))
            average_equity = (
                total_equity + float(previous_total_equity)
            ) / 2
            cases.append(case(
                case_id=f"real_{company_id}_{period}_roe",
                metric_id="roe",
                inputs={
                    "numerator": net_profit,
                    "current_denominator": total_equity,
                    "previous_denominator": float(previous_total_equity),
                    "average_denominator": average_equity,
                },
                expected=net_profit / average_equity * 100,
                unit="%",
                formula="net_profit / average(total_equity) * 100",
                formula_version="average-balance-v1",
                **common,
            ))

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "financial-real-formula-golden-v1",
        "description": (
            "Expected values independently calculated from the Eastmoney "
            "F10 real-data snapshot; not generated by MetricEngine."
        ),
        "cases": cases,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    GOLDEN_PATH.write_text(text, encoding="utf-8", newline="\n")
    digest = canonical_text_sha256(GOLDEN_PATH)
    print(f"generated {len(cases)} real formula golden cases: {GOLDEN_PATH}")
    print(f"sha256-canonical-text:{digest}")


if __name__ == "__main__":
    main()
