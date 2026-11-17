"""Explicit Financial analysis templates (B10.6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from langgraph_langchain.runtime.models import RuntimePlanStep
from langgraph_langchain.runtime.plans import AnalysisPlan

from .intent import FinancialIntent

__all__ = ["FinancialTemplate", "FinancialTemplateRegistry"]


class FinancialTemplateError(ValueError):
    """Raised when a template is invalid or does not match an intent."""


@dataclass(frozen=True)
class FinancialTemplate:
    template_id: str
    title: str
    metrics: tuple[str, ...]
    required_statements: tuple[str, ...]
    version: str = "b10.6"

    def validate(self) -> None:
        if not self.template_id or not self.title:
            raise FinancialTemplateError("template_id and title are required")
        if not self.metrics:
            raise FinancialTemplateError("template must declare metrics")
        if not self.required_statements:
            raise FinancialTemplateError("template must declare required statements")

    def build_plan(
        self,
        intent: FinancialIntent,
        *,
        goal: str | None = None,
    ) -> AnalysisPlan:
        self.validate()
        if intent.metric_id not in self.metrics:
            raise FinancialTemplateError(
                f"metric {intent.metric_id!r} is not part of template {self.template_id!r}"
            )
        step = RuntimePlanStep(
            objective=goal or self.title,
            method="financial_indicator",
            expected_outputs=["financial_indicator_result"],
            parameters={
                **intent.to_parameters(),
                "template_id": self.template_id,
            },
        )
        return AnalysisPlan(
            goal=goal or self.title,
            metrics=list(self.metrics),
            steps=[step],
        )


class FinancialTemplateRegistry:
    def __init__(self) -> None:
        self._templates: dict[str, FinancialTemplate] = {}

    def register(self, template: FinancialTemplate) -> FinancialTemplate:
        template.validate()
        existing = self._templates.get(template.template_id)
        if existing is not None:
            if existing != template:
                raise FinancialTemplateError(
                    f"template conflict for {template.template_id!r}"
                )
            return existing
        self._templates[template.template_id] = template
        return template

    def get(self, template_id: str) -> FinancialTemplate:
        template = self._templates.get(template_id)
        if template is None:
            raise FinancialTemplateError(f"template not found: {template_id}")
        return template

    def list_templates(self) -> list[FinancialTemplate]:
        return sorted(self._templates.values(), key=lambda item: item.template_id)

    def __len__(self) -> int:
        return len(self._templates)


financial_template_registry = FinancialTemplateRegistry()
financial_template_registry.register(
    FinancialTemplate(
        template_id="single_metric_analysis",
        title="Analyze one canonical Financial indicator",
        metrics=(
            "revenue",
            "gross_profit",
            "net_profit",
            "eps",
            "gross_margin",
            "total_assets",
            "total_equity",
            "operating_cash_flow",
            "capital_expenditure",
            "roe",
            "roa",
            "free_cash_flow",
            "revenue_growth",
        ),
        required_statements=("income", "balance", "cash"),
    )
)
