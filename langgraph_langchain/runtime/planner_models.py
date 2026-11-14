"""Structured planner context models: PlannerInput and DataProfile (B4.2.1).

DataProfile is an *auxiliary planning context*, never a hard dependency: a
missing/stale/corrupted profile degrades planning but never invalidates a
registered Asset (frozen contract §6.4 of the B4.2.0 design).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from langgraph_langchain.data.assets import SchemaSnapshot

PROFILE_VERSION = "1"
GENERATOR_VERSION = "b4.2.1"
MAX_PROFILE_BYTES = 64 * 1024
MAX_NUMERIC_COLUMNS = 20
MAX_TOP_K = 10


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ColumnProfile(BaseModel):
    name: str
    dtype: str
    missing_rate: float
    unique_count: Optional[int] = None
    numeric_summary: Optional[dict[str, float]] = None
    top_categories: Optional[list[tuple[str, int]]] = None
    time_range: Optional[list[str]] = None


class DataProfileMeta(BaseModel):
    asset_id: str
    data_version: str
    profile_version: str = PROFILE_VERSION
    generator_version: str = GENERATOR_VERSION
    generated_at: str = Field(default_factory=_utc_now_iso)
    row_count: int
    column_count: int
    duplicate_rows: Optional[int] = None


class DataProfile(BaseModel):
    meta: DataProfileMeta
    columns: list[ColumnProfile] = Field(default_factory=list)
    candidate_keys: list[str] = Field(default_factory=list)
    truncated: bool = False
    incomplete_reasons: list[str] = Field(default_factory=list)

    def is_current_for(self, asset_id: str, data_version: str) -> bool:
        """True only when the profile matches the asset and its data version."""
        return (
            self.meta.asset_id == asset_id
            and self.meta.data_version == data_version
            and self.meta.profile_version == PROFILE_VERSION
            and self.meta.generator_version == GENERATOR_VERSION
        )


class PlannerAssetContext(BaseModel):
    asset_id: str
    schema_snapshot: SchemaSnapshot
    profile: Optional[DataProfile] = None
    profile_unavailable_reason: Optional[str] = None


class PlannerInput(BaseModel):
    question: str
    assets: list[PlannerAssetContext] = Field(min_length=1)
    session_context: dict[str, Any] = Field(default_factory=dict)
