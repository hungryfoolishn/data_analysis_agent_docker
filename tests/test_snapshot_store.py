from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from langgraph_langchain.data.assets import hash_dataframe
from langgraph_langchain.data.snapshots import (
    SnapshotFormatError,
    SnapshotIntegrityError,
    SnapshotNotFoundError,
    SnapshotReference,
    SnapshotStore,
    SnapshotValidationError,
    SnapshotWriteError,
    new_snapshot_id,
)


def _store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(tmp_path / ".data_snapshots")


def _reference(frame: pd.DataFrame, tmp_path: Path) -> tuple[SnapshotStore, SnapshotReference]:
    store = _store(tmp_path)
    return store, store.save(frame, snapshot_id=new_snapshot_id())


def test_snapshot_round_trip_preserves_common_types(tmp_path: Path):
    frame = pd.DataFrame(
        {
            "integer": [1, 2, 2],
            "float": [1.5, -2.25, 0.0],
            "text": ["a", "", None],
            "boolean": [True, False, None],
            "day": [
                pd.Timestamp("2026-01-01"),
                pd.Timestamp("2026-01-02"),
                None,
            ],
            "nullable_integer": pd.array([1, pd.NA, 3], dtype="Int64"),
        }
    )
    store, reference = _reference(frame, tmp_path)

    assert store.exists(reference) is True
    loaded = store.load(reference)

    pd.testing.assert_frame_equal(loaded, frame)
    assert reference.content_hash == hash_dataframe(frame)
    assert reference.snapshot_hash == hash_file_hash(reference, store)


def hash_file_hash(reference: SnapshotReference, store: SnapshotStore) -> str:
    from langgraph_langchain.data.assets import hash_file

    return hash_file(store.root / reference.snapshot_location)


def test_empty_dataframe_round_trip(tmp_path: Path):
    frame = pd.DataFrame(
        {
            "integer": pd.Series(dtype="int64"),
            "text": pd.Series(dtype="string"),
        }
    )
    store, reference = _reference(frame, tmp_path)

    loaded = store.load(reference)

    assert loaded.empty
    assert loaded.columns.tolist() == ["integer", "text"]
    assert reference.row_count == 0
    assert reference.column_count == 2


def test_snapshot_hash_mismatch_is_rejected(tmp_path: Path):
    frame = pd.DataFrame({"value": [1, 2]})
    store, reference = _reference(frame, tmp_path)
    tampered = reference.model_copy(update={"snapshot_hash": "0" * 64})

    with pytest.raises(SnapshotIntegrityError, match="Snapshot file hash"):
        store.load(tampered)


def test_snapshot_content_hash_mismatch_is_rejected(tmp_path: Path):
    frame = pd.DataFrame({"value": [1, 2]})
    store, reference = _reference(frame, tmp_path)
    tampered = reference.model_copy(update={"content_hash": "0" * 64})

    with pytest.raises(SnapshotIntegrityError, match="DataFrame hash"):
        store.load(tampered)


def test_missing_snapshot_is_rejected(tmp_path: Path):
    frame = pd.DataFrame({"value": [1]})
    store, reference = _reference(frame, tmp_path)
    store.delete(reference)

    assert store.exists(reference) is False
    with pytest.raises(SnapshotNotFoundError, match="does not exist"):
        store.load(reference)


def test_corrupt_snapshot_is_rejected(tmp_path: Path):
    frame = pd.DataFrame({"value": [1]})
    store, reference = _reference(frame, tmp_path)
    (store.root / reference.snapshot_location).write_bytes(b"not parquet")

    with pytest.raises(SnapshotIntegrityError):
        store.load(reference)


def test_unsupported_snapshot_format_is_rejected(tmp_path: Path):
    frame = pd.DataFrame({"value": [1]})
    store, reference = _reference(frame, tmp_path)
    unsupported = reference.model_copy(update={"snapshot_format": "pickle"})

    with pytest.raises(SnapshotFormatError, match="Unsupported snapshot format"):
        store.exists(unsupported)


def test_path_traversal_is_rejected(tmp_path: Path):
    frame = pd.DataFrame({"value": [1]})
    store, reference = _reference(frame, tmp_path)
    traversal = reference.model_copy(
        update={"snapshot_location": f"../{reference.snapshot_id}.parquet"}
    )

    with pytest.raises(SnapshotValidationError, match="SnapshotStore"):
        store.load(traversal)


def test_failed_write_cleans_temp_file(tmp_path: Path, monkeypatch):
    store = _store(tmp_path)
    frame = pd.DataFrame({"value": [1]})

    def fail_to_parquet(self, path, *args, **kwargs):
        raise OSError("simulated parquet failure")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", fail_to_parquet)
    with pytest.raises(SnapshotWriteError, match="simulated parquet failure"):
        store.save(frame, snapshot_id=new_snapshot_id())

    assert list(store.root.iterdir()) == []


def test_same_snapshot_id_is_not_overwritten(tmp_path: Path):
    store = _store(tmp_path)
    snapshot_id = new_snapshot_id()

    store.save(pd.DataFrame({"value": [1]}), snapshot_id=snapshot_id)
    with pytest.raises(SnapshotValidationError, match="already exists"):
        store.save(pd.DataFrame({"value": [2]}), snapshot_id=snapshot_id)


def test_save_rejects_wrong_content_hash(tmp_path: Path):
    store = _store(tmp_path)

    with pytest.raises(SnapshotValidationError, match="content_hash"):
        store.save(
            pd.DataFrame({"value": [1]}),
            snapshot_id=new_snapshot_id(),
            content_hash="0" * 64,
        )


def test_delete_only_removes_managed_snapshot(tmp_path: Path):
    frame = pd.DataFrame({"value": [1]})
    store, reference = _reference(frame, tmp_path)

    store.delete(reference)

    assert store.exists(reference) is False
    assert list(store.root.iterdir()) == []


def test_unsupported_object_value_fails_before_write(tmp_path: Path):
    store = _store(tmp_path)
    frame = pd.DataFrame({"object": [{"unsupported": True}]})

    with pytest.raises(TypeError, match="Unsupported DataFrame value"):
        store.save(frame, snapshot_id=new_snapshot_id())

    assert list(store.root.iterdir()) == []
