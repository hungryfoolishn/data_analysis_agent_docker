"""Suite runner that keeps deterministic scoring separate from agent execution."""

from __future__ import annotations

from collections.abc import Callable
from typing import Iterable

from .deterministic import DeterministicEvaluator
from .models import EvaluationCase, EvaluationRunInput, EvaluationSummary


class EvaluationRunner:
    def __init__(self, evaluator: DeterministicEvaluator | None = None):
        self.evaluator = evaluator or DeterministicEvaluator()

    def score(
        self,
        suite_id: str,
        pairs: Iterable[tuple[EvaluationCase, EvaluationRunInput]],
        *,
        metadata: dict | None = None,
    ) -> EvaluationSummary:
        pairs = list(pairs)
        results = [self.evaluator.evaluate(case, run) for case, run in pairs]
        return EvaluationSummary.from_results(
            suite_id,
            results,
            critical_case_ids={case.case_id for case, _ in pairs if case.critical},
            metadata=metadata,
        )

    def execute_and_score(
        self,
        suite_id: str,
        cases: Iterable[EvaluationCase],
        execute: Callable[[EvaluationCase], EvaluationRunInput],
        *,
        metadata: dict | None = None,
    ) -> EvaluationSummary:
        return self.score(suite_id, ((case, execute(case)) for case in cases), metadata=metadata)
