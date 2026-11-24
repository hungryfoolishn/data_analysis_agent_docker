"""Unit tests for the R0a-1B product job builder and safe state transport."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from langgraph_langchain.execution.product_job import (
    JSON_STATE_NAME,
    ProductStateError,
    build_product_worker_job,
    restore_product_namespace_state,
    split_result_artifacts,
)


@pytest.fixture
def sample_df():
    return pd.DataFrame({"region": ["N", "S"], "sales": [10, 20]})


def _builder(**kwargs):
    defaults = dict(code="print('x')", workspace_dir=None)
    return build_product_worker_job(**{**kwargs})


class TestBuildProductWorkerJob:
    def test_dataframe_staged_as_parquet_state(self, sample_df, tmp_path):
        prepared = build_product_worker_job(
            code="print(df)",
            workspace_dir=str(tmp_path),
            namespace={"df": sample_df, "other_text": "hello"},
        )
        names = [ref.relative_path for ref in prepared.job.input_artifacts]
        assert "state__df__df.parquet" in names
        staged = tmp_path / ".analysis_state" / "state__df__df.parquet"
        assert staged.is_file()
        restored = pd.read_parquet(staged)
        assert restored.equals(sample_df)
        # JSON-safe scalars travel in the portable namespace
        assert prepared.job.namespace == {"other_text": "hello"}
        prepared.cleanup_staging()
        assert not staged.exists()

    def test_ndarray_staged_as_npy_state(self, tmp_path):
        arr = np.array([1.0, 2.0, 3.0])
        prepared = build_product_worker_job(
            code="print(arr)", workspace_dir=str(tmp_path), namespace={"arr": arr}
        )
        names = [ref.relative_path for ref in prepared.job.input_artifacts]
        assert "state__np__arr.npy" in names
        prepared.cleanup_staging()

    def test_helpers_modules_and_callables_are_not_transportable(self, sample_df, tmp_path):
        import pandas as pd_module

        def helper():
            return 1

        prepared = build_product_worker_job(
            code="print(x)",
            workspace_dir=str(tmp_path),
            namespace={
                "df": sample_df,
                "pd": pd_module,
                "helper": helper,
                "Path": Path,
                "WORKSPACE_DIR": str(tmp_path),
                "SOURCE_PATH": "",
            },
        )
        assert prepared.job.namespace == {}
        # only df state is staged
        assert [ref.relative_path for ref in prepared.job.input_artifacts] == [
            "state__df__df.parquet"
        ]
        prepared.cleanup_staging()

    def test_json_namespace_includes_nested_scalars_and_converts_numpy(self, tmp_path):
        prepared = build_product_worker_job(
            code="print(x)",
            workspace_dir=str(tmp_path),
            namespace={
                "config": {"depth": 3, "labels": ["a", "b"]},
                "ratio": np.float64(0.25),
                "count": np.int64(7),
            },
        )
        assert prepared.job.namespace == {
            "config": {"depth": 3, "labels": ["a", "b"]},
            "ratio": 0.25,
            "count": 7,
        }

    def test_unsupported_object_is_skipped(self, tmp_path):
        class Custom:
            pass

        prepared = build_product_worker_job(
            code="print(x)",
            workspace_dir=str(tmp_path),
            namespace={"obj": Custom()},
        )
        assert prepared.job.namespace == {}
        assert prepared.job.input_artifacts == ()

    def test_source_file_inside_workspace_is_staged_with_hash(self, sample_df, tmp_path):
        source = tmp_path / "data.csv"
        sample_df.to_csv(source, index=False)
        prepared = build_product_worker_job(
            code="print(df)",
            workspace_dir=str(tmp_path),
            source_path=str(source),
            namespace={"df": sample_df},
        )
        by_name = {ref.relative_path: ref for ref in prepared.job.input_artifacts}
        assert "data.csv" in by_name
        ref = by_name["data.csv"]
        assert ref.size_bytes == source.stat().st_size
        assert len(ref.sha256) == 64
        prepared.cleanup_staging()

    def test_missing_workspace_fails_closed(self, tmp_path):
        with pytest.raises(ProductStateError):
            build_product_worker_job(
                code="print(x)", workspace_dir=str(tmp_path / "missing")
            )

    def test_invalid_var_names_are_skipped(self, tmp_path):
        prepared = build_product_worker_job(
            code="print(x)",
            workspace_dir=str(tmp_path),
            namespace={"not a valid name": 1, "x" * 100: 2},
        )
        assert prepared.job.namespace == {}

    def test_job_is_absolute_and_portable(self, sample_df, tmp_path):
        prepared = build_product_worker_job(
            code="print(df)", workspace_dir=str(tmp_path), namespace={"df": sample_df}
        )
        assert prepared.job.workspace_dir.startswith("/")
        assert prepared.job.code == "print(df)"
        prepared.cleanup_staging()


class TestRestoreProductNamespaceState:
    def test_roundtrip_dataframe(self, sample_df):
        buffer_path = None
        import io

        parquet_buffer = io.BytesIO()
        sample_df.to_parquet(parquet_buffer)
        namespace: dict = {}
        restored = restore_product_namespace_state(
            namespace, {"state__df__df.parquet": parquet_buffer.getvalue()}
        )
        assert restored == ["df"]
        assert namespace["df"].equals(sample_df)

    def test_roundtrip_ndarray(self):
        import io

        arr = np.array([[1, 2], [3, 4]])
        buffer = io.BytesIO()
        np.save(buffer, arr, allow_pickle=False)
        namespace: dict = {}
        restored = restore_product_namespace_state(
            namespace, {"state__np__arr.npy": buffer.getvalue()}
        )
        assert restored == ["arr"]
        assert np.array_equal(namespace["arr"], arr)

    def test_roundtrip_json_scalars(self):
        namespace: dict = {}
        payload = json.dumps({"alpha": 1, "beta": "x", "nested": {"k": [1, 2]}})
        restored = restore_product_namespace_state(
            namespace, {JSON_STATE_NAME: payload.encode("utf-8")}
        )
        assert set(restored) == {"alpha", "beta", "nested"}
        assert namespace["nested"] == {"k": [1, 2]}

    def test_unrecognized_state_artifact_fails_closed(self):
        with pytest.raises(ProductStateError):
            restore_product_namespace_state({}, {"state__weird.bin": b"..."})

    def test_invalid_json_state_name_fails_closed(self):
        with pytest.raises(ProductStateError):
            restore_product_namespace_state(
                {}, {JSON_STATE_NAME: json.dumps({"bad name!": 1}).encode()}
            )

    def test_corrupted_parquet_fails_closed(self):
        with pytest.raises(ProductStateError):
            restore_product_namespace_state({}, {"state__df__df.parquet": b"junk"})


class TestSplitResultArtifacts:
    def test_state_files_are_separated_from_user_artifacts(self):
        user, state = split_result_artifacts(
            ["chart.png", "state__df__df.parquet", "result.json"]
        )
        assert user == ["chart.png", "result.json"]
        assert state == ["state__df__df.parquet"]
