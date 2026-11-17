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


def _success_cases():
    return [case for case in _load_golden()["cases"] if case["expected_status"] == "calculated"]


def _rejected_cases():
    return [case for case in _load_golden()["cases"] if case["expected_status"] == "rejected"]


def _statements(case):
    if case["metric_id"] in {"roe", "roa"}:
        income = dict(case)
        income.update({
            "statement_type": "income",
            "period_type": "duration",
            "period_start": "2024-01-01",
            "raw_line_items": {"net_profit": case["raw_line_items"]["net_profit"]},
        })
        balance = dict(case)
        balance.update({
            "statement_type": "balance",
            "period_type": "instant",
            "period_start": None,
            "raw_line_items": {
                key: value
                for key, value in case["raw_line_items"].items()
                if key != "net_profit"
            },
        })
        return [_statement(income), _statement(balance)]
    return [_statement(case)]


def _statement(case):
    items = []
    for concept, value in case["raw_line_items"].items():
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
    assert payload["schema_version"] == "b10.8.5"
    assert payload["normalization_version"] == "b10.2.3"
    assert payload["period_semantics_version"] == "b10.8.3"
    assert payload["catalog_version"] == "b10.8.5"
    assert {case["market"] for case in payload["cases"]} == {"us", "cn"}
    assert {case["currency"] for case in payload["cases"]} == {"USD", "CNY"}


@pytest.mark.parametrize("case", _success_cases(), ids=lambda item: item["case_id"])
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
    context = FinancialContext(intent=intent, statements=_statements(case))
    result = context.resolve(case["metric_id"])
    assert result.status == "calculated"
    assert result.value == Decimal(case["expected_value"])
    assert result.statistic_kind == case["expected_statistic_kind"]


@pytest.mark.parametrize("case", _success_cases(), ids=lambda item: item["case_id"])
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
    context = FinancialContext(intent=intent, statements=_statements(case))
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

def test_golden_minimum_coverage_matrix_is_met():
    payload = _load_golden()
    cases = payload["cases"]
    assert len(cases) >= 20
    assert sum(case["expected_status"] == "calculated" for case in cases) >= 10
    assert sum(case["expected_status"] == "rejected" for case in cases) >= 10
    assert {case["market"] for case in cases} == {"us", "cn"}
    assert {case["statement_type"] for case in cases} == {"income", "balance", "cash"}
    assert {case["period_basis"] for case in cases} >= {"single_period"}
    assert all(case["expected_reason"] for case in cases if case["expected_status"] == "rejected")
    assert all(case["raw_line_items"] is not None for case in cases)


@pytest.mark.parametrize("case", _rejected_cases(), ids=lambda item: item["case_id"])
def test_golden_rejection_records_explicit_reason_and_no_indicator(case):
    assert case["expected_status"] == "rejected"
    assert case["expected_reason"]
    assert case["expected_evidence"] is None
    assert case["expected_value"] is None


def test_golden_sec_raw_fixture_enters_b8_data_asset(tmp_path):
    from langgraph_langchain.data.adapters.base import FetchRequest
    from langgraph_langchain.data.adapters.registry import AdapterRegistry
    from langgraph_langchain.data.acquisition import AcquisitionService
    from langgraph_langchain.runtime.context import AnalysisRuntime
    from langgraph_langchain.runtime.financial.sec import SecCompanyFactsAdapter

    raw = json.loads((Path(__file__).parent / "fixtures" / "financial" / "sec" / "companyconcept_amendment.json").read_text(encoding="utf-8"))
    adapter = SecCompanyFactsAdapter(transport=lambda url, params: raw)
    request = FetchRequest(
        dataset_id="sec_company_facts",
        parameters={
            "cik": "0000320193",
            "canonical_concept": "Revenues",
            "preferred_unit": "USD",
            "entity_id": "ent_us",
            "statement_type": "income",
            "reporting_basis": "consolidated",
            "statistic_kind": "absolute_amount",
        },
    )
    runtime = AnalysisRuntime(workspace_dir=tmp_path / "workspace", session_id="golden-sec", question="golden SEC")
    registry = AdapterRegistry(); registry.register(adapter)
    outcome = AcquisitionService(runtime=runtime, registry=registry, history_dir=tmp_path / "history").acquire(request)
    assert outcome.asset.source_metadata["snapshot_reference"]["row_count"] == 2
    assert outcome.asset.source_metadata["source_id"] == "financial_sec_company_facts"


def test_golden_china_announcement_and_document_facts_enter_b8(tmp_path):
    from langgraph_langchain.data.adapters.base import FetchRequest
    from langgraph_langchain.data.adapters.registry import AdapterRegistry
    from langgraph_langchain.data.acquisition import AcquisitionService
    from langgraph_langchain.runtime.context import AnalysisRuntime
    from langgraph_langchain.runtime.financial.china import ChinaCompanyFilingsAdapter
    from langgraph_langchain.runtime.financial.china_documents import (
        ChinaStatementPeriod,
        china_fact_to_row,
        parse_china_announcement_list,
        parse_china_statement_csv,
    )

    root = Path(__file__).parent / "fixtures" / "financial" / "china"
    announcement_payload = json.loads((root / "announcement_list.json").read_text(encoding="utf-8"))
    announcement = parse_china_announcement_list(announcement_payload).announcements[0]
    period = ChinaStatementPeriod(
        period_start="2024-01-01",
        period_end="2024-03-31",
        fiscal_year=2024,
        fiscal_period="Q1",
        reporting_basis="consolidated",
    )
    mapping = {"营业收入": "revenue", "营业成本": "cost_of_sales", "净利润": "net_profit"}
    document = (root / "statement_income_sample.csv").read_bytes()
    facts = parse_china_statement_csv(
        document,
        announcement=announcement,
        period=period,
        statement_type="income",
        canonical_concepts=mapping,
    )
    rows = [china_fact_to_row(fact, entity_id="ent_cn") for fact in facts if fact.value is not None]
    adapter = ChinaCompanyFilingsAdapter(transport=lambda url, params: rows)
    request = FetchRequest(dataset_id="cn_company_filings", parameters={"stock_code": "600000"})
    runtime = AnalysisRuntime(workspace_dir=tmp_path / "workspace", session_id="golden-china", question="golden China")
    registry = AdapterRegistry(); registry.register(adapter)
    outcome = AcquisitionService(runtime=runtime, registry=registry, history_dir=tmp_path / "history").acquire(request)
    assert outcome.asset.source_metadata["snapshot_reference"]["row_count"] == 2
    assert outcome.asset.source_metadata["source_id"] == "financial_cn_company_filings"


def test_golden_period_missing_intermediate_is_explicit():
    from langgraph_langchain.runtime.financial.periods import (
        PeriodSemanticsError,
        derive_single_quarter_item,
    )

    q1 = _statement({
        "market": "us", "entity_id": "ent_us", "statement_type": "income", "period_type": "duration",
        "period_start": "2024-01-01", "period_end": "2024-03-31", "fiscal_year": 2024,
        "fiscal_period": "Q1", "reporting_basis": "consolidated", "currency": "USD",
        "raw_line_items": {"revenue": "100"},
    })
    fy = _statement({
        "market": "us", "entity_id": "ent_us", "statement_type": "income", "period_type": "duration",
        "period_start": "2024-01-01", "period_end": "2024-12-31", "fiscal_year": 2024,
        "fiscal_period": "FY", "reporting_basis": "consolidated", "currency": "USD",
        "raw_line_items": {"revenue": "600"},
    })
    with pytest.raises(PeriodSemanticsError, match="missing_intermediate"):
        derive_single_quarter_item(fy, q1, concept="revenue")
