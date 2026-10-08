"""Executable financial tool registry for Runtime V10.2.

The static ``FinancialToolSpec`` catalog describes what a tool can do.  This
module binds a spec to a deterministic Python callable and executes it with a
stable request/result contract.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .execution_models import (
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutionStatus,
)
from .models import FinancialToolSpec


ToolExecutor = Callable[[dict[str, Any]], Any]


class FinancialToolRegistryError(ValueError):
    """Raised when an executable tool registry is configured incorrectly."""


class UnknownFinancialToolError(KeyError):
    """Raised when a tool ID has no executable binding."""


@dataclass(frozen=True)
class ExecutableFinancialTool:
    """A tool spec bound to its runtime executor."""

    spec: FinancialToolSpec
    executor: ToolExecutor

    def execute(
        self,
        payload: dict[str, Any] | None = None,
        *,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> ToolExecutionResult:
        request = ToolExecutionRequest(
            request_id=request_id or f"tool_request_{uuid4().hex}",
            tool_id=self.spec.tool_id,
            payload=dict(payload or {}),
            metadata=dict(metadata or {}),
        )
        started_at = datetime_now_isoformat()
        started = time.perf_counter()
        try:
            output = self.executor(dict(request.payload))
            return ToolExecutionResult(
                request_id=request.request_id,
                tool_id=self.spec.tool_id,
                status=ToolExecutionStatus.SUCCEEDED,
                output=output,
                started_at=started_at,
                finished_at=datetime_now_isoformat(),
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
                metadata=dict(request.metadata),
            )
        except Exception as exc:  # noqa: BLE001 - execution boundary
            return ToolExecutionResult(
                request_id=request.request_id,
                tool_id=self.spec.tool_id,
                status=ToolExecutionStatus.FAILED,
                output=None,
                error=f"{type(exc).__name__}: {exc}",
                started_at=started_at,
                finished_at=datetime_now_isoformat(),
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
                metadata=dict(request.metadata),
            )


class FinancialToolExecutionRegistry:
    """Registry of tool specs bound to executable callables."""

    def __init__(
        self,
        tools: Iterable[ExecutableFinancialTool] = (),
    ) -> None:
        self._tools: dict[str, ExecutableFinancialTool] = {}
        for tool in tools:
            self.register(tool)

    @classmethod
    def from_specs(
        cls,
        specs: Mapping[str, FinancialToolSpec],
        executors: Mapping[str, ToolExecutor],
    ) -> "FinancialToolExecutionRegistry":
        tools = []
        for tool_id, executor in executors.items():
            spec = specs.get(tool_id)
            if spec is None:
                raise FinancialToolRegistryError(
                    f"Cannot bind unknown tool ID: {tool_id}"
                )
            tools.append(ExecutableFinancialTool(spec=spec, executor=executor))
        return cls(tools)

    def register(self, tool: ExecutableFinancialTool) -> None:
        if not isinstance(tool, ExecutableFinancialTool):
            raise FinancialToolRegistryError(
                "Tool must be an ExecutableFinancialTool."
            )
        if not isinstance(tool.spec, FinancialToolSpec):
            raise FinancialToolRegistryError(
                f"Tool {tool.spec} must contain a FinancialToolSpec."
            )
        if not callable(tool.executor):
            raise FinancialToolRegistryError(
                f"Tool {tool.spec.tool_id} executor must be callable."
            )
        if tool.spec.tool_id != tool.spec.tool_id.strip() or not tool.spec.tool_id:
            raise FinancialToolRegistryError("Tool ID cannot be blank.")
        if tool.spec.tool_id in self._tools:
            raise FinancialToolRegistryError(
                f"Duplicate executable tool ID: {tool.spec.tool_id}"
            )
        self._tools[tool.spec.tool_id] = tool

    def get(self, tool_id: str) -> ExecutableFinancialTool:
        try:
            return self._tools[tool_id]
        except KeyError as exc:
            raise UnknownFinancialToolError(tool_id) from exc

    def list_tools(self) -> list[ExecutableFinancialTool]:
        return [self._tools[tool_id] for tool_id in sorted(self._tools)]

    def execute(
        self,
        tool_id: str,
        payload: dict[str, Any] | None = None,
        *,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> ToolExecutionResult:
        return self.get(tool_id).execute(
            payload,
            metadata=metadata,
            request_id=request_id,
        )


def datetime_now_isoformat() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
