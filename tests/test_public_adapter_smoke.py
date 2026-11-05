"""Optional real-API smoke tests.

These are skipped unless explicit credentials are provided. CI must not depend
on public services; use tests/test_public_adapters.py for mocked contracts.
"""

from __future__ import annotations

import os

import pytest

from langgraph_langchain.data.adapters.base import FetchRequest
from langgraph_langchain.data.adapters.public import (
    BEAAdapter,
    BLSAdapter,
    FREDAdapter,
    SECAdapter,
)


def test_fred_real_smoke():
    if not os.environ.get("FRED_API_KEY"):
        pytest.skip("FRED_API_KEY not provided")
    result = FREDAdapter().fetch(FetchRequest(dataset_id="fred_gdp", start="2024-01-01", end="2024-12-31"))
    assert not result.dataframe.empty
    assert result.quality_report.is_usable


def test_bls_real_smoke():
    if not os.environ.get("RUN_BLS_SMOKE"):
        pytest.skip("BLS smoke not requested")
    result = BLSAdapter().fetch(FetchRequest(dataset_id="bls_employment", start="2024-01-01", end="2024-12-31"))
    assert not result.dataframe.empty
    assert result.quality_report.is_usable


def test_bea_real_smoke():
    if not os.environ.get("BEA_API_KEY"):
        pytest.skip("BEA_API_KEY not provided")
    result = BEAAdapter().fetch(FetchRequest(dataset_id="bea_gdp", parameters={"year": "ALL"}))
    assert not result.dataframe.empty
    assert result.quality_report.is_usable


def test_sec_real_smoke():
    if not os.environ.get("RUN_SEC_SMOKE"):
        pytest.skip("SEC smoke not requested")
    result = SECAdapter().fetch(
        FetchRequest(dataset_id="sec_company_revenues", parameters={"cik": "0000320193"})
    )
    assert not result.dataframe.empty
    assert result.quality_report.is_usable
