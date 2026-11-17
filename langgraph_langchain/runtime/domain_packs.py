"""Domain Pack registry (B10.2.0).

Platform-neutral, explicit-registration infrastructure for domain packs
(Financial, Macro, …).  Design rules (B10.1 architecture §3, B10.2 task §4):

- explicit registration only — no auto-discovery, no dynamic code loading;
- importing this module has NO side effects and registers nothing;
- the registry knows nothing about any specific domain: pack semantics live
  inside each pack;
- duplicate registration of the SAME contract is idempotent; the same
  ``pack_id`` with different content is an explicit error;
- invalid contracts are rejected at registration time.

This module is platform infrastructure: it must never import any domain pack
(Financial or Macro) and no frozen Macro code depends on it.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "DomainPackContract",
    "DomainPackRegistry",
    "DomainPackRegistrationError",
]

_PACK_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


class DomainPackRegistrationError(ValueError):
    """Raised when a pack contract cannot be registered."""


class DomainPackContract(BaseModel):
    """Declarative identity/capability/boundary contract for one domain pack.

    ``model_contract`` / ``normalization_contract`` are pack-defined
    descriptor mappings (schema summary, authority rules, versions) — the
    registry treats them as opaque lineage metadata and never interprets
    them.  ``dependency_boundary`` lists import roots the pack forbids
    itself from importing (e.g. another pack's internal modules); the
    registry stores the declaration and packs' own tests enforce it.
    """

    model_config = {"frozen": True}

    pack_id: str
    pack_version: str
    domain: str
    supported_capabilities: tuple[str, ...]
    model_contract: Mapping[str, Any] = Field(default_factory=dict)
    normalization_contract: Mapping[str, Any] = Field(default_factory=dict)
    dependency_boundary: tuple[str, ...] = Field(default_factory=tuple)
    registration_identity: str = ""

    @field_validator("pack_id")
    @classmethod
    def _pack_id(cls, value: str) -> str:
        if not _PACK_ID_PATTERN.fullmatch(value):
            raise ValueError(
                f"pack_id must match {_PACK_ID_PATTERN.pattern}, got {value!r}"
            )
        return value

    @field_validator("pack_version")
    @classmethod
    def _pack_version(cls, value: str) -> str:
        if not _SEMVER_PATTERN.fullmatch(value):
            raise ValueError(
                f"pack_version must be semantic (X.Y.Z), got {value!r}"
            )
        return value

    @field_validator("domain")
    @classmethod
    def _domain(cls, value: str) -> str:
        if not value or value != value.strip().lower():
            raise ValueError(f"domain must be a non-empty lowercase token: {value!r}")
        return value

    @field_validator("supported_capabilities")
    @classmethod
    def _capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("supported_capabilities must not be empty")
        for capability in value:
            if not capability or capability != capability.strip():
                raise ValueError(
                    f"capability must be a non-unpadded token: {capability!r}"
                )
        return value

    @property
    def registration_key(self) -> str:
        return self.registration_identity or self.pack_id


class DomainPackRegistry:
    """Explicit registry for domain pack contracts."""

    def __init__(self) -> None:
        self._contracts: dict[str, DomainPackContract] = {}

    def register(self, contract: DomainPackContract) -> DomainPackContract:
        """Register one contract.

        - invalid contract → ``DomainPackRegistrationError`` (pydantic
          validation errors are wrapped);
        - same key + identical content → idempotent (returns existing);
        - same key + different content → explicit error (never overwrite).
        """
        if not isinstance(contract, DomainPackContract):
            raise DomainPackRegistrationError(
                "Only DomainPackContract instances can be registered, got "
                f"{type(contract).__name__}"
            )
        key = contract.registration_key
        existing = self._contracts.get(key)
        if existing is not None:
            if existing == contract:
                return existing
            raise DomainPackRegistrationError(
                f"Pack registration conflict for {key!r}: existing="
                f"{existing.pack_version}, new={contract.pack_version} with "
                "different content; bump pack_version to register a new pack"
            )
        self._contracts[key] = contract
        return contract

    def get(self, pack_id: str) -> DomainPackContract:
        contract = self._contracts.get(pack_id)
        if contract is None:
            raise KeyError(f"Domain pack not registered: {pack_id!r}")
        return contract

    def has(self, pack_id: str) -> bool:
        return pack_id in self._contracts

    def list_contracts(self) -> list[DomainPackContract]:
        return sorted(self._contracts.values(), key=lambda item: item.pack_id)

    def unregister(self, pack_id: str) -> None:
        """Test/admin helper — explicit removal, never implicit."""
        self._contracts.pop(pack_id, None)

    def __len__(self) -> int:
        return len(self._contracts)
