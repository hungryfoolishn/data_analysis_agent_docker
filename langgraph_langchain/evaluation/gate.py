"""Release gate based on category-level deterministic evaluation changes."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .models import EvaluationSummary


class ReleaseGatePolicy(BaseModel):
    minimum_category_scores: dict[str, float] = Field(default_factory=lambda: {
        "calculation": 1.0,
        "evidence": 0.95,
        "overclaim": 1.0,
        "workflow": 0.95,
    })
    maximum_regression: float = Field(default=0.0, ge=0.0)
    allow_critical_failures: bool = False


class GateDecision(BaseModel):
    allowed: bool
    reasons: list[str]
    current_suite_id: str
    baseline_suite_id: str | None = None


class ReleaseGate:
    def __init__(self, policy: ReleaseGatePolicy | None = None):
        self.policy = policy or ReleaseGatePolicy()

    def decide(
        self,
        current: EvaluationSummary,
        baseline: EvaluationSummary | None = None,
    ) -> GateDecision:
        reasons: list[str] = []
        if current.critical_failures and not self.policy.allow_critical_failures:
            reasons.append(f"{current.critical_failures} critical evaluation case(s) failed")
        for category, minimum in self.policy.minimum_category_scores.items():
            actual = current.category_scores.get(category, 0.0)
            if actual < minimum:
                reasons.append(f"{category} score {actual:.3f} is below required {minimum:.3f}")
        if baseline is not None:
            for category, baseline_score in baseline.category_scores.items():
                current_score = current.category_scores.get(category, 0.0)
                regression = baseline_score - current_score
                if regression > self.policy.maximum_regression:
                    reasons.append(
                        f"{category} regressed by {regression:.3f} "
                        f"({baseline_score:.3f} -> {current_score:.3f})"
                    )
        return GateDecision(
            allowed=not reasons,
            reasons=reasons,
            current_suite_id=current.suite_id,
            baseline_suite_id=baseline.suite_id if baseline else None,
        )
