"""R0a-1C Evidence Trust Boundary regression tests.

These tests lock the evidence trust contract:

- Construction never grants trust by default.
- The EvidenceRegistry is the only trust authority for canonical evidence.
- Persistence/restore never re-grants or injects trust.
- Deterministic collectors bind ``verified`` to content hashes and do not
  accept caller-assigned trust.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from langgraph_langchain.data.assets import DataAsset, SchemaSnapshot
from langgraph_langchain.data.database import BoundedReadResult, SQLiteReadMetadata
from langgraph_langchain.evidence import (
    CanonicalEvidence,
    EvidenceRegistry,
    EvidenceRegistryError,
    collect_operation_evidence,
    collect_sqlite_query_evidence,
)
from langgraph_langchain.schemas import EvidenceItem, Finding
from langgraph_langchain.session_persistence import SessionPersistence


# ── helpers ───────────────────────────────────────────────────────────────────


def _execution(
    execution_id: str = "exec-1",
    *,
    task_id: str = "task-1",
    tool_name: str = "run_analysis_operation",
    status: str = "succeeded",
):
    return SimpleNamespace(
        execution_id=execution_id,
        task_id=task_id,
        tool_name=tool_name,
        status=status,
    )


def _canonical(
    execution_id: str = "exec-1",
    *,
    validation_status: str = "unverified",
    evidence_type: str = "analysis_operation",
) -> CanonicalEvidence:
    return CanonicalEvidence(
        evidence_type=evidence_type,
        execution_id=execution_id,
        task_id="task-1",
        tool_name="run_analysis_operation",
        input_asset_ids=["asset-in"],
        output_asset_ids=["asset-out"],
        artifact_ids=["artifact-1"],
        evidence_text="deterministic operation completed",
        numeric_values={"row_count": 2.0, "column_count": 2.0},
        validation_status=validation_status,
    )


def _registry() -> EvidenceRegistry:
    registry = EvidenceRegistry(
        assets={"asset-in": object(), "asset-out": object()},
        artifacts={"artifact-1": object()},
    )
    registry.register_execution(_execution())
    return registry


def _hash_asset(name: str = "out") -> DataAsset:
    return DataAsset(
        name=name,
        source_type="parquet",
        location=None,
        content_hash="a" * 64,
        schema_snapshot=SchemaSnapshot.from_dataframe(pd.DataFrame({"a": [1, 2], "b": [3, 4]})),
        source_metadata={"execution_id": "exec-1"},
    )


def _hashless_asset(name: str = "out") -> DataAsset:
    return _hash_asset(name).model_copy(update={"content_hash": None})


def _bounded_read(df: pd.DataFrame | None = None) -> BoundedReadResult:
    frame = df if df is not None else pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    metadata = SQLiteReadMetadata(
        database_path="/tmp/db.sqlite",
        source_identity="sqlite:sha256:" + "c" * 64,
        query_type="sql",
        table_name=None,
        query_sha256="b" * 64,
        selected_columns=tuple(frame.columns),
        returned_row_count=len(frame),
        requested_row_limit=1000,
        truncated=False,
        execution_duration_seconds=0.01,
    )
    return BoundedReadResult(dataframe=frame, metadata=metadata)


# ── creation defaults ─────────────────────────────────────────────────────────


class TestCreationDefaults:
    def test_new_canonical_evidence_is_unverified(self):
        assert _canonical().validation_status == "unverified"

    def test_new_evidence_item_is_unverified(self):
        assert EvidenceItem(evidence_text="x").verification_status == "unverified"

    def test_operation_evidence_requires_content_hash(self):
        with pytest.raises(ValueError, match="content hash"):
            collect_operation_evidence(
                execution_id="exec-1",
                operation="groupby",
                parameters={},
                input_asset_ids=["asset-in"],
                output_asset=_hashless_asset(),
                numeric_values={"row_count": 2.0, "column_count": 2.0},
            )

    def test_operation_evidence_verified_requires_registered_asset(self):
        with pytest.raises(ValueError, match="registered output asset"):
            collect_operation_evidence(
                execution_id="exec-1",
                operation="groupby",
                parameters={},
                input_asset_ids=["asset-in"],
                output_asset=DataAsset(
                    name="x",
                    source_type="parquet",
                    content_hash=None,
                    schema_snapshot=SchemaSnapshot(row_count=0, column_count=0, columns=[]),
                ),
                numeric_values={},
            )

    def test_sqlite_evidence_requires_content_hash(self):
        sqlite_asset = DataAsset.from_sqlite_query(
            dataframe=pd.DataFrame({"a": [1, 2]}),
            source_identity="sqlite:sha256:" + "c" * 64,
            query_fingerprint="b" * 64,
            query_type="sql",
        )
        with pytest.raises(Exception, match="content hash"):
            collect_sqlite_query_evidence(
                execution_id="exec-1",
                result=_bounded_read(),
                output_asset=sqlite_asset.model_copy(update={"content_hash": None}),
            )

    def test_sqlite_evidence_trust_is_not_caller_assignable(self):
        import inspect

        signature = inspect.signature(collect_sqlite_query_evidence)
        assert "validation_status" not in signature.parameters

    def test_sqlite_evidence_verified_binds_content_hash(self):
        asset = DataAsset.from_sqlite_query(
            dataframe=pd.DataFrame({"a": [1, 2]}),
            source_identity="sqlite:sha256:" + "c" * 64,
            query_fingerprint="b" * 64,
            query_type="sql",
        )
        evidence = collect_sqlite_query_evidence(
            execution_id="exec-1",
            result=_bounded_read(),
            output_asset=asset,
        )
        assert evidence.validation_status == "verified"
        assert evidence.provenance["content_hash"] == asset.content_hash


# ── registry trust authority ─────────────────────────────────────────────────


class TestRegistryTrustAuthority:
    def test_register_never_preserves_caller_verified(self):
        registry = _registry()
        evidence = registry.register(_canonical(validation_status="verified"))
        assert evidence.validation_status == "reference_validated"

    def test_restore_record_resets_verified_state(self):
        registry = _registry()
        restored = registry.restore_record(_canonical(validation_status="verified"))
        assert restored.validation_status == "unverified"

    def test_verify_without_validator_fails_closed(self):
        registry = _registry()
        evidence = registry.register(_canonical())
        with pytest.raises(EvidenceRegistryError):
            registry.verify(evidence.evidence_id)
        assert registry.get(evidence.evidence_id).validation_status == "failed"

    def test_validator_exception_never_promotes(self):
        registry = _registry()
        evidence = registry.register(_canonical())

        def boom(_evidence):
            raise RuntimeError("validator crashed")

        with pytest.raises(RuntimeError):
            registry.verify(evidence.evidence_id, content_validator=boom)
        assert registry.get(evidence.evidence_id).validation_status == "reference_validated"

    def test_validator_failures_reject_and_mark_failed(self):
        registry = _registry()
        evidence = registry.register(_canonical())

        def rejected(_evidence):
            return ["content mismatch"]

        with pytest.raises(EvidenceRegistryError, match="content mismatch"):
            registry.verify(evidence.evidence_id, content_validator=rejected)
        assert registry.get(evidence.evidence_id).validation_status == "failed"

    def test_validator_unknown_results_reject(self):
        registry = _registry()
        evidence = registry.register(_canonical())

        def unknown(_evidence):
            return ["validator returned UNKNOWN; cannot promote"]

        with pytest.raises(EvidenceRegistryError):
            registry.verify(evidence.evidence_id, content_validator=unknown)
        assert registry.get(evidence.evidence_id).validation_status == "failed"

    def test_failed_verification_requires_content_validator_again(self):
        registry = _registry()
        evidence = registry.register(_canonical())
        with pytest.raises(EvidenceRegistryError):
            registry.verify(evidence.evidence_id)
        # A failed record cannot be silently promoted without a validator.
        with pytest.raises(EvidenceRegistryError):
            registry.verify(evidence.evidence_id)


# ── persistence / restore trust boundary ─────────────────────────────────────


class TestRestoreTrustBoundary:
    @staticmethod
    def _finding(verification_status: str, verification_result_id: str | None):
        return {
            "finding_id": "F001",
            "statement": "Sales dropped 15%",
            "evidence": [
                {
                    "evidence_text": "Q3 total was 850K",
                    "verification_status": verification_status,
                    "verification_result_id": verification_result_id,
                }
            ],
            "confidence_level": "high",
            "evidence_level": "A",
        }

    def test_restored_verified_evidence_is_downgraded(self):
        findings = SessionPersistence.deserialize_findings(
            [self._finding("verified", "verif-123")]
        )
        evidence = findings[0].evidence[0]
        assert evidence.verification_status == "unverified"
        assert evidence.verification_result_id is None

    def test_tampered_state_file_cannot_inject_verified(self):
        raw = json.loads(json.dumps([self._finding("verified", "verif-xyz")]))
        raw[0]["evidence"][0]["verification_status"] = "verified"
        findings = SessionPersistence.deserialize_findings(raw)
        assert findings[0].evidence[0].verification_status == "unverified"
        assert findings[0].evidence[0].verification_result_id is None

    def test_restored_failed_evidence_is_unverified(self):
        findings = SessionPersistence.deserialize_findings(
            [self._finding("failed", "verif-9")]
        )
        assert findings[0].evidence[0].verification_status == "unverified"

    def test_restored_unverified_evidence_stays_unverified(self):
        findings = SessionPersistence.deserialize_findings(
            [self._finding("unverified", None)]
        )
        assert findings[0].evidence[0].verification_status == "unverified"

    def test_registry_canonical_restore_resets_trust(self):
        registry = _registry()
        restored = registry.restore_record(_canonical(validation_status="verified"))
        assert restored.validation_status == "unverified"
