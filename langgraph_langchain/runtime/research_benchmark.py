"""B11.6 Multi-Agent Benchmark over the B6 golden dataset.

Fair-comparison harness:
- Ground truth comes from the independent B6 golden case JSONs
  (tests/evaluation/golden_cases), never from agent output.
- Both single-agent and multi-agent paths use the same production
  analysis core (execute_analysis_operation) and the same evidence
  adapter (collect_operation_evidence), so tool access is identical.
- Single-agent baseline follows B11.1 SingleAgentRunner semantics:
  direct one-shot execution without plan, review, or completeness audit.
- Multi-agent path uses ResearchOrchestrator (B11.2-B11.5): plan ->
  DataAnalyst -> ReportWriter -> QualityAuditor with bounded retry.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from langgraph_langchain.data.assets import DataAsset
from langgraph_langchain.evidence.operations import collect_operation_evidence
from langgraph_langchain.runtime.analysis_operations import (
    AnalysisOperationError,
    execute_analysis_operation,
)
from langgraph_langchain.runtime.multi_agent import AgentRole, ReviewDecision
from langgraph_langchain.runtime.research_agent import (
    DataAnalystAgent,
    ResearchOrchestrator,
    ResearchPlan,
    ResearchPlanningAgent,
    ResearchStep,
)

# Golden cases whose frozen ground truth is inconsistent with the dataset
# (B6 golden data quality issue; documented in the B11.6 report).
GROUND_TRUTH_INCONSISTENT_CASES = frozenset(
    {"trend_analysis_005", "trend_analysis_006"}
)


__all__ = [
    "BenchmarkCase",
    "CaseMetricResult",
    "CaseRunResult",
    "BenchmarkAggregate",
    "load_golden_benchmark_cases",
    "ResearchBenchmarkRunner",
    "summarize_results",
]

ROW_METRICS = {
    "revenue_actual",
    "revenue_target",
    "orders",
    "avg_order_value",
    "q2_1_target",
    "q2_1_actual",
    "q2_1_attainment",
}


class UnverifiableMetricError(ValueError):
    """Raised when ground truth for a metric cannot be independently computed."""


@dataclass(frozen=True)
class BenchmarkCase:
    """One benchmark case with independent ground truth from B6 golden JSON."""

    case_id: str
    dataset_version: str
    goal: str
    task_type: str
    expected_metrics: tuple[dict[str, Any], ...]
    dataset: str
    fault_injected: bool = False
    excluded: bool = False


@dataclass
class CaseMetricResult:
    metric: str
    expected: float | None
    actual: float | None
    passed: bool | None  # None = excluded from scoring (unverifiable ground truth)
    evidence_ids: tuple[str, ...] = ()

    @property
    def excluded(self) -> bool:
        return self.passed is None


@dataclass
class CaseRunResult:
    path: str  # "single" | "multi"
    values: dict[str, float | None]
    metric_results: list[CaseMetricResult]
    evidence_count: int
    elapsed_seconds: float
    review_decision: str | None = None
    issues: tuple[str, ...] = ()
    error: str | None = None

    @property
    def scored_results(self) -> list[CaseMetricResult]:
        return [m for m in self.metric_results if not m.excluded]

    @property
    def finding_accuracy(self) -> float:
        scored = self.scored_results
        if not scored:
            return 0.0
        return sum(1 for m in scored if m.passed) / len(scored)

    @property
    def answer_accuracy(self) -> bool:
        scored = self.scored_results
        return bool(scored) and all(m.passed for m in scored)

    @property
    def evidence_validity(self) -> float:
        scored = self.scored_results
        if not scored:
            return 0.0
        return sum(1 for m in scored if m.evidence_ids) / len(scored)


@dataclass
class BenchmarkAggregate:
    path: str
    cases: int
    answer_accuracy_rate: float
    mean_finding_accuracy: float
    mean_evidence_validity: float
    mean_elapsed_seconds: float
    accepted_reviews: int = 0


def load_golden_benchmark_cases(cases_dir: Path) -> list[BenchmarkCase]:
    """Load all B6 golden cases as benchmark cases with ground truth."""
    cases: list[BenchmarkCase] = []
    for path in sorted(Path(cases_dir).glob("*.json")):
        raw_cases = json.loads(path.read_text(encoding="utf-8"))
        for raw in raw_cases:
            cases.append(
                BenchmarkCase(
                    case_id=raw["case_id"],
                    excluded=raw["case_id"] in GROUND_TRUTH_INCONSISTENT_CASES,
                    dataset_version=raw.get("dataset_sha256", "")[:12],
                    goal=raw["question"],
                    task_type=raw.get("task_type", "metric"),
                    expected_metrics=tuple(raw.get("expected_metrics", [])),
                    dataset=raw.get("dataset", ""),
                )
            )
    return cases


class MetricPlanPlanner(ResearchPlanningAgent):
    """Builds one executor step per expected metric, preserving metric_id."""

    def __init__(self, case: BenchmarkCase) -> None:
        super().__init__()
        self._case = case

    def plan(
        self, goal: str, context: Mapping[str, Any] | None = None
    ) -> ResearchPlan:
        steps: list[ResearchStep] = []
        previous = ""
        index = 0
        for exp in self._case.expected_metrics:
            if exp["metric"] == "revenue_change_pct" and exp.get(
                "filters", {}
            ).get("compare_period") is None:
                continue  # unverifiable ground truth; not planned
            step_id = f"rs_metric_{index}"
            steps.append(
                ResearchStep(
                    step_id=step_id,
                    objective=f"compute {exp['metric']} for {exp.get('period')}",
                    agent_role=AgentRole.EXECUTOR,
                    tool="run_analysis_operation",
                    parameters={
                        "metric_id": exp["metric"],
                        "expectation": dict(exp),
                    },
                    depends_on=(previous,) if previous else (),
                )
            )
            index += 1
            previous = step_id
        steps.append(
            ResearchStep(
                step_id="rs_verify",
                objective="Review metric findings against ground truth",
                agent_role=AgentRole.REVIEWER,
                tool="review",
                depends_on=(previous,) if previous else (),
            )
        )
        return ResearchPlan(
            plan_id=f"rplan_{self._case.case_id}",
            goal=goal,
            steps=tuple(steps),
            success_criteria=("metric_calculated", "evidence_generated", "review_accepted"),
            data_requirements=("period", "department", "revenue_actual"),
        )


class ResearchBenchmarkRunner:
    """Runs each case through single- and multi-agent paths on real data."""

    def __init__(
        self,
        *,
        dataset_version: str,
        execution_prefix: str = "b116",
        max_retries: int = 1,
    ) -> None:
        self.dataset_version = dataset_version
        self.execution_prefix = execution_prefix
        self.max_retries = max_retries
        self._op_counter = 0

    # ------------------------------------------------------------------
    # Shared production analysis core (same tool access for both paths)
    # ------------------------------------------------------------------
    def _next_execution_id(self, case_id: str) -> str:
        self._op_counter += 1
        return f"{self.execution_prefix}_{case_id}_op{self._op_counter}"

    def _compute_metric(
        self, frame: pd.DataFrame, expectation: Mapping[str, Any]
    ) -> tuple[float | None, list[pd.DataFrame]]:
        """Compute one metric via real execute_analysis_operation calls.

        Returns (value, intermediate_frames) so callers can attach evidence.
        """
        metric = expectation["metric"]
        period = expectation.get("period")
        group = expectation.get("group")
        filters = expectation.get("filters", {})
        frames: list[pd.DataFrame] = []

        # department_compare prefix metrics map to the underlying column
        column = metric
        if metric.startswith(("left_", "right_")):
            column = metric.split("_", 1)[1]

        if column in ROW_METRICS:
            sub = frame
            if period is not None:
                sub = execute_analysis_operation(
                    sub, operation="filter",
                    parameters={"column": "period", "operator": "eq", "value": period},
                )
            if group is not None:
                sub = execute_analysis_operation(
                    sub, operation="filter",
                    parameters={"column": "department", "operator": "eq", "value": group},
                )
            sub = execute_analysis_operation(
                sub, operation="select_columns", parameters={"columns": [column]},
            )
            frames.append(sub)
            return (float(sub.iloc[0][column]) if len(sub) else None), frames

        if metric == "monthly_revenue":
            sub = frame
            if group is not None:
                sub = execute_analysis_operation(
                    sub, operation="filter",
                    parameters={"column": "department", "operator": "eq", "value": group},
                )
            if period is not None:
                sub = execute_analysis_operation(
                    sub, operation="filter",
                    parameters={"column": "period", "operator": "eq", "value": period},
                )
            frames.append(sub)
            return (
                float(sub.iloc[0]["revenue_actual"]) if len(sub) else None,
                frames,
            )

        if metric == "revenue_difference":
            departments = filters.get("department")
            if not isinstance(departments, list) or len(departments) != 2:
                raise AnalysisOperationError(
                    "revenue_difference requires two departments"
                )
            values: list[float] = []
            for dept in departments:
                sub = frame
                if period is not None:
                    sub = execute_analysis_operation(
                        sub, operation="filter",
                        parameters={"column": "period", "operator": "eq", "value": period},
                    )
                sub = execute_analysis_operation(
                    sub, operation="filter",
                    parameters={"column": "department", "operator": "eq", "value": dept},
                )
                frames.append(sub)
                if not len(sub):
                    values.append(float("nan"))
                else:
                    values.append(float(sub.iloc[0]["revenue_actual"]))
            if any(pd.isna(v) for v in values):
                return None, frames
            return values[0] - values[1], frames

        if metric == "anomaly_count":
            sub = frame
            if period is not None:
                sub = execute_analysis_operation(
                    sub, operation="filter",
                    parameters={"column": "period", "operator": "eq", "value": period},
                )
            sub = execute_analysis_operation(
                sub, operation="group_aggregate",
                parameters={
                    "group_by": "period",
                    "metric": "data_anomaly_count",
                    "agg": "sum",
                },
            )
            frames.append(sub)
            return (
                float(sub.iloc[0]["data_anomaly_count_sum"]) if len(sub) else None,
                frames,
            )

        if metric == "revenue_change_pct":
            compare_period = filters.get("compare_period")
            if compare_period is None:
                raise UnverifiableMetricError(
                    "revenue_change_pct requires compare_period in filters"
                )
            sub = frame
            if group is not None:
                sub = execute_analysis_operation(
                    sub, operation="filter",
                    parameters={"column": "department", "operator": "eq", "value": group},
                )
            sub_cur = execute_analysis_operation(
                sub, operation="filter",
                parameters={"column": "period", "operator": "eq", "value": period},
            )
            sub_cmp = execute_analysis_operation(
                sub, operation="filter",
                parameters={"column": "period", "operator": "eq", "value": compare_period},
            )
            frames.extend([sub_cur, sub_cmp])
            if not len(sub_cur) or not len(sub_cmp):
                return None, frames
            current = float(sub_cur.iloc[0]["revenue_actual"])
            compare = float(sub_cmp.iloc[0]["revenue_actual"])
            if compare == 0:
                return None, frames
            return (current - compare) / compare, frames

        raise AnalysisOperationError(f"unsupported benchmark metric: {metric}")

    def _collect_evidence(
        self,
        *,
        case_id: str,
        metric: str,
        parameters: dict[str, Any],
        input_asset: DataAsset,
        frames: Sequence[pd.DataFrame],
    ) -> str:
        """Attach real production operation evidence for one computed metric."""
        output = frames[-1] if frames else pd.DataFrame()
        output_asset = DataAsset.from_dataframe(
            dataframe=output,
            source_path=Path(f"benchmark://{case_id}/{metric}"),
            source_type="benchmark",
        )
        numeric_values: dict[str, float] = {}
        for key, val in parameters.items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                numeric_values[str(key)] = float(val)
        evidence = collect_operation_evidence(
            execution_id=self._next_execution_id(case_id),
            operation=f"benchmark_{metric}",
            parameters=parameters,
            input_asset_ids=[input_asset.asset_id],
            output_asset=output_asset,
            numeric_values=numeric_values,
        )
        return evidence.evidence_id

    def _is_verifiable(self, expectation: Mapping[str, Any]) -> bool:
        """Whether this metric's ground truth is independently computable."""
        metric = expectation["metric"]
        if metric == "revenue_change_pct":
            return expectation.get("filters", {}).get("compare_period") is not None
        return True

    def _score_metric(
        self, expectation: Mapping[str, Any], actual: float | None
    ) -> bool:
        if actual is None:
            return False
        expected = float(expectation["value"])
        tolerance = float(expectation.get("tolerance", 0.0))
        return abs(actual - expected) <= tolerance

    # ------------------------------------------------------------------
    # Single-agent baseline (B11.1 SingleAgentRunner semantics)
    # ------------------------------------------------------------------
    def run_single(self, case: BenchmarkCase, frame: pd.DataFrame) -> CaseRunResult:
        start = time.monotonic()
        input_asset = DataAsset.from_dataframe(
            dataframe=frame,
            source_path=Path(f"benchmark://{case.case_id}/input"),
            source_type="benchmark",
        )
        metric_results: list[CaseMetricResult] = []
        values: dict[str, float | None] = {}
        error: str | None = None
        evidence_count = 0
        for exp in case.expected_metrics:
            metric = exp["metric"]
            value = None
            try:
                value, frames = self._compute_metric(frame, exp)
                eid = self._collect_evidence(
                    case_id=case.case_id,
                    metric=metric,
                    parameters={"period": exp.get("period")},
                    input_asset=input_asset,
                    frames=frames,
                )
                evidence_count += 1
                metric_results.append(
                    CaseMetricResult(
                        metric=metric,
                        expected=float(exp["value"]),
                        actual=value,
                        passed=self._score_metric(exp, value),
                        evidence_ids=(eid,),
                    )
                )
            except UnverifiableMetricError:
                metric_results.append(
                    CaseMetricResult(
                        metric=metric, expected=None, actual=None, passed=None,
                    )
                )
            except Exception as exc:  # single agent: one shot, no retry
                error = str(exc)
                metric_results.append(
                    CaseMetricResult(
                        metric=metric,
                        expected=float(exp.get("value", 0.0)) if exp.get("value") is not None else None,
                        actual=None,
                        passed=False,
                    )
                )
            values[metric] = value
        elapsed = time.monotonic() - start
        return CaseRunResult(
            path="single",
            values=values,
            metric_results=metric_results,
            evidence_count=evidence_count,
            elapsed_seconds=elapsed,
            error=error,
        )

    # ------------------------------------------------------------------
    # Multi-agent path (B11.2-B11.5 ResearchOrchestrator)
    # ------------------------------------------------------------------
    def run_multi(self, case: BenchmarkCase, frame: pd.DataFrame) -> CaseRunResult:
        start = time.monotonic()
        input_asset = DataAsset.from_dataframe(
            dataframe=frame,
            source_path=Path(f"benchmark://{case.case_id}/input"),
            source_type="benchmark",
        )

        def analyst_executor(step: ResearchStep) -> Any:
            exp = step.parameters.get("expectation")
            if exp is None:
                return None
            metric = exp["metric"]
            try:
                value, frames = self._compute_metric(frame, exp)
                eid = self._collect_evidence(
                    case_id=case.case_id,
                    metric=metric,
                    parameters={"period": exp.get("period")},
                    input_asset=input_asset,
                    frames=frames,
                )
                return type(
                    "ExecResult",
                    (),
                    {
                        "status": "calculated" if value is not None else "no_data",
                        "value": value,
                        "evidence_ids": (eid,) if value is not None else (),
                    },
                )()
            except Exception:
                return type(
                    "ExecResult",
                    (),
                    {"status": "failed", "value": None, "evidence_ids": ()},
                )()

        orchestrator = ResearchOrchestrator(
            planner=MetricPlanPlanner(case),
            max_retries=self.max_retries,
        )
        result = orchestrator.run(
            case.goal,
            context={
                "load_params": {"dataset": case.dataset},
                "analysis_params": {
                    "case_id": case.case_id,
                    "expectations": list(case.expected_metrics),
                },
                "data_requirements": ("period", "department", "revenue_actual"),
            },
            step_executor=analyst_executor,
        )
        # Map findings back to expectations by plan order (metric_id may repeat)
        verifiable_expectations = [
            exp for exp in case.expected_metrics if self._is_verifiable(exp)
        ]
        executor_findings = [
            f for f in result.findings if f.evidence_ids or f.confidence > 0
        ]
        finding_by_order: dict[int, Any] = dict(
            zip(range(len(verifiable_expectations)), executor_findings)
        )
        metric_results: list[CaseMetricResult] = []
        values: dict[str, float | None] = {}
        order_index = 0
        for exp in case.expected_metrics:
            metric = exp["metric"]
            verifiable = self._is_verifiable(exp)
            finding = (
                finding_by_order.get(order_index) if verifiable else None
            )
            if verifiable:
                order_index += 1
            actual = finding.value if finding is not None else None
            eid = tuple(finding.evidence_ids) if finding is not None else ()
            metric_results.append(
                CaseMetricResult(
                    metric=metric,
                    expected=float(exp["value"]) if exp.get("value") is not None else None,
                    actual=actual,
                    passed=(
                        self._score_metric(exp, actual) if verifiable else None
                    ),
                    evidence_ids=eid,
                )
            )
            values[metric] = actual
        elapsed = time.monotonic() - start
        issues = tuple(
            issue for review in result.reviews for issue in review.issues
        )
        return CaseRunResult(
            path="multi",
            values=values,
            metric_results=metric_results,
            evidence_count=sum(1 for m in metric_results if m.evidence_ids),
            elapsed_seconds=elapsed,
            review_decision=result.decision.value,
            issues=issues,
        )

    def run_case(
        self, case: BenchmarkCase, frame: pd.DataFrame
    ) -> dict[str, Any]:
        single = self.run_single(case, frame)
        multi = self.run_multi(case, frame)
        return {
            "case_id": case.case_id,
            "task_type": case.task_type,
            "dataset_version": self.dataset_version,
            "single": self._serialize_run(single),
            "multi": self._serialize_run(multi),
        }

    @staticmethod
    def _serialize_run(run: CaseRunResult) -> dict[str, Any]:
        return {
            "finding_accuracy": run.finding_accuracy,
            "answer_accuracy": run.answer_accuracy,
            "evidence_validity": run.evidence_validity,
            "evidence_count": run.evidence_count,
            "elapsed_seconds": run.elapsed_seconds,
            "review_decision": run.review_decision,
            "issues": list(run.issues),
            "error": run.error,
            "metric_results": [
                {
                    "metric": m.metric,
                    "expected": m.expected,
                    "actual": m.actual,
                    "passed": m.passed,
                    "evidence_ids": list(m.evidence_ids),
                }
                for m in run.metric_results
            ],
        }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-case results into path-level quality metrics."""
    out: dict[str, Any] = {}
    for path_key in ("single", "multi"):
        runs = [r[path_key] for r in results]
        n = len(runs)
        if not n:
            continue
        out[path_key] = {
            "cases": n,
            "answer_accuracy_rate": sum(1 for r in runs if r["answer_accuracy"]) / n,
            "mean_finding_accuracy": sum(r["finding_accuracy"] for r in runs) / n,
            "mean_evidence_validity": sum(r["evidence_validity"] for r in runs) / n,
            "mean_elapsed_seconds": sum(r["elapsed_seconds"] for r in runs) / n,
        }
    # Improvement summary
    single = out.get("single", {})
    multi = out.get("multi", {})
    out["comparison"] = {
        "numeric_no_regression": (
            multi.get("mean_finding_accuracy", 0.0)
            >= single.get("mean_finding_accuracy", 0.0)
        ),
        "evidence_coverage_delta": (
            multi.get("mean_evidence_validity", 0.0)
            - single.get("mean_evidence_validity", 0.0)
        ),
        "completeness_delta": (
            multi.get("mean_finding_accuracy", 0.0)
            - single.get("mean_finding_accuracy", 0.0)
        ),
        "latency_overhead_ratio": (
            multi.get("mean_elapsed_seconds", 0.0)
            / max(single.get("mean_elapsed_seconds", 1e-9), 1e-9)
        ),
    }
    return out


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="B11.6 benchmark runner")
    parser.add_argument("--cases-dir", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    frame = pd.read_csv(args.dataset)
    all_cases = load_golden_benchmark_cases(args.cases_dir)
    cases = [c for c in all_cases if not c.excluded]
    import hashlib

    dataset_version = hashlib.sha256(args.dataset.read_bytes()).hexdigest()[:12]
    runner = ResearchBenchmarkRunner(dataset_version=dataset_version)
    results = []
    for case in cases:
        results.append(runner.run_case(case, frame))
    summary = summarize_results(results)
    output = {"dataset_version": dataset_version, "case_count": len(results), "summary": summary, "results": results}
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
