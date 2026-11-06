"""Evidence generation guards for Runtime V2."""

from .adapters import (
    canonical_from_legacy_evidence_item,
    canonical_from_operation_evidence,
    canonical_from_sqlite_query_evidence,
)
from .collector import EvidenceCollectionError, EvidenceCollector
from .models import CanonicalEvidence
from .registry import EvidenceRegistry, EvidenceRegistryError
from .operations import (
    OperationEvidence,
    collect_operation_evidence,
)
from .sqlite import (
    SqliteEvidenceError,
    SqliteQueryEvidence,
    collect_sqlite_query_evidence,
)

__all__ = [
    "CanonicalEvidence",
    "EvidenceRegistry",
    "EvidenceRegistryError",
    "canonical_from_legacy_evidence_item",
    "canonical_from_operation_evidence",
    "canonical_from_sqlite_query_evidence",
    "OperationEvidence",
    "collect_operation_evidence",
    "EvidenceCollectionError",
    "EvidenceCollector",
    "SqliteEvidenceError",
    "SqliteQueryEvidence",
    "collect_sqlite_query_evidence",
]
