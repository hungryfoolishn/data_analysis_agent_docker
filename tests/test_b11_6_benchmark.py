"""B11.6 Multi-Agent benchmark tests over the B6 golden dataset."""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, ".")

from langgraph_langchain.runtime.research_benchmark import (
    BenchmarkCase,
    ResearchBenchmarkRunner,
    load_golden_benchmark_cases,
    summarize_results,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = REPO_ROOT / "tests" / "evaluation" / "golden_cases"
DATASET = REPO_ROOT / "tests" / "evaluation" / "data" / "sales_performance_2025_2026h1.csv"


@pytest.fixture(scope="module")
def golden_frame() -> pd.DataFrame:
    return pd.read_csv(DATASET)


@pytest.fixture(scope="module")
def golden_cases() -> list[BenchmarkCase]:
    cases = load_golden_benchmark_cases(CASES_DIR)
    assert len(cases) >= 50
    return cases


class TestGoldenCaseLoading:
    def test_ground_truth_is_independent(self, golden_cases):
        """Ground truth comes from golden JSON, never from agent output."""
        case = golden_cases[0]
        assert case.expected_metrics
        for exp in case.expected_metrics:
            assert "value" in exp
            assert "tolerance" in exp
            assert "metric" in exp

    def test_case_types_covered(self, golden_cases):
        types = {c.task_type for c in golden_cases}
        assert {"metric", "comparison", "trend", "anomaly", "report"} <= types


class TestBenchmarkRunner:
    def test_single_path_scores_ground_truth(self, golden_cases, golden_frame):
        case = next(c for c in golden_cases if c.task_type == "metric")
        runner = ResearchBenchmarkRunner(dataset_version="test_v1")
        result = runner.run_single(case, golden_frame)
        assert result.metric_results
        assert result.finding_accuracy >= 0.0
        # every metric must be attempted exactly once (one-shot baseline)
        assert len(result.metric_results) == len(case.expected_metrics)

    def test_multi_path_produces_review_and_synthesis(
        self, golden_cases, golden_frame
    ):
        case = next(c for c in golden_cases if c.task_type == "metric")
        runner = ResearchBenchmarkRunner(dataset_version="test_v1")
        result = runner.run_multi(case, golden_frame)
        assert result.review_decision == "accept"
        assert result.evidence_count >= len(case.expected_metrics)

    def test_anomaly_count_matches_ground_truth(self, golden_cases, golden_frame):
        case = next(c for c in golden_cases if c.task_type == "anomaly")
        runner = ResearchBenchmarkRunner(dataset_version="test_v1")
        result = runner.run_single(case, golden_frame)
        assert result.answer_accuracy

    def test_comparison_difference_matches_ground_truth(
        self, golden_cases, golden_frame
    ):
        case = next(c for c in golden_cases if c.task_type == "comparison")
        runner = ResearchBenchmarkRunner(dataset_version="test_v1")
        result = runner.run_multi(case, golden_frame)
        assert result.answer_accuracy
        assert result.review_decision == "accept"

    def test_period_change_pct_matches_ground_truth(
        self, golden_cases, golden_frame
    ):
        case = next(c for c in golden_cases if c.task_type == "trend")
        # period_comparison cases have single revenue_change_pct expectation
        change_cases = [
            c for c in golden_cases
            if any(m["metric"] == "revenue_change_pct" for m in c.expected_metrics)
        ]
        assert change_cases
        runner = ResearchBenchmarkRunner(dataset_version="test_v1")
        result = runner.run_multi(change_cases[0], golden_frame)
        assert result.answer_accuracy

    def test_missing_data_discloses_failure(self, golden_frame):
        """Fault injection: non-existent department must fail both paths honestly."""
        case = BenchmarkCase(
            case_id="fault_missing_dept",
            dataset_version="test_v1",
            goal="查询不存在部门的收入",
            task_type="metric",
            expected_metrics=(
                {
                    "metric": "revenue_actual",
                    "value": 12345.0,
                    "tolerance": 0.01,
                    "period": "2026-01",
                    "group": "不存在的部门",
                    "filters": {"department": "不存在的部门"},
                },
            ),
            dataset="tests/evaluation/data/sales_performance_2025_2026h1.csv",
        )
        runner = ResearchBenchmarkRunner(dataset_version="test_v1")
        single = runner.run_single(case, golden_frame)
        multi = runner.run_multi(case, golden_frame)
        # Single: silent failure (no value, no structured disclosure)
        assert single.metric_results[0].actual is None
        assert not single.metric_results[0].passed
        # Multi: auditor surfaces structured failure
        assert multi.review_decision in {"reject", "revise"}
        assert multi.issues


class TestFairComparison:
    def test_multi_no_numeric_regression(self, golden_cases, golden_frame):
        """Quality gate 1: multi-agent numeric correctness must not regress."""
        runner = ResearchBenchmarkRunner(dataset_version="test_v1")
        sample = golden_cases[:8]
        results = [runner.run_case(c, golden_frame) for c in sample]
        summary = summarize_results(results)
        assert summary["comparison"]["numeric_no_regression"]

    def test_summary_structure(self, golden_cases, golden_frame):
        runner = ResearchBenchmarkRunner(dataset_version="test_v1")
        results = [runner.run_case(golden_cases[0], golden_frame)]
        summary = summarize_results(results)
        for path_key in ("single", "multi"):
            block = summary[path_key]
            assert block["cases"] == 1
            assert 0.0 <= block["answer_accuracy_rate"] <= 1.0
            assert 0.0 <= block["mean_evidence_validity"] <= 1.0
            assert block["mean_elapsed_seconds"] >= 0.0
        comp = summary["comparison"]
        assert "numeric_no_regression" in comp
        assert "evidence_coverage_delta" in comp
        assert comp["latency_overhead_ratio"] >= 0.0
