"""Domain-neutral models consumed from WrenAI or another semantic layer."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from langgraph_langchain.runtime.models import utc_now


class SemanticModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class SemanticMetric(SemanticModel):
    id: str
    name: str
    display_name: str
    description: str = ""
    unit: str = "number"
    grain_entity: Optional[str] = None
    allowed_dimensions: list[str] = Field(default_factory=list)
    default_filters: list[dict[str, Any]] = Field(default_factory=list)
    additivity: str = "non_additive"
    execution_ref: Optional[dict[str, Any]] = None
    version: str = ""


class SemanticDimension(SemanticModel):
    id: str
    display_name: str
    entity_id: Optional[str] = None
    source_field: Optional[str] = None
    data_type: str = "string"
    semantic_role: str = "dimension"
    description: str = ""
    synonyms: list[str] = Field(default_factory=list)


class SemanticEntity(SemanticModel):
    id: str
    name: str
    display_name: str
    description: str = ""
    grain: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    source_models: list[str] = Field(default_factory=list)
    version: str = ""


class SemanticRelationship(SemanticModel):
    id: str
    from_entity: str
    to_entity: str
    cardinality: str
    predicate: str = ""


class SemanticSourceAsset(SemanticModel):
    id: str
    name: str
    source_type: str
    location_ref: Optional[str] = None
    query_ref: Optional[str] = None
    schema_snapshot: dict[str, Any] = Field(
        default_factory=dict, validation_alias="schema", serialization_alias="schema"
    )


class SemanticPermissions(BaseModel):
    """Persistable policy facts only; credentials are deliberately excluded."""

    model_config = ConfigDict(extra="forbid")

    policy_version: str = ""
    allowed_data_scopes: list[str] = Field(default_factory=list)
    denied_fields: list[str] = Field(default_factory=list)
    row_filters: list[dict[str, Any]] = Field(default_factory=list)


class SemanticResolution(BaseModel):
    contract_version: str = "1.0"
    provider: str
    context_version: str
    question: str
    query: Optional[str] = None
    metric_definitions: list[SemanticMetric] = Field(default_factory=list)
    entities: list[SemanticEntity] = Field(default_factory=list)
    dimensions: list[SemanticDimension] = Field(default_factory=list)
    relationships: list[SemanticRelationship] = Field(default_factory=list)
    source_assets: list[SemanticSourceAsset] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    permissions: SemanticPermissions = Field(default_factory=SemanticPermissions)
    resolved_at: str = Field(default_factory=utc_now)

    @model_validator(mode="before")
    @classmethod
    def reject_credentials(cls, value: Any) -> Any:
        blocked_keys = {
            "api_key",
            "apikey",
            "api_token",
            "access_token",
            "authorization",
            "bearer_token",
            "password",
            "secret",
        }

        def walk(item: Any) -> None:
            if isinstance(item, dict):
                for key, nested in item.items():
                    if str(key).lower() in blocked_keys:
                        raise ValueError(f"Credentials are not allowed in semantic context: {key}")
                    walk(nested)
            elif isinstance(item, list):
                for nested in item:
                    walk(nested)

        walk(value)
        return value

    def to_external_context(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def matches_question(self, question: str) -> bool:
        return self.question.strip() == question.strip()
