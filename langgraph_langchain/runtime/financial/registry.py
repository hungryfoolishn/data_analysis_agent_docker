"""Financial statement identity key and registry (B10.2.2).

Implements the identity design frozen in
``docs/architecture/reviews/b10.2.1-financial-identity-baseline.md`` §3:

- logical identity = 9 dimensions (market, entity_id, canonical_concept,
  statement_type, period_type, fiscal_year, fiscal_period, reporting_basis,
  currency) — canonical_concept is explicitly NOT a lone key and market/
  basis/currency are always explicit;
- version layers live OUTSIDE the key: accession/announcement id (source
  identity), content_hash-style content version (platform DataAsset carries
  the real hash at registration time), normalization version;
- registration mirrors the B9 ``IndicatorAssetRegistry`` behavior: same key
  + same source version + same content version is idempotent; same key +
  same source version + different content is an explicit conflict (never a
  silent overwrite); same key + different source version is a parallel
  restatement version (latest pointer + retained history).

The registry is per-run (created fresh per analysis run) and domain-local —
the Financial Pack defines its own identity and never imports Macro's key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "FinancialStatementKey",
    "FinancialFactEntry",
    "FinancialStatementRegistry",
    "FinancialIdentityError",
]

_KEY_MARKETS = {"us", "cn"}
_PERIODS = {"FY", "Q1", "Q2", "Q3", "Q4", "H1", "9M", "YTD", "TTM"}
_PERIOD_TYPES = {"instant", "duration"}
_STATEMENTS = {"income", "balance", "cash"}
_BASES = {"consolidated", "parent"}


class FinancialIdentityError(ValueError):
    """Raised when identity dimensions or registration rules are violated."""


@dataclass(frozen=True)
class FinancialStatementKey:
    """Logical identity of one normalized financial fact (9 dimensions)."""

    market: str
    entity_id: str
    canonical_concept: str
    statement_type: str
    period_type: str
    fiscal_year: int
    fiscal_period: str
    reporting_basis: str
    currency: str

    def __post_init__(self) -> None:
        if self.market not in _KEY_MARKETS:
            raise FinancialIdentityError(
                f"market must be one of {sorted(_KEY_MARKETS)}, got {self.market!r}"
            )
        if not self.entity_id:
            raise FinancialIdentityError("entity_id is required")
        if not self.canonical_concept:
            raise FinancialIdentityError("canonical_concept is required")
        if self.statement_type not in _STATEMENTS:
            raise FinancialIdentityError(
                f"statement_type must be one of {sorted(_STATEMENTS)}, got {self.statement_type!r}"
            )
        expected_period_type = "instant" if self.statement_type == "balance" else "duration"
        if self.period_type != expected_period_type:
            raise FinancialIdentityError(
                f"{self.statement_type} statements are {expected_period_type!r}, "
                f"got period_type {self.period_type!r}"
            )
        if self.fiscal_period not in _PERIODS:
            raise FinancialIdentityError(
                f"fiscal_period must be one of {sorted(_PERIODS)}, got {self.fiscal_period!r}"
            )
        if self.reporting_basis not in _BASES:
            raise FinancialIdentityError(
                f"reporting_basis must be one of {sorted(_BASES)}, got {self.reporting_basis!r}"
            )
        if not (len(self.currency) == 3 and self.currency.isupper() and self.currency.isalpha()):
            raise FinancialIdentityError(
                f"currency must be ISO 4217, got {self.currency!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "entity_id": self.entity_id,
            "canonical_concept": self.canonical_concept,
            "statement_type": self.statement_type,
            "period_type": self.period_type,
            "fiscal_year": self.fiscal_year,
            "fiscal_period": self.fiscal_period,
            "reporting_basis": self.reporting_basis,
            "currency": self.currency,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FinancialStatementKey":
        return cls(**payload)


@dataclass(frozen=True)
class FinancialFactEntry:
    """One registered fact: identity + source version + content version."""

    key: FinancialStatementKey
    accession_number: str          # source identity (SEC accession / 公告 ID)
    content_version: str           # content hash (or caller-supplied version)
    asset_id: str                  # platform DataAsset reference
    amendment: bool = False
    source_document_id: Optional[str] = None
    normalization_version: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def source_version(self) -> tuple[str, bool, str]:
        # The source document id is part of source identity.  Two filings can
        # share an accession label (for example a CN announcement batch) while
        # representing different source documents; they must not collide.
        return (self.accession_number, self.amendment, self.source_document_id or "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key.to_dict(),
            "accession_number": self.accession_number,
            "content_version": self.content_version,
            "asset_id": self.asset_id,
            "amendment": self.amendment,
            "source_document_id": self.source_document_id,
            "normalization_version": self.normalization_version,
            "metadata": dict(self.metadata),
        }


@dataclass
class _KeyVersions:
    entries: list[FinancialFactEntry] = field(default_factory=list)


class FinancialStatementRegistry:
    """Per-run registry of normalized financial facts (see module docstring)."""

    def __init__(self) -> None:
        self._entries: dict[FinancialStatementKey, _KeyVersions] = {}

    def register(self, entry: FinancialFactEntry) -> FinancialFactEntry:
        if not isinstance(entry, FinancialFactEntry):
            raise FinancialIdentityError(
                "Only FinancialFactEntry instances can be registered"
            )
        if not isinstance(entry.key, FinancialStatementKey):
            raise FinancialIdentityError(
                "key must be a valid FinancialStatementKey"
            )
        if not entry.asset_id or not entry.asset_id.strip():
            raise FinancialIdentityError(
                "asset_id is required; untraceable facts must not be registered"
            )
        if not entry.accession_number or not entry.accession_number.strip():
            raise FinancialIdentityError(
                "accession_number (source identity) is required; facts without "
                "a traceable source must not be registered"
            )
        if not entry.content_version or not entry.content_version.strip():
            raise FinancialIdentityError("content_version is required")
        versions = self._entries.setdefault(entry.key, _KeyVersions())
        same_source = [
            item
            for item in versions.entries
            if item.source_version == entry.source_version
        ]
        for item in same_source:
            if item.content_version == entry.content_version and item.asset_id == entry.asset_id:
                return item  # idempotent
            raise FinancialIdentityError(
                "Financial fact conflict for "
                f"{entry.key.to_dict()}: source version {entry.source_version} "
                f"already registered with content {item.content_version}, "
                f"got {entry.content_version}; declare amendment or bump the "
                "filing version instead of overwriting"
            )
        # Keep registration order.  Lexicographic accession sorting is not a
        # semantic filing order; explicit resolution APIs expose all versions.
        versions.entries.append(entry)
        return entry

    def resolve_latest(self, key: FinancialStatementKey) -> FinancialFactEntry:
        versions = self._entries.get(key)
        if not versions or not versions.entries:
            raise KeyError(f"No financial fact registered for key: {key.to_dict()}")
        return versions.entries[-1]

    def resolve_versions(self, key: FinancialStatementKey) -> list[FinancialFactEntry]:
        versions = self._entries.get(key)
        if not versions:
            return []
        return list(versions.entries)

    def __len__(self) -> int:
        return sum(len(versions.entries) for versions in self._entries.values())
