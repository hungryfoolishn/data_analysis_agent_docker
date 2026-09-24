"""Formula Golden evaluation for Runtime V9.3."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .data_service import FinancialDataService
from .metrics import MetricComputation, compute_metric
from .period import PeriodNormalizer


FormulaComputer = Callable[..., MetricComputation]


@dataclass(frozen=True)
class FormulaGoldenCase:
    case_id: str
    metric_id: str
    company_id: str
    period: str
    expected: float | None
    unit: str
    formula: str
    formula_version: str
    tolerance: float = 0.0005
    inputs: dict[str, float | None] = field(default_factory=dict)
    expected_status: str = "CALCULATED"


@dataclass(frozen=True)
class FormulaGoldenResult:
    case_id: str
    metric_id: str
    company_id: str
    period: str
    expected: float | None
    actual: float | None
    status: str
    passed: bool
    message: str
    formula_version: str
    actual_inputs: dict[str, float | None] = field(default_factory=dict)


@dataclass(frozen=True)
class FormulaGoldenSummary:
    total_cases: int
    passed_cases: int
    failed_cases: int
    pass_rate: float
    results: list[FormulaGoldenResult]

    @property
    def failures(self) -> list[FormulaGoldenResult]:
        return [item for item in self.results if not item.passed]


class FormulaGoldenLoader:
    """Load static Formula Golden cases independent of MetricEngine."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def load(self) -> list[FormulaGoldenCase]:
        if not self.directory.is_dir():
            raise FileNotFoundError(f"Formula golden directory not found: {self.directory}")

        cases: list[FormulaGoldenCase] = []
        for path in sorted(self.directory.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            entries = raw.get("cases", []) if isinstance(raw, dict) else raw
            if not isinstance(entries, list):
                raise ValueError(f"Formula golden file must contain a list: {path}")
            cases.extend(FormulaGoldenCase(**item) for item in entries)

        case_ids = [item.case_id for item in cases]
        duplicates = {case_id for case_id in case_ids if case_ids.count(case_id) > 1}
        if duplicates:
            raise ValueError(f"Duplicate formula golden case IDs: {', '.join(sorted(duplicates))}")
        return cases


class FormulaGoldenRunner:
    """Compare MetricEngine output with independently recorded golden values."""

    def __init__(
        self,
        data_service: FinancialDataService,
        *,
        metric_computer: FormulaComputer | None = None,
        period_normalizer: PeriodNormalizer | None = None,
    ) -> None:
        self.data_service = data_service
        self.metric_computer = metric_computer or compute_metric
        self.period_normalizer = period_normalizer or PeriodNormalizer()

    def run_case(self, case: FormulaGoldenCase) -> FormulaGoldenResult:
        company = self.data_service.resolve_company(case.company_id)
        period = self.period_normalizer.parse(case.period).normalized_period
        previous_period = self.data_service.previous_period(company.company_id, period)
        income, balance, cash_flow, previous_income = self.data_service.statements(
            company.company_id,
            period,
            previous_period,
        )
        previous_balance = self.data_service.previous_balance(company.company_id, period)

        common = {
            "case_id": case.case_id,
            "metric_id": case.metric_id,
            "company_id": company.company_id,
            "period": period,
            "formula_version": case.formula_version,
        }
        try:
            computation = self.metric_computer(
                case.metric_id,
                income=income,
                balance=balance,
                cash_flow=cash_flow,
                previous_income=previous_income,
                previous_balance=previous_balance,
            )
        except Exception as exc:
            return FormulaGoldenResult(
                **common,
                expected=case.expected,
                actual=None,
                status="error",
                passed=False,
                message=f"MetricEngine execution failed: {exc}",
            )

        if computation.status != "calculated" or computation.value is None:
            return FormulaGoldenResult(
                **common,
                expected=case.expected,
                actual=None,
                status=computation.status,
                passed=False,
                message=(
                    f"MetricEngine status={computation.status}, "
                    f"reason={computation.reason}"
                ),
                actual_inputs=computation.inputs,
            )

        if case.inputs:
            normalized_case_inputs = {
                key: None if value is None else round(float(value), 6)
                for key, value in case.inputs.items()
            }
            normalized_actual_inputs = {
                key: None if value is None else round(float(value), 6)
                for key, value in computation.inputs.items()
            }
            if normalized_case_inputs != normalized_actual_inputs:
                return FormulaGoldenResult(
                    **common,
                    expected=case.expected,
                    actual=computation.value,
                    status="input_mismatch",
                    passed=False,
                    message=(
                        "Calculation inputs do not match Formula Golden inputs; "
                        f"expected={normalized_case_inputs}, actual={normalized_actual_inputs}."
                    ),
                    actual_inputs=computation.inputs,
                )

        if case.expected is None:
            return FormulaGoldenResult(
                **common,
                expected=None,
                actual=computation.value,
                status="golden_unavailable",
                passed=False,
                message="Formula Golden expected value is missing.",
                actual_inputs=computation.inputs,
            )

        tolerance = max(0.0, case.tolerance)
        difference = abs(computation.value - case.expected)
        allowed = tolerance
        passed = difference <= allowed
        return FormulaGoldenResult(
            **common,
            expected=case.expected,
            actual=computation.value,
            status="passed" if passed else "failed",
            passed=passed,
            message=(
                f"MetricEngine matches Formula Golden within {allowed:.6g}."
                if passed
                else f"MetricEngine differs by {difference:.6g}; allowed {allowed:.6g}."
            ),
            actual_inputs=computation.inputs,
        )

    def run(self, cases: Iterable[FormulaGoldenCase]) -> FormulaGoldenSummary:
        results = [self.run_case(case) for case in cases]
        passed = sum(item.passed for item in results)
        return FormulaGoldenSummary(
            total_cases=len(results),
            passed_cases=passed,
            failed_cases=len(results) - passed,
            pass_rate=passed / len(results) if results else 0.0,
            results=results,
        )
