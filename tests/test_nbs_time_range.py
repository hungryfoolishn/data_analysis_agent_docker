"""B9.8.5: NBS adapter time range tests."""

from __future__ import annotations

import pytest

from langgraph_langchain.data.adapters.public import NBSAdapter
from langgraph_langchain.data.adapters.base import FetchRequest


def test_nbs_time_range_in_dfwds():
    adapter = NBSAdapter(cache=None)
    captured = {}

    def transport(url, params):
        captured["params"] = dict(params)
        return {"returncode": 200, "ret": [{"datanodes": [
            {"data": {"code": "A010101", "sj": "2024", "data": 100}}
        ]}]}

    adapter._transport = transport
    adapter.fetch(FetchRequest(dataset_id="nbs_gdp", start="2020-01-01", end="2024-12-31"))
    import json
    dfwds = json.loads(captured["params"]["dfwds"])
    sj = next(w for w in dfwds if w["wdcode"] == "sj")
    assert sj["valuecode"] == "2020-2024"


def test_nbs_default_time_range():
    adapter = NBSAdapter(cache=None)
    captured = {}

    def transport(url, params):
        captured["params"] = dict(params)
        return {"returncode": 200, "ret": [{"datanodes": [
            {"data": {"code": "A010101", "sj": "2024", "data": 100}}
        ]}]}

    adapter._transport = transport
    adapter.fetch(FetchRequest(dataset_id="nbs_gdp"))
    import json
    dfwds = json.loads(captured["params"]["dfwds"])
    sj = next(w for w in dfwds if w["wdcode"] == "sj")
    assert sj["valuecode"] == "2015-2026"
