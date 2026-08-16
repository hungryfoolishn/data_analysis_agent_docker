"""Provider protocol plus offline implementations used before WrenAI wiring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, runtime_checkable

import yaml

from langgraph_langchain.semantic.models import SemanticResolution


@runtime_checkable
class SemanticContextProvider(Protocol):
    async def resolve_question(
        self,
        question: str,
        *,
        request_context: Optional[Mapping[str, Any]] = None,
    ) -> SemanticResolution:
        ...


class MockSemanticContextProvider:
    """Deterministic provider for contract and integration tests."""

    def __init__(self, resolutions: Mapping[str, Any], *, default: Any = None) -> None:
        self._resolutions = dict(resolutions)
        self._default = default

    async def resolve_question(
        self,
        question: str,
        *,
        request_context: Optional[Mapping[str, Any]] = None,
    ) -> SemanticResolution:
        raw = self._resolutions.get(question, self._default)
        if raw is None:
            raise LookupError(f"No semantic resolution configured for question: {question}")
        resolution = raw if isinstance(raw, SemanticResolution) else SemanticResolution.model_validate(raw)
        return resolution.model_copy(update={"question": question})


class LocalFileSemanticContextProvider:
    """Load a configured JSON/YAML resolution without coupling to its author."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    async def resolve_question(
        self,
        question: str,
        *,
        request_context: Optional[Mapping[str, Any]] = None,
    ) -> SemanticResolution:
        raw = self._read()
        if "resolutions" in raw:
            raw = raw["resolutions"].get(question, raw.get("default"))
            if raw is None:
                raise LookupError(f"No semantic resolution configured for question: {question}")
        resolution = SemanticResolution.model_validate(raw)
        return resolution.model_copy(update={"question": question})

    def _read(self) -> dict[str, Any]:
        if self.path.suffix.lower() in {".yaml", ".yml"}:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        elif self.path.suffix.lower() == ".json":
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            raise ValueError("Semantic context file must be JSON or YAML")
        if not isinstance(raw, dict):
            raise ValueError("Semantic context file must contain an object")
        return raw
