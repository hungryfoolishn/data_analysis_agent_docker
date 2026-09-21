"""Offline golden evaluation runner for Runtime V8.5."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Mapping

from .comparator import compare_metric
from .models import (
    CaseEvaluation,
    EvaluationRun,
    EvaluationScore,
    GoldenCandidateResult,
    GoldenCase,
    MetricAnswer,
    MetricExpectation,
)


class GoldenEvaluationRunner:
    """Score analysis correctness independently from Runtime execution success."""

    WEIGHTS = {
        "execution": 0.10,
        "verification": 0.10,
        "numeric": 0.30,
        "evidence": 0.15,
        "finding": 0.15,
        "report": 0.20,
    }

    @staticmethod
    def _context_matches(answer: MetricAnswer, expectation: MetricExpectation) -> bool:
        if expectation.period and answer.period != expectation.period:
            return False
        if expectation.group and answer.group != expectation.group:
            return False
        for key, value in expectation.filters.items():
            if answer.filters.get(key) != value:
                return False
        return True

    @classmethod
    def _answer_for(
        cls,
        expectation: MetricExpectation,
        answers: list[MetricAnswer],
    ) -> MetricAnswer | None:
        matching = [
            item for item in answers
            if item.metric == expectation.metric
            and cls._context_matches(item, expectation)
        ]
        return matching[0] if matching else None

    def run_case(
        self,
        case: GoldenCase,
        candidate: GoldenCandidateResult,
    ) -> CaseEvaluation:
        comparisons = []
        for expectation in case.expected_metrics:
            answer = self._answer_for(expectation, candidate.metric_answers)
            comparisons.append(
                compare_metric(expectation, answer.value if answer is not None else None)
            )
        numeric_passed = sum(item.passed for item in comparisons)
        numeric_score = numeric_passed / len(comparisons) if comparisons else 0.0

        execution_score = 1.0 if candidate.status == "succeeded" else 0.0
        verification_score = 1.0 if candidate.verification_passed else 0.0
        evidence_score = (
            min(1.0, candidate.verified_evidence_count / max(1, case.minimum_verified_evidence))
            if case.required_evidence
            else 1.0
        )
        finding_score = 1.0 if candidate.finding_count > 0 else 0.0 if case.required_finding else 1.0

        failures: list[str] = []
        failures.extend(
            f"numeric:{item.metric}:{item.message}" for item in comparisons if not item.passed
        )
        if candidate.status != "succeeded":
            failures.append(f"execution:{candidate.status}")
        if not candidate.verification_passed:
            failures.append("verification:not_passed")
        if case.required_evidence and candidate.verified_evidence_count < case.minimum_verified_evidence:
            failures.append(
                f"evidence:verified={candidate.verified_evidence_count},required={case.minimum_verified_evidence}"
            )
        if case.required_finding and candidate.finding_count <= 0:
            failures.append("finding:not_created")
        if case.required_sql and not candidate.sql_text.strip():
            failures.append("sql:not_present")

        report_text = candidate.report_text.casefold()
        missing_report = [
            token for token in case.report_must_contain if token.casefold() not in report_text
        ]
        forbidden_report = [
            token for token in case.report_must_not_contain if token.casefold() in report_text
        ]
        report_tokens = [*case.report_must_contain, *case.report_must_not_contain]
        report_score = (
            (len(report_tokens) - len(missing_report) - len(forbidden_report)) / len(report_tokens)
            if report_tokens
            else 1.0
        )
        failures.extend(f"report:missing:{token}" for token in missing_report)
        failures.extend(f"report:forbidden:{token}" for token in forbidden_report)

        score = EvaluationScore(
            execution_score=execution_score,
            verification_score=verification_score,
            numeric_score=numeric_score,
            evidence_score=evidence_score,
            finding_score=finding_score,
            report_score=report_score,
            total_score=(
                self.WEIGHTS["execution"] * execution_score
                + self.WEIGHTS["verification"] * verification_score
                + self.WEIGHTS["numeric"] * numeric_score
                + self.WEIGHTS["evidence"] * evidence_score
                + self.WEIGHTS["finding"] * finding_score
                + self.WEIGHTS["report"] * report_score
            ),
        )

        # A golden case passes only when correctness-critical components pass;
        # score explains partial quality and is never a substitute for numeric PASS.
        passed = not failures and all(item.passed for item in comparisons)
        return CaseEvaluation(
            case_id=case.case_id,
            run_id=candidate.run_id,
            task_type=case.task_type,
            executor_type=case.executor_type,
            skill_name=candidate.skill_name,
            skill_id=candidate.skill_id,
            skill_version=candidate.skill_version,
            skill_hash=candidate.skill_hash,
            passed=passed,
            score=score,
            metric_comparisons=comparisons,
            failures=failures,
            duration_ms=candidate.duration_ms,
            metadata={
                "critical": case.critical,
                "tags": list(case.tags),
                "candidate_metadata": candidate.metadata,
            },
        )

    def run_dataset(
        self,
        cases: Iterable[GoldenCase],
        candidates: Mapping[str, GoldenCandidateResult],
        *,
        run_id: str | None = None,
    ) -> EvaluationRun:
        evaluations = []
        for case in cases:
            candidate = candidates.get(case.case_id)
            if candidate is None:
                candidate = GoldenCandidateResult(
                    case_id=case.case_id,
                    run_id=run_id or "missing_candidate",
                    status="missing",
                    verification_passed=False,
                )
            evaluations.append(self.run_case(case, candidate))

        return self.build_run(evaluations, run_id=run_id)

    @staticmethod
    def build_run(
        evaluations: Iterable[CaseEvaluation],
        *,
        run_id: str | None = None,
    ) -> EvaluationRun:
        items = list(evaluations)
        total = len(items)
        passed = sum(item.passed for item in items)
        scores = [item.score.total_score for item in items]

        def averages(selector) -> dict[str, float]:
            grouped: dict[str, list[float]] = defaultdict(list)
            for item in items:
                key = selector(item)
                grouped[key].append(item.score.total_score)
            return {
                key: round(sum(values) / len(values), 4)
                for key, values in sorted(grouped.items())
            }

        return EvaluationRun(
            run_id=run_id or "golden_evaluation",
            total_cases=total,
            passed_cases=passed,
            failed_cases=total - passed,
            pass_rate=passed / total if total else 0.0,
            average_score=round(sum(scores) / len(scores), 4) if scores else 0.0,
            by_task_type=averages(lambda item: item.task_type),
            by_executor=averages(lambda item: item.executor_type),
            by_skill=averages(lambda item: item.skill_name or "__fallback__"),
            failures=[item for item in items if not item.passed],
            evaluations=items,
        )
