"""B10.7: deterministic Financial Golden Dataset and E2E coverage."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from langgraph_langchain.runtime.financial.context import FinancialContext
from langgraph_langchain.runtime.financial.evidence import register_financial_evidence
from langgraph_langchain.evidence.registry import EvidenceRegistry
from langgraph_langchain.runtime.financial.intent import resolve_financial_intent
from langgraph_langchain.runtime.financial.models import (
    FinancialLineItem,
    FinancialStatement,
    FinancialUnit,
)
from langgraph_langchain.runtime.financial.runtime_integration import FinancialDomainRuntime


ROOT = Path(__file__).parent / "fixtures" / "financial" / "b10_golden.json"


def _load_golden():
    return json.loads(ROOT.read_text(encoding="utf-8"))


def _statement(case):
    items = []
    for concept, value in case["line_items"].items():
        kind = "per_share" if concept == "eps" else "absolute_amount"
        unit = FinancialUnit(
            statistic_kind=kind,
            numeric_unit=case["currency"] if kind == "absolute_amount" else case["currency"],
            currency=case["currency"],
            unit_denominator="share" if concept == "eps" else None,
        )
        items.append(
            FinancialLineItem(
                canonical_concept=concept,
                value=Decimal(value),
                statistic_kind=kind,
                unit=unit,
            )
        )
    return FinancialStatement(
        entity_id=case["entity_id"],
        statement_type=case["statement_type"],
        period_type=case["period_type"],
        period_start=case["period_start"],
        period_end=case["period_end"],
        fiscal_year=case["fiscal_year"],
        fiscal_period=case["fiscal_period"],
        reporting_basis=case["reporting_basis"],
        currency=case["currency"],
        line_items=tuple(items),
    )


def test_golden_dataset_versions_and_market_coverage_are_explicit():
    payload = _load_golden()
    assert payload["schema_version"] == "b10.7"
    assert payload["normalization_version"] == "b10.2.3"
    assert payload["period_semantics_version"] == "b10.3"
    assert payload["catalog_version"] == "b10.4"
    assert {case["market"] for case in payload["cases"]} == {"us", "cn"}
    assert {case["currency"] for case in payload["cases"]} == {"USD", "CNY"}


@pytest.mark.parametrize("case", _load_golden()["cases"], ids=lambda item: item["case_id"])
def test_golden_indicator_is_exact_and_semantically_explicit(case):
    intent = resolve_financial_intent(
        {
            "market": case["market"],
            "entity_id": case["entity_id"],
            "statement_type": case["statement_type"],
            "fiscal_year": case["fiscal_year"],
            "fiscal_period": case["fiscal_period"],
            "reporting_basis": case["reporting_basis"],
            "currency": case["currency"],
            "metric_id": case["metric_id"],
            "market_scope": case["market"],
        }
    )
    context = FinancialContext(intent=intent, statements=[_statement(case)])
    result = context.resolve(case["metric_id"])
    assert result.status == "calculated"
    assert result.value == Decimal(case["expected_value"])
    assert result.statistic_kind == case["expected_statistic_kind"]


@pytest.mark.parametrize("case", _load_golden()["cases"], ids=lambda item: item["case_id"])
def test_golden_case_runs_through_runtime_v2_and_evidence_bridge(case):
    intent = resolve_financial_intent(
        {
            "market": case["market"],
            "entity_id": case["entity_id"],
            "statement_type": case["statement_type"],
            "fiscal_year": case["fiscal_year"],
            "fiscal_period": case["fiscal_period"],
            "reporting_basis": case["reporting_basis"],
            "currency": case["currency"],
            "metric_id": case["metric_id"],
            "market_scope": case["market"],
        }
    )
    context = FinancialContext(intent=intent, statements=[_statement(case)])
    runtime = FinancialDomainRuntime(context=context)
    result = runtime.run(intent)
    assert result.execution.status == "succeeded"
    assert result.indicator.value == Decimal(case["expected_value"])
    registry = EvidenceRegistry(executions={result.execution.execution_id: result.execution})
    evidence = register_financial_evidence(
        registry,
        execution=result.execution,
        indicator=result.indicator,
        evidence_text=f"{case['case_id']} canonical indicator.",
    )
    assert evidence.validation_status == "reference_validated"
    assert evidence.numeric_values[case["metric_id"]] == float(Decimal(case["expected_value"]))
