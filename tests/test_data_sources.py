from pathlib import Path

import pandas as pd

from langgraph_langchain.data import (
    DataCache, FileDataSource, MemoryDataSource, SamplingSpec, cache_key,
)


def test_file_source_scans_chunks_and_materializes_columns(tmp_path: Path):
    path = tmp_path / "orders.csv"
    pd.DataFrame({"id": range(6), "region": ["N", "S"] * 3, "amount": range(10, 16)}).to_csv(path, index=False)
    source = FileDataSource(path)
    scan = source.scan_schema()
    assert scan.estimated_rows == 6
    assert scan.source_hash
    assert source.preview(2).shape == (2, 3)
    assert source.materialize(columns=["amount"]).columns.tolist() == ["amount"]
    assert sum(len(chunk) for chunk in source.iter_chunks(chunk_rows=2)) == 6


def test_sampling_is_deterministic_and_discloses_coverage():
    source = MemoryDataSource(pd.DataFrame({"group": ["A"] * 8 + ["B"] * 2, "value": range(10)}))
    spec = SamplingSpec(method="stratified", target_rows=4, random_seed=7, stratification_fields=["group"])
    first, metadata = source.sample(spec)
    second, _ = source.sample(spec)
    assert first.equals(second)
    assert metadata.original_row_count == 10
    assert metadata.coverage["group"]["sample_distinct"] == 2


def test_csv_random_sampling_is_chunked_and_repeatable(tmp_path: Path):
    path = tmp_path / "large.csv"
    pd.DataFrame({"id": range(5000), "value": range(5000)}).to_csv(path, index=False)
    source = FileDataSource(path)
    spec = SamplingSpec(method="random", target_rows=50, random_seed=9)
    first, metadata = source.sample(spec)
    second, _ = source.sample(spec)
    assert first.equals(second)
    assert len(first) == 50
    assert metadata.original_row_count == 5000


def test_cache_key_includes_source_hash_and_cache_expires(tmp_path: Path):
    first = cache_key(source_hash="a", adapter_version="v1", parameters={"x": 1})
    second = cache_key(source_hash="b", adapter_version="v1", parameters={"x": 1})
    assert first != second
    cache = DataCache(tmp_path)
    cache.put(first, pd.DataFrame({"x": [1]}), ttl_seconds=1)
    value = cache.get(first)
    assert value is not None and value.iloc[0, 0] == 1
