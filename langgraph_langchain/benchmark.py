"""Stability benchmark dataset for testing agent reliability.

This module defines a set of test cases to evaluate agent stability and consistency.
"""

from typing import List, Dict, Any
from pathlib import Path
import json


class BenchmarkCase:
    """A single benchmark test case."""

    def __init__(
        self,
        case_id: str,
        name: str,
        description: str,
        data_file: str,
        instruction: str,
        expected_findings_count: int,
        expected_stages: List[str],
        max_steps: int,
        difficulty: str,  # "easy", "medium", "hard"
        tags: List[str],
    ):
        self.case_id = case_id
        self.name = name
        self.description = description
        self.data_file = data_file
        self.instruction = instruction
        self.expected_findings_count = expected_findings_count
        self.expected_stages = expected_stages
        self.max_steps = max_steps
        self.difficulty = difficulty
        self.tags = tags

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "name": self.name,
            "description": self.description,
            "data_file": self.data_file,
            "instruction": self.instruction,
            "expected_findings_count": self.expected_findings_count,
            "expected_stages": self.expected_stages,
            "max_steps": self.max_steps,
            "difficulty": self.difficulty,
            "tags": self.tags,
        }


# Define benchmark cases
BENCHMARK_CASES = [
    BenchmarkCase(
        case_id="BC001",
        name="Simple Sales Analysis",
        description="Basic sales data analysis with clear schema and simple question",
        data_file="benchmark_data/sales_simple.csv",
        instruction="What are the top 3 products by revenue?",
        expected_findings_count=1,
        expected_stages=["schema_understanding", "data_quality_check", "basic_eda", "deep_dive", "conclusion_synthesis", "report_generation"],
        max_steps=15,
        difficulty="easy",
        tags=["sales", "aggregation", "ranking"],
    ),
    BenchmarkCase(
        case_id="BC002",
        name="Time Series Trend Analysis",
        description="Analyze trends over time with date parsing",
        data_file="benchmark_data/sales_timeseries.csv",
        instruction="Analyze the monthly sales trend over the past year. Are sales increasing or decreasing?",
        expected_findings_count=2,
        expected_stages=["schema_understanding", "data_quality_check", "basic_eda", "deep_dive", "conclusion_synthesis", "report_generation"],
        max_steps=20,
        difficulty="medium",
        tags=["timeseries", "trend", "visualization"],
    ),
    BenchmarkCase(
        case_id="BC003",
        name="Customer Segmentation",
        description="Segment customers based on behavior patterns",
        data_file="benchmark_data/customers.csv",
        instruction="Segment customers into groups based on their purchase behavior. What are the characteristics of each segment?",
        expected_findings_count=3,
        expected_stages=["schema_understanding", "data_quality_check", "basic_eda", "deep_dive", "conclusion_synthesis", "report_generation"],
        max_steps=25,
        difficulty="hard",
        tags=["segmentation", "clustering", "customer_analysis"],
    ),
    BenchmarkCase(
        case_id="BC004",
        name="Missing Data Handling",
        description="Dataset with significant missing values",
        data_file="benchmark_data/sales_missing.csv",
        instruction="Analyze sales performance. Handle missing values appropriately.",
        expected_findings_count=2,
        expected_stages=["schema_understanding", "data_quality_check", "basic_eda", "deep_dive", "conclusion_synthesis", "report_generation"],
        max_steps=20,
        difficulty="medium",
        tags=["data_quality", "missing_values", "cleaning"],
    ),
    BenchmarkCase(
        case_id="BC005",
        name="Correlation Analysis",
        description="Find correlations between multiple variables",
        data_file="benchmark_data/marketing.csv",
        instruction="What factors are most correlated with conversion rate?",
        expected_findings_count=2,
        expected_stages=["schema_understanding", "data_quality_check", "basic_eda", "deep_dive", "conclusion_synthesis", "report_generation"],
        max_steps=20,
        difficulty="medium",
        tags=["correlation", "feature_importance", "marketing"],
    ),
    BenchmarkCase(
        case_id="BC006",
        name="Outlier Detection",
        description="Identify and analyze outliers in the data",
        data_file="benchmark_data/transactions.csv",
        instruction="Identify unusual transactions that might indicate fraud or errors.",
        expected_findings_count=2,
        expected_stages=["schema_understanding", "data_quality_check", "basic_eda", "deep_dive", "conclusion_synthesis", "report_generation"],
        max_steps=20,
        difficulty="medium",
        tags=["outliers", "anomaly_detection", "fraud"],
    ),
    BenchmarkCase(
        case_id="BC007",
        name="Multi-Metric Dashboard",
        description="Calculate and compare multiple business metrics",
        data_file="benchmark_data/ecommerce.csv",
        instruction="Create a dashboard showing key e-commerce metrics: conversion rate, average order value, customer lifetime value.",
        expected_findings_count=3,
        expected_stages=["schema_understanding", "data_quality_check", "basic_eda", "deep_dive", "conclusion_synthesis", "report_generation"],
        max_steps=25,
        difficulty="hard",
        tags=["metrics", "dashboard", "ecommerce"],
    ),
    BenchmarkCase(
        case_id="BC008",
        name="Cohort Analysis",
        description="Analyze user cohorts over time",
        data_file="benchmark_data/user_activity.csv",
        instruction="Perform a cohort analysis to understand user retention over time.",
        expected_findings_count=3,
        expected_stages=["schema_understanding", "data_quality_check", "basic_eda", "deep_dive", "conclusion_synthesis", "report_generation"],
        max_steps=30,
        difficulty="hard",
        tags=["cohort", "retention", "user_behavior"],
    ),
    BenchmarkCase(
        case_id="BC009",
        name="A/B Test Analysis",
        description="Compare two groups and determine statistical significance",
        data_file="benchmark_data/ab_test.csv",
        instruction="Analyze the A/B test results. Is there a statistically significant difference between the control and treatment groups?",
        expected_findings_count=2,
        expected_stages=["schema_understanding", "data_quality_check", "basic_eda", "deep_dive", "conclusion_synthesis", "report_generation"],
        max_steps=20,
        difficulty="medium",
        tags=["ab_test", "statistics", "hypothesis_testing"],
    ),
    BenchmarkCase(
        case_id="BC010",
        name="Ambiguous Schema",
        description="Dataset with unclear column names requiring interpretation",
        data_file="benchmark_data/unclear_schema.csv",
        instruction="Analyze the data and provide insights. (Note: column names are abbreviated)",
        expected_findings_count=1,
        expected_stages=["schema_understanding", "data_quality_check", "basic_eda", "deep_dive", "conclusion_synthesis", "report_generation"],
        max_steps=25,
        difficulty="hard",
        tags=["schema_understanding", "ambiguity", "interpretation"],
    ),
]


