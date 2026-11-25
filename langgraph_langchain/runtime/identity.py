"""Runtime V2 identity context primitives.

Identity is established outside Runtime, propagated through Runtime, persisted with task
snapshots, and verified again on restore.  It is never taken from worker code or request bodies.
"""
from __future__ import annotations

from dataclasses import dataclass

from langgraph_langchain.security import validate_owner_id


@dataclass(frozen=True)
class RuntimeIdentityContext:
    session_id: str
    owner_id: str | None = None
    schema_version: str = "runtime_identity.v1"

    @classmethod
    def create(cls, *, session_id: str, owner_id: str | None = None) -> "RuntimeIdentityContext":
        # API session IDs are already whitelisted at the platform boundary.
        # Runtime accepts compact internal identifiers used by domain/golden
        # tests, but rejects any value that could become a path component.
        if not isinstance(session_id, str) or not session_id or len(session_id) > 128:
            raise ValueError("session_id must be a non-empty string of at most 128 characters")
        if any(ch in session_id for ch in ("/", "\\")) or session_id in {".", ".."}:
            raise ValueError("session_id must not contain path components")
        validated_owner = validate_owner_id(owner_id) if owner_id else None
        return cls(session_id=session_id, owner_id=validated_owner)

    @property
    def owned(self) -> bool:
        return self.owner_id is not None

    def worker_metadata(self) -> dict[str, str]:
        """Additive metadata carried by the WorkerJob envelope, not worker code."""
        metadata = {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
        }
        if self.owner_id:
            metadata["owner_id"] = self.owner_id
        return metadata
