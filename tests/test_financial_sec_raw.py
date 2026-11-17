import json
from decimal import Decimal
from pathlib import Path

import pytest

from langgraph_langchain.data.adapters.base import AdapterRequestError
from langgraph_langchain.data.acquisition import AcquisitionService
from langgraph_langchain.data.adapters.registry import AdapterRegistry
from langgraph_langchain.runtime.financial.sec import SecCompanyFactsAdapter
from langgraph_langchain.runtime.financial.sec_raw import (
    SecConceptParseError,
    parse_sec_company_concept,
)


FIXTURES = Path(__file__).parent / "fixtures" / "financial" / "sec"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _request(**overrides):
    parameters = {
        "cik": "0000320193",
        "canonical_concept": "Revenues",
        "statement_type": "income",
        "reporting_basis": "consolidated",
        "statistic_kind": "absolute_amount",
        "entity_id": "ent_us",
        "preferred_unit": "USD",
    }
    parameters.update(overrides)
    from langgraph_langchain.data.adapters.base import FetchRequest

    return FetchRequest(dataset_id="sec_company_facts", parameters=parameters)


def test_income_duration_facts_keep_one_row_per_filing():
    parsed = parse_sec_company_concept(
        _load("companyconcept_income.json"),
        taxonomy="us-gaap",
        concept="Revenues",
        preferred_unit="USD",
    )
    assert len(parsed.facts) == 2
    assert {fact.accession_number for fact in parsed.facts} == {
        "0000320193-24-000001",
        "0000320193-24-000002",
    }
    assert all(fact.period_type == "duration" for fact in parsed.facts)
    assert parsed.facts[0].value == Decimal("119575000000")
    assert parsed.facts[0].source_document_id.startswith("sec:0000320193-24-000001:USD:2024-01-01:")
    assert parsed.source_response_hash


def test_instant_fact_has_no_start():
    parsed = parse_sec_company_concept(
        _load("companyconcept_instant.json"),
        taxonomy="us-gaap",
        concept="Assets",
        preferred_unit="USD",
    )
    fact = parsed.facts[0]
    assert fact.period_type == "instant"
    assert fact.period_start is None
    assert fact.period_end == "2024-03-31"
    assert fact.value == Decimal("348161000000")


def test_non_usd_unit_is_preserved():
    payload = _load("companyconcept_cashflow.json")
    payload["units"] = {
        "EUR": [
            {
                "start": "2024-01-01",
                "end": "2024-03-31",
                "val": 123.45,
                "accn": "0000320193-24-000006",
                "fy": 2024,
                "fp": "Q1",
                "form": "10-Q",
                "filed": "2024-05-01",
            }
        ]
    }
    parsed = parse_sec_company_concept(
        payload,
        taxonomy="us-gaap",
        concept="NetCashProvidedByUsedInOperatingActivities",
    )
    assert parsed.facts[0].unit == "EUR"
    assert parsed.facts[0].value == Decimal("123.45")


def test_multiple_units_requires_explicit_selection():
    payload = _load("companyconcept_multiple_units.json")
    with pytest.raises(SecConceptParseError, match="ambiguous units"):
        parse_sec_company_concept(payload, taxonomy="us-gaap", concept="EarningsPerShareBasic")
    parsed = parse_sec_company_concept(
        payload,
        taxonomy="us-gaap",
        concept="EarningsPerShareBasic",
        preferred_unit="USD/shares",
    )
    assert parsed.facts[0].value == Decimal("1.53")


def test_empty_units_and_invalid_json_are_rejected():
    with pytest.raises(SecConceptParseError, match="units"):
        parse_sec_company_concept(
            _load("companyconcept_income.json") | {"units": {}},
            taxonomy="us-gaap",
            concept="Revenues",
        )
    with pytest.raises(SecConceptParseError, match="JSON object"):
        parse_sec_company_concept([], taxonomy="us-gaap", concept="Revenues")


def test_adapter_maps_raw_response_and_acquires_data_asset(tmp_path):
    from langgraph_langchain.runtime.context import AnalysisRuntime

    raw = _load("companyconcept_amendment.json")
    adapter = SecCompanyFactsAdapter(transport=lambda url, params: raw)
    result = adapter.fetch(_request())
    assert result.quality_report.is_usable
    assert len(result.dataframe) == 2
    original = result.dataframe.iloc[0]
    amendment = result.dataframe.iloc[1]
    assert original["accession_number"] == "0000320193-24-000001"
    assert amendment["source_form"] == "10-Q/A"
    assert amendment["source_response_hash"] == result.dataframe.iloc[0]["source_response_hash"]

    runtime = AnalysisRuntime(
        workspace_dir=tmp_path / "workspace",
        session_id="sec-raw-acq",
        question="load SEC raw concept fixture",
    )
    registry = AdapterRegistry()
    registry.register(adapter)
    service = AcquisitionService(runtime=runtime, registry=registry, history_dir=tmp_path / "history")
    outcome = service.acquire(_request())
    assert outcome.asset.source_metadata["source_id"] == "financial_sec_company_facts"
    assert outcome.asset.source_metadata["snapshot_reference"]["snapshot_id"]
    assert outcome.asset.source_metadata["snapshot_reference"]["row_count"] == 2


def test_http_failure_is_not_hidden_by_parser():
    def failing_transport(url, params):
        raise TimeoutError("SEC timeout")

    adapter = SecCompanyFactsAdapter(transport=failing_transport)
    with pytest.raises(Exception):
        adapter.fetch(_request())

def test_missing_required_fact_fields_are_rejected():
    payload = _load("companyconcept_income.json")
    payload["units"]["USD"][0]["accn"] = None
    with pytest.raises(SecConceptParseError, match="accn"):
        parse_sec_company_concept(
            payload,
            taxonomy="us-gaap",
            concept="Revenues",
            preferred_unit="USD",
        )


def test_invalid_start_order_and_taxonomy_are_rejected():
    payload = _load("companyconcept_income.json")
    payload["units"]["USD"][0]["start"] = "2024-04-01"
    with pytest.raises(SecConceptParseError, match="after end"):
        parse_sec_company_concept(
            payload,
            taxonomy="us-gaap",
            concept="Revenues",
            preferred_unit="USD",
        )
    payload = _load("companyconcept_income.json")
    payload["taxonomy"] = "ifrs-full"
    with pytest.raises(SecConceptParseError, match="taxonomy"):
        parse_sec_company_concept(
            payload,
            taxonomy="us-gaap",
            concept="Revenues",
            preferred_unit="USD",
        )


def test_duplicate_and_amendment_facts_are_not_collapsed():
    payload = _load("companyconcept_amendment.json")
    payload["units"]["USD"].append(payload["units"]["USD"][0])
    parsed = parse_sec_company_concept(
        payload,
        taxonomy="us-gaap",
        concept="Revenues",
        preferred_unit="USD",
    )
    assert len(parsed.facts) == 3
    assert len({fact.source_document_id for fact in parsed.facts}) == 2