class BenchmarkRunner:
    """Runs benchmark tests and tracks results."""

    def __init__(self, results_file: Path = None):
        self.results_file = results_file or Path("./workspace/.benchmark_results.json")
        self.results: Dict[str, Any] = self._load_results()

    def _load_results(self) -> Dict[str, Any]:
        """Load previous benchmark results."""
        if self.results_file.exists():
            try:
                with open(self.results_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"runs": [], "summary": {}}

    def _save_results(self):
        """Save benchmark results."""
        try:
            self.results_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.results_file, "w", encoding="utf-8") as f:
                json.dump(self.results, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Failed to save benchmark results: {e}")

    def record_run(
        self,
        case_id: str,
        success: bool,
        steps: int,
        duration_seconds: float,
        findings_count: int,
        stages_completed: List[str],
        failure_code: str = None,
        notes: str = None,
    ):
        """Record a benchmark run result."""
        from datetime import datetime

        run_result = {
            "case_id": case_id,
            "timestamp": datetime.now().isoformat(),
            "success": success,
            "steps": steps,
            "duration_seconds": duration_seconds,
            "findings_count": findings_count,
            "stages_completed": stages_completed,
            "failure_code": failure_code,
            "notes": notes,
        }

        self.results["runs"].append(run_result)
        self._update_summary()
        self._save_results()

    def _update_summary(self):
        """Update summary statistics."""
        runs = self.results["runs"]
        if not runs:
            return

        total_runs = len(runs)
        successful_runs = sum(1 for r in runs if r["success"])
        failed_runs = total_runs - successful_runs

        # Group by case_id
        by_case = {}
        for run in runs:
            case_id = run["case_id"]
            if case_id not in by_case:
                by_case[case_id] = []
            by_case[case_id].append(run)

        # Calculate per-case stats
        case_stats = []
        for case_id, case_runs in by_case.items():
            case_total = len(case_runs)
            case_success = sum(1 for r in case_runs if r["success"])
            case_stats.append({
                "case_id": case_id,
                "total_runs": case_total,
                "successful_runs": case_success,
                "success_rate": case_success / case_total if case_total > 0 else 0,
            })

        self.results["summary"] = {
            "total_runs": total_runs,
            "successful_runs": successful_runs,
            "failed_runs": failed_runs,
            "overall_success_rate": successful_runs / total_runs if total_runs > 0 else 0,
            "case_stats": case_stats,
        }

    def get_summary(self) -> Dict[str, Any]:
        """Get benchmark summary."""
        return self.results.get("summary", {})

    def get_case_history(self, case_id: str) -> List[Dict[str, Any]]:
        """Get run history for a specific case."""
        return [r for r in self.results["runs"] if r["case_id"] == case_id]


def get_benchmark_cases() -> List[BenchmarkCase]:
    """Get all benchmark cases."""
    return BENCHMARK_CASES


def get_benchmark_case(case_id: str) -> BenchmarkCase:
    """Get a specific benchmark case by ID."""
    for case in BENCHMARK_CASES:
        if case.case_id == case_id:
            return case
    raise ValueError(f"Benchmark case {case_id} not found")


def export_benchmark_cases(output_file: Path):
    """Export benchmark cases to JSON file."""
    cases_dict = [case.to_dict() for case in BENCHMARK_CASES]
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({"cases": cases_dict}, f, indent=2, ensure_ascii=False)
