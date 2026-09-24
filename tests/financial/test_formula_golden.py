"""Runtime V9.3 Formula Golden evaluation tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from langgraph_langchain.runtime.financial import (
    CALCULATED,
    FinancialDataService,
    FormulaGoldenCase,
    FormulaGoldenLoader,
    FormulaGoldenRunner,
    MetricComputation,
    compute_metric,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "tests" / "financial" / "data" / "financial"
GOLDEN_DIR = REPO_ROOT / "tests" / "financial" / "formula_golden"


def build_runner() -> FormulaGoldenRunner:
    return FormulaGoldenRunner(FinancialDataService.from_csv_directory(DATA_DIR))


def test_formula_golden_loader_loads_versioned_cases():
    cases = FormulaGoldenLoader(GOLDEN_DIR).load()

    assert len(cases) >= 15
    assert len({case.case_id for case in cases}) == len(cases)
    assert all(case.formula for case in cases)
    assert all(case.formula_version for case in cases)
    assert all(case.expected is not None for case in cases)
    assert all(case.tolerance >= 0 for case in cases)


def test_formula_golden_expected_values_pass_with_metric_engine():
    cases = FormulaGoldenLoader(GOLDEN_DIR).load()
    summary = build_runner().run(cases)

    assert summary.total_cases == len(cases)
    assert summary.passed_cases == len(cases)
    assert summary.failed_cases == 0
    assert summary.pass_rate == 1.0
    assert not summary.failures


def test_formula_golden_detects_roe_ending_equity_mutation():
    cases = FormulaGoldenLoader(GOLDEN_DIR).load()
    roe_case = next(case for case in cases if case.metric_id == "roe")

    def mutated_roe(metric_id: str, **kwargs):
        computation = compute_metric(metric_id, **kwargs)
        if metric_id != "roe":
            return computation
        income = kwargs["income"]
        balance = kwargs["balance"]
        return MetricComputation(
            metric_id=metric_id,
            status=CALCULATED,
            value=round(income.net_profit / balance.total_equity * 100, 6),
            inputs=computation.inputs,
        )

    summary = FormulaGoldenRunner(
        FinancialDataService.from_csv_directory(DATA_DIR),
        metric_computer=mutated_roe,
    ).run([roe_case])

    assert summary.failed_cases == 1
    assert summary.failures[0].case_id == roe_case.case_id
    assert summary.failures[0].status == "failed"
    assert summary.failures[0].message.startswith("MetricEngine differs by")


def test_formula_golden_detects_wrong_numeric_result():
    cases = FormulaGoldenLoader(GOLDEN_DIR).load()
    case = next(item for item in cases if item.metric_id == "revenue")

    def wrong_result(metric_id: str, **kwargs):
        computation = compute_metric(metric_id, **kwargs)
        return replace(computation, value=computation.value + 1)

    summary = FormulaGoldenRunner(
        FinancialDataService.from_csv_directory(DATA_DIR),
        metric_computer=wrong_result,
    ).run([case])

    assert summary.failed_cases == 1
    assert summary.failures[0].case_id == case.case_id
    assert summary.failures[0].expected == case.expected
    assert summary.failures[0].actual == case.expected + 1


def test_formula_golden_detects_calculation_input_mismatch():
    cases = FormulaGoldenLoader(GOLDEN_DIR).load()
    roe_case = next(case for case in cases if case.metric_id == "roe")
    mismatched_inputs = dict(roe_case.inputs)
    mismatched_inputs["previous_denominator"] = (
        mismatched_inputs["previous_denominator"] + 1
    )
    mutated_case = replace(roe_case, inputs=mismatched_inputs)

    summary = build_runner().run([mutated_case])

    assert summary.failed_cases == 1
    assert summary.failures[0].status == "input_mismatch"
    assert "Calculation inputs do not match Formula Golden inputs" in summary.failures[0].message
