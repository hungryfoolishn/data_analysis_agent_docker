"""B10.6: canonical Financial intent/context/template/evidence workflow."""

from __future__ import annotations

from decimal import Decimal

import pytest

from langgraph_langchain.evidence.registry import EvidenceRegistry
from langgraph_langchain.runtime.financial.context import (
    FinancialContext,
    FinancialContextError,
)
from langgraph_langchain.runtime.financial.intent import (
    FinancialIntentError,
    resolve_financial_intent,
)
from langgraph_langchain.runtime.financial.models import (
    FinancialLineItem,
    FinancialStatement,
    FinancialUnit,
)
from langgraph_langchain.runtime.financial.runtime_integration import (
    FinancialDomainRuntime,
)
from langgraph_langchain.runtime.financial.templates import FinancialTemplateError


def _item(concept, value, currency="CNY"):
    if concept == "eps":
        unit = FinancialUnit(
            statistic_kind="per_share",
            numeric_unit=currency,
            currency=currency,
            unit_denominator="share",
        )
        kind = "per_share"
    else:
        unit = FinancialUnit(
            statistic_kind="absolute_amount",
            numeric_unit=currency,
            currency=currency,
        )
        kind = "absolute_amount"
    return FinancialLineItem(
        canonical_concept=concept,
        value=value,
        statistic_kind=kind,
        unit=unit,
    )


def _statement(year, values, statement_type="income", currency="CNY"):
    return FinancialStatement(
        entity_id="ent_1",
        statement_type=statement_type,
        period_type="duration" if statement_type != "balance" else "instant",
        period_start=f"{year}-01-01",
        period_end=f"{year}-12-31",
        fiscal_year=year,
        fiscal_period="FY",
        reporting_basis="consolidated",
        currency=currency,
        line_items=tuple(_item(concept, Decimal(str(value))) for concept, value in values.items()),
    )


def _intent(**overrides):
    fields = {
        "market": "cn",
        "entity_id": "ent_1",
        "statement_type": "income",
        "fiscal_year": 2024,
        "fiscal_period": "FY",
        "reporting_basis": "consolidated",
        "currency": "CNY",
        "metric_id": "revenue",
    }
    fields.update(overrides)
    return resolve_financial_intent(fields)


def test_intent_requires_explicit_fields_without_fuzzy_parsing():
    with pytest.raises(FinancialIntentError, match="requires explicit fields"):
        resolve_financial_intent({"market": "cn"})
    intent = _intent()
    assert intent.to_parameters()["metric_id"] == "revenue"
    with pytest.raises(FinancialIntentError, match="unsupported market"):
        resolve_financial_intent(
            {
                **intent.to_parameters(),
                "market": "jp",
            }
        )


def test_template_builds_explicit_runtime_v2_plan():
    intent = _intent()
    from langgraph_langchain.runtime.financial.templates import financial_template_registry

    template = financial_template_registry.get("single_metric_analysis")
    plan = template.build_plan(intent)
    assert len(plan.steps) == 1
    assert plan.steps[0].method == "financial_indicator"
    assert plan.steps[0].parameters["metric_id"] == "revenue"
    with pytest.raises(FinancialTemplateError, match="not part of template"):
        template.build_plan(_intent(metric_id="unknown_metric"))


def test_context_resolves_raw_indicator_from_canonical_statement():
    intent = _intent()
    context = FinancialContext(intent=intent, statements=[_statement(2024, {"revenue": 1234.5})])
    result = context.resolve("revenue")
    assert result.status == "calculated"
    assert result.value == Decimal("1234.5")
    assert result.lineage["accession_number"] is None


def test_context_resolves_ratio_and_growth():
    intent = _intent(metric_id="gross_margin")
    context = FinancialContext(
        intent=intent,
        statements=[_statement(2024, {"revenue": 1000, "gross_profit": 250})],
    )
    ratio = context.resolve("gross_margin")
    assert ratio.status == "calculated"
    assert ratio.value == Decimal("25")

    growth_intent = _intent(metric_id="revenue_growth")
    growth_context = FinancialContext(
        intent=growth_intent,
        statements=[
            _statement(2023, {"revenue": 100}),
            _statement(2024, {"revenue": 110}),
        ],
    )
    growth = growth_context.resolve("revenue_growth")
    assert growth.status == "calculated"
    assert growth.value == Decimal("10")


def test_context_missing_statement_is_explicit():
    intent = _intent()
    context = FinancialContext(intent=intent, statements=[_statement(2023, {"revenue": 1})])
    with pytest.raises(FinancialContextError, match="expected exactly one"):
        context.resolve("revenue")


def test_domain_runtime_runs_through_runtime_v2_and_records_evidence():
    intent = _intent()
    context = FinancialContext(
        intent=intent,
        statements=[_statement(2024, {"revenue": 1234.5})],
        input_asset_ids=("asset_1",),
    )
    runtime = FinancialDomainRuntime(context=context)
    result = runtime.run(intent, session_id="financial_v2", run_id="run_v2")
    assert result.execution.status == "succeeded"
    assert result.execution.tool_name == "financial_indicator"
    assert result.indicator.value == Decimal("1234.5")
    registry = EvidenceRegistry(assets={"asset_1": object()}, executions={result.execution.execution_id: result.execution})
    from langgraph_langchain.runtime.financial.evidence import register_financial_evidence

    evidence = register_financial_evidence(
        registry,
        execution=result.execution,
        indicator=result.indicator,
        evidence_text="Canonical revenue is explicitly traceable.",
        input_asset_ids=context.input_asset_ids,
    )
    assert evidence.validation_status == "reference_validated"
    assert evidence.numeric_values["revenue"] == 1234.5
    assert evidence.source_metadata["financial_metric"]["statistic_kind"] == "absolute_amount"
