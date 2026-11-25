"""Bounded adapter between Legacy ReAct tools and Runtime V2.

Runtime V2 already invokes the real Legacy tool objects through ``StructuredTaskExecutor``.
This adapter adds the R2.5 governance missing from that bridge: a finite call budget, an explicit
call history, and fail-closed rejection when a plan tries to explore forever.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable


class CapabilityBudgetExceededError(RuntimeError):
    """Raised when a Runtime plan exceeds the Legacy capability call budget."""


@dataclass
class ToolCallBudget:
    """Finite, auditable budget for tool invocation inside one Runtime run."""

    max_calls: int = 24
    allowed_tools: frozenset[str] | None = None
    calls: Counter[str] = field(default_factory=Counter)
    history: list[tuple[str, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.max_calls, int) or self.max_calls < 1:
            raise ValueError("max_calls must be a positive integer")
        if self.allowed_tools is not None and not isinstance(self.allowed_tools, frozenset):
            self.allowed_tools = frozenset(self.allowed_tools)

    def consume(self, tool_name: str) -> int:
        """Consume one call and return the count for that tool."""
        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            raise CapabilityBudgetExceededError(f"tool is not enabled in Runtime V2: {tool_name}")
        if sum(self.calls.values()) >= self.max_calls:
            raise CapabilityBudgetExceededError(
                f"Runtime tool budget exhausted: max_calls={self.max_calls}"
            )
        self.calls[tool_name] += 1
        self.history.append((tool_name, self.calls[tool_name]))
        return self.calls[tool_name]

    @property
    def remaining(self) -> int:
        return max(0, self.max_calls - sum(self.calls.values()))

    def snapshot(self) -> dict:
        return {
            "max_calls": self.max_calls,
            "used_calls": sum(self.calls.values()),
            "remaining_calls": self.remaining,
            "calls_by_tool": dict(self.calls),
            "history": [{"tool_name": name, "call_index": index} for name, index in self.history],
        }
