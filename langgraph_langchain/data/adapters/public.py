"""First public data source adapters.

CI tests inject a transport callable; no external service call is required.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

import pandas as pd

import json

from langgraph_langchain.data.adapters.base import (
    AdapterDescriptor,
    AdapterRequestError,
    AdapterTemporaryError,
    DatasetSpec,
    FetchRequest,
    HttpJsonAdapter,
)


def _require(mapping: Mapping[str, Any], name: str) -> Any:
    value = mapping.get(name)
    if value is None or value == "":
        raise AdapterRequestError(f"Missing required parameter: {name}")
    return value


class FREDAdapter(HttpJsonAdapter):
    descriptor = AdapterDescriptor(
        adapter_id="fred",
        version="1.0.0",
        source_type="fred",
        description="Federal Reserve Economic Data observations.",
        requires_credentials=True,
    )
    datasets = {
        "fred_gdp": DatasetSpec(
            dataset_id="fred_gdp",
            name="US Real GDP",
            description="Gross domestic product observations.",
            unit="billions_of_chained_2017_dollars",
            frequency="quarterly",
            source_url="https://fred.stlouisfed.org/series/GDP",
            required_columns=("date", "value", "series_id"),
            time_column="date",
            update_policy="quarterly_release",
        ),
        "fred_cpi": DatasetSpec(
            dataset_id="fred_cpi",
            name="US CPI Urban Consumers",
            description="Consumer price index observations.",
            unit="index_1982_1984_100",
            frequency="monthly",
            source_url="https://fred.stlouisfed.org/series/CPIAUCSL",
            required_columns=("date", "value", "series_id"),
            time_column="date",
            update_policy="monthly_release",
        ),
    }

    def _fetch_raw(self, request: FetchRequest, spec: DatasetSpec) -> Any:
        api_key = _require(self._credentials(), "api_key")
        series_id = request.parameters.get("series_id") or self._default_series(spec.dataset_id)
        params = {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
        }
        if request.start:
            params["observation_start"] = request.start
        if request.end:
            params["observation_end"] = request.end
        return self._transport(
            "https://api.stlouisfed.org/fred/series/observations",
            params,
        )

    def _credentials(self) -> Mapping[str, Any]:
        from langgraph_langchain.config import DEEPSEEK_API_KEY  # placeholder if dedicated key absent
        import os

        return {"api_key": os.environ.get("FRED_API_KEY", "")}

    def _default_series(self, dataset_id: str) -> str:
        return "GDP" if dataset_id == "fred_gdp" else "CPIAUCSL"

    def normalize(self, raw: Any, request: FetchRequest, spec: DatasetSpec) -> pd.DataFrame:
        observations = raw.get("observations") if isinstance(raw, dict) else []
        rows = [
            {
                "date": item.get("date"),
                "value": item.get("value"),
                "series_id": item.get("series_id"),
            }
            for item in observations
            if isinstance(item, dict)
        ]
        frame = pd.DataFrame(rows, columns=["date", "value", "series_id"])
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        return frame.dropna(subset=["date"]).reset_index(drop=True)


class BLSAdapter(HttpJsonAdapter):
    descriptor = AdapterDescriptor(
        adapter_id="bls",
        version="1.0.0",
        source_type="bls",
        description="Bureau of Labor Statistics time series.",
        requires_credentials=False,
    )
    datasets = {
        "bls_employment": DatasetSpec(
            dataset_id="bls_employment",
            name="US Nonfarm Payroll Employment",
            description="Total nonfarm employment.",
            unit="thousands_of_persons",
            frequency="monthly",
            source_url="https://www.bls.gov/ces/",
            required_columns=("period", "value", "series_id"),
            time_column="period",
            update_policy="monthly_release",
        ),
        "bls_cpi": DatasetSpec(
            dataset_id="bls_cpi",
            name="US CPI All Urban Consumers",
            description="Consumer price index, not seasonally adjusted.",
            unit="index_1982_1984_100",
            frequency="monthly",
            source_url="https://www.bls.gov/cpi/",
            required_columns=("period", "value", "series_id"),
            time_column="period",
            update_policy="monthly_release",
        ),
    }

    def _fetch_raw(self, request: FetchRequest, spec: DatasetSpec) -> Any:
        series_id = request.parameters.get("series_id") or self._default_series(spec.dataset_id)
        params: dict[str, Any] = {}
        if request.start:
            params["startyear"] = request.start[:4]
        if request.end:
            params["endyear"] = request.end[:4]
        return self._transport(
            f"https://api.bls.gov/public/v2/timeseries/data/{series_id}",
            params,
        )

    def _default_series(self, dataset_id: str) -> str:
        return "CES0000000001" if dataset_id == "bls_employment" else "CUUR0000SA0"

    def normalize(self, raw: Any, request: FetchRequest, spec: DatasetSpec) -> pd.DataFrame:
        series = raw.get("Results", {}).get("series", []) if isinstance(raw, dict) else []
        rows: list[dict[str, Any]] = []
        for item in series:
            for observation in item.get("data", []):
                rows.append(
                    {
                        "period": observation.get("period"),
                        "value": observation.get("value"),
                        "series_id": item.get("seriesID"),
                    }
                )
        frame = pd.DataFrame(rows, columns=["period", "value", "series_id"])
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        return frame.dropna(subset=["period"]).reset_index(drop=True)


class BEAAdapter(HttpJsonAdapter):
    descriptor = AdapterDescriptor(
        adapter_id="bea",
        version="1.0.0",
        source_type="bea",
        description="Bureau of Economic Analysis NIPA data.",
        requires_credentials=True,
    )
    datasets = {
        "bea_gdp": DatasetSpec(
            dataset_id="bea_gdp",
            name="US GDP by NIPA table",
            description="Gross domestic product annual series.",
            unit="billions_of_current_dollars",
            frequency="annual",
            source_url="https://apps.bea.gov/api/",
            required_columns=("period", "value", "line_description"),
            time_column="period",
            update_policy="periodic_release",
        ),
        "bea_personal_income": DatasetSpec(
            dataset_id="bea_personal_income",
            name="US Personal Income by NIPA table",
            description="Personal income annual series.",
            unit="billions_of_current_dollars",
            frequency="annual",
            source_url="https://apps.bea.gov/api/",
            required_columns=("period", "value", "line_description"),
            time_column="period",
            update_policy="periodic_release",
        ),
    }

    def _fetch_raw(self, request: FetchRequest, spec: DatasetSpec) -> Any:
        import os

        user_id = os.environ.get("BEA_API_KEY", "")
        table_name = self._table_name(spec.dataset_id)
        line_description = self._line_description(spec.dataset_id)
        params = {
            "UserID": user_id,
            "method": "GetData",
            "DataSetName": "NIPA",
            "TableName": table_name,
            "LineDescription": line_description,
            "Frequency": "A",
            "Year": request.parameters.get("year", "ALL"),
            "ResultFormat": "JSON",
        }
        return self._transport("https://apps.bea.gov/api/data", params)

    def _table_name(self, dataset_id: str) -> str:
        return "T10101" if dataset_id == "bea_gdp" else "T20100"

    def _line_description(self, dataset_id: str) -> str:
        return (
            "Gross domestic product"
            if dataset_id == "bea_gdp"
            else "Personal income"
        )

    def normalize(self, raw: Any, request: FetchRequest, spec: DatasetSpec) -> pd.DataFrame:
        data = raw.get("BEAAPI", {}).get("Results", {}).get("Data", []) if isinstance(raw, dict) else []
        if not data and isinstance(raw, dict):
            data = raw.get("BEAAPI", {}).get("Error", [])
        rows = [
            {
                "period": item.get("TimePeriod"),
                "value": item.get("DataValue"),
                "line_description": item.get("LineDescription"),
            }
            for item in data
            if isinstance(item, dict) and item.get("TimePeriod") is not None
        ]
        frame = pd.DataFrame(rows, columns=["period", "value", "line_description"])
        frame["value"] = pd.to_numeric(
            frame["value"].astype(str).str.replace(",", "", regex=False),
            errors="coerce",
        )
        return frame.dropna(subset=["period"]).reset_index(drop=True)


class SECAdapter(HttpJsonAdapter):
    descriptor = AdapterDescriptor(
        adapter_id="sec_edgar",
        version="1.0.0",
        source_type="sec_edgar",
        description="SEC EDGAR company XBRL concepts.",
        requires_credentials=False,
    )
    datasets = {
        "sec_company_revenues": DatasetSpec(
            dataset_id="sec_company_revenues",
            name="Company Revenues",
            description="us-gaap Revenues facts.",
            unit="USD",
            frequency="filing_event",
            source_url="https://www.sec.gov/edgar/",
            required_columns=("start", "end", "value", "form"),
            time_column="end",
            update_policy="filing_event",
        ),
        "sec_company_net_income": DatasetSpec(
            dataset_id="sec_company_net_income",
            name="Company Net Income",
            description="us-gaap NetIncomeLoss facts.",
            unit="USD",
            frequency="filing_event",
            source_url="https://www.sec.gov/edgar/",
            required_columns=("start", "end", "value", "form"),
            time_column="end",
            update_policy="filing_event",
        ),
    }

    def _fetch_raw(self, request: FetchRequest, spec: DatasetSpec) -> Any:
        cik = _require(request.parameters, "cik")
        digits = re.sub(r"\D", "", str(cik))
        if not digits:
            raise AdapterRequestError("cik must contain digits")
        concept = (
            "Revenues"
            if spec.dataset_id == "sec_company_revenues"
            else "NetIncomeLoss"
        )
        return self._transport(
            f"https://data.sec.gov/api/xbrl/companyconcept/CIK{digits.zfill(10)}/us-gaap/{concept}.json",
            {},
        )

    def normalize(self, raw: Any, request: FetchRequest, spec: DatasetSpec) -> pd.DataFrame:
        units = raw.get("units", {}).get("USD", []) if isinstance(raw, dict) else []
        rows = [
            {
                "start": item.get("start"),
                "end": item.get("end"),
                "value": item.get("val"),
                "form": item.get("form"),
                "accession_number": item.get("accn"),
            }
            for item in units
            if isinstance(item, dict)
        ]
        frame = pd.DataFrame(
            rows,
            columns=["start", "end", "value", "form", "accession_number"],
        )
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        frame["end"] = pd.to_datetime(frame["end"], errors="coerce")
        return frame.dropna(subset=["end"]).reset_index(drop=True)


def default_adapter_registry():
    from langgraph_langchain.data.adapters.registry import AdapterRegistry

    registry = AdapterRegistry()
    registry.register(FREDAdapter())
    registry.register(BLSAdapter())
    registry.register(BEAAdapter())
    registry.register(SECAdapter())
    registry.register(NBSAdapter())
    return registry


class NBSAdapter(HttpJsonAdapter):
    """China National Bureau of Statistics adapter (B8.5).

    Uses the public easyquery JSON endpoint of data.stats.gov.cn.  Datasets
    are annual national-series aggregates; the transport is injectable for
    offline contract tests, and real network calls require no credentials.
    """

    descriptor = AdapterDescriptor(
        adapter_id="nbs",
        version="1.0.0",
        source_type="nbs",
        description="China National Bureau of Statistics annual series.",
        requires_credentials=False,
        rate_limit_seconds=1.0,
    )

    datasets = {
        "nbs_gdp": DatasetSpec(
            dataset_id="nbs_gdp",
            name="China gross domestic product (annual)",
            description="Annual GDP at current prices, national total.",
            unit="100_million_cny",
            frequency="annual",
            source_url="https://data.stats.gov.cn/easyquery.htm?cn=E0103",
            required_columns=("period", "value"),
            time_column="period",
            update_policy="annual_release",
        ),
        "nbs_cpi": DatasetSpec(
            dataset_id="nbs_cpi",
            name="China consumer price index (annual, same period last year = 100)",
            description="Annual CPI, national total, same-period-last-year base.",
            unit="same_period_last_year_100",
            frequency="annual",
            source_url="https://data.stats.gov.cn/easyquery.htm?cn=E0103",
            required_columns=("period", "value"),
            time_column="period",
            update_policy="annual_release",
        ),
    }

    _ZB_CODES = {"nbs_gdp": "A010101", "nbs_cpi": "A01030H"}

    def _fetch_raw(self, request: FetchRequest, spec: DatasetSpec) -> Any:
        start = request.start or "2015"
        end = request.end or "2026"
        value_code = f"{str(start)[:4]}-{str(end)[:4]}"
        params = {
            "m": "QueryData",
            "dbcode": "hgnd",
            "rowcode": "zb",
            "colcode": "sj",
            "wds": "[]",
            "dfwds": json.dumps(
                [
                    {"wdcode": "zb", "valuecode": self._ZB_CODES.get(spec.dataset_id, "")},
                    {"wdcode": "sj", "valuecode": value_code},
                ],
                ensure_ascii=False,
            ),
        }
        return self._transport("https://data.stats.gov.cn/easyquery.htm", params)

    def normalize(self, raw: Any, request: FetchRequest, spec: DatasetSpec) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        if isinstance(raw, dict):
            returncode = raw.get("returncode")
            if returncode is not None and returncode != 200:
                raise AdapterTemporaryError(
                    f"NBS easyquery returned returncode {returncode}"
                )
            for ret in raw.get("ret", []) or []:
                for node in ret.get("datanodes", []) or []:
                    data = node.get("data") or {}
                    period = data.get("sj")
                    value = data.get("data")
                    if period is None or value in (None, ""):
                        continue
                    try:
                        numeric = float(value)
                    except (TypeError, ValueError):
                        continue
                    rows.append(
                        {
                            "period": str(period),
                            "value": numeric,
                            "indicator": data.get("code", ""),
                        }
                    )
        frame = pd.DataFrame(rows, columns=["period", "value", "indicator"])
        return frame.drop_duplicates(subset=["period"]).sort_values("period").reset_index(drop=True)
