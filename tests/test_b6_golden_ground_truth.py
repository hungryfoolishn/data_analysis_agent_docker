"""B6 golden ground-truth consistency and revenue_change_pct contract tests.

Anti-regression guard introduced by the post-B11 B6 P2 remediation:
every golden expected value must be independently reproducible from the
frozen dataset, and revenue_change_pct must carry explicit comparison
period semantics (no silent defaults).
"""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, ".")

from langgraph_langchain.runtime.research_benchmark import (
    GROUND_TRUTH_INCONSISTENT_CASES,
    BenchmarkCase,
    ResearchBenchmarkRunner,
    UnverifiableMetricError,
    load_golden_benchmark_cases,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = REPO_ROOT / "tests" / "evaluation" / "golden_cases"
DATASET = REPO_ROOT / "tests" / "evaluation" / "data" / "sales_performance_2025_2026h1.csv"


@pytest.fixture(scope="module")
def golden_frame() -> pd.DataFrame:
    return pd.read_csv(DATASET)


@pytest.fixture(scope="module")
def runner() -> ResearchBenchmarkRunner:
    return ResearchBenchmarkRunner(dataset_version="consistency_check")


class TestQ2TrendGroundTruth:
    """P2-1: corrected Q2 trend ground truth must match the dataset."""

    def test_trend_005_values_match_dataset(self, golden_frame, runner):
        cases = {c.case_id: c for c in load_golden_benchmark_cases(CASES_DIR)}
        case = cases["trend_analysis_005"]
        assert case.expected_metrics[0]["value"] == pytest.approx(632016.0)
        assert case.expected_metrics[1]["value"] == pytest.approx(665280.0)
        assert case.expected_metrics[2]["value"] == pytest.approx(632940.0)
        result = runner.run_single(case, golden_frame)
        assert result.answer_accuracy

    def test_trend_006_values_match_dataset(self, golden_frame, runner):
        cases = {c.case_id: c for c in load_golden_benchmark_cases(CASES_DIR)}
        case = cases["trend_analysis_006"]
        assert case.expected_metrics[0]["value"] == pytest.approx(721116.0)
        assert case.expected_metrics[1]["value"] == pytest.approx(757680.0)
        assert case.expected_metrics[2]["value"] == pytest.approx(719565.0)
        result = runner.run_single(case, golden_frame)
        assert result.answer_accuracy

    def test_no_cases_marked_inconsistent(self):
        """After the P2 fix the exclusion list must be empty."""
        assert GROUND_TRUTH_INCONSISTENT_CASES == frozenset()


class TestRevenueChangePeriodContract:
    """P2-2: revenue_change_pct requires explicit compare_period."""

    def _change_case(self, group: str, filters: dict, value: float = 0.0):
        return BenchmarkCase(
            case_id="contract_probe",
            dataset_version="test",
            goal="probe",
            task_type="metric",
            expected_metrics=(
                {
                    "metric": "revenue_change_pct",
                    "value": value,
                    "tolerance": 0.001,
                    "period": "2026-02",
                    "group": group,
                    "filters": filters,
                },
            ),
            dataset="x",
        )

    def test_missing_compare_period_rejected(self, golden_frame, runner):
        """Missing compare_period must fail explicitly, never default."""
        case = self._change_case("杭州开发一部", {"department": "杭州开发一部"})
        with pytest.raises(UnverifiableMetricError):
            runner._compute_metric(golden_frame, case.expected_metrics[0])

    def test_specified_period_computes_ratio(self, golden_frame, runner):
        case = self._change_case(
            "杭州开发一部",
            {"department": "杭州开发一部", "compare_period": "2026-01"},
            value=0.0828,
        )
        value, _frames = runner._compute_metric(golden_frame, case.expected_metrics[0])
        # 2026-02=533544, 2026-01=491744 for 杭州开发一部
        assert value == pytest.approx((533544 - 491744) / 491744)

    def test_zero_baseline_returns_none(self, golden_frame, runner):
        case = self._change_case(
            "杭州开发一部",
            {"department": "杭州开发一部", "compare_period": "2026-01"},
        )
        frame = golden_frame.copy()
        mask = (frame.department == "杭州开发一部") & (frame.period == "2026-01")
        frame.loc[mask, "revenue_actual"] = 0.0
        value, _frames = runner._compute_metric(frame, case.expected_metrics[0])
        assert value is None

    def test_missing_compare_data_returns_none(self, golden_frame, runner):
        case = self._change_case(
            "杭州开发一部",
            {"department": "杭州开发一部", "compare_period": "1999-01"},
        )
        value, _frames = runner._compute_metric(golden_frame, case.expected_metrics[0])
        assert value is None

    def test_missing_department_returns_none(self, golden_frame, runner):
        case = self._change_case(
            "不存在的部门",
            {"department": "不存在的部门", "compare_period": "2026-01"},
        )
        value, _frames = runner._compute_metric(golden_frame, case.expected_metrics[0])
        assert value is None

    def test_comprehensive_cases_have_explicit_compare_period(self):
        """Every comprehensive revenue_change_pct expectation must be explicit."""
        comp = json.loads(
            (CASES_DIR / "comprehensive_analysis.json").read_text(encoding="utf-8")
        )
        count = 0
        for case in comp:
            for exp in case["expected_metrics"]:
                if exp["metric"] != "revenue_change_pct":
                    continue
                count += 1
                filters = exp["filters"]
                assert filters.get("compare_period"), case["case_id"]
                assert filters.get("comparison_type") == "month_over_month"
        assert count == 6  # 6 comprehensive cases, 1 change_pct each


class TestGoldenGroundTruthConsistency:
    """Every scored golden expectation must be reproducible from the dataset."""

    def test_all_50_cases_present(self):
        cases = load_golden_benchmark_cases(CASES_DIR)
        assert len(cases) == 50
        assert all(not c.excluded for c in cases)

    def test_every_expectation_reproducible(self, golden_frame, runner):
        cases = load_golden_benchmark_cases(CASES_DIR)
        checked = 0
        for case in cases:
            single = runner.run_single(case, golden_frame)
            scored = single.scored_results
            assert scored, case.case_id
            for metric in scored:
                assert metric.passed, (
                    case.case_id,
                    metric.metric,
                    metric.expected,
                    metric.actual,
                )
                checked += 1
        assert checked >= 90

    def test_b6_loader_validation_clean(self):
        from langgraph_langchain.runtime.evaluation import GoldenCaseLoader

        loader = GoldenCaseLoader(CASES_DIR)
        assert loader.validate() == []
