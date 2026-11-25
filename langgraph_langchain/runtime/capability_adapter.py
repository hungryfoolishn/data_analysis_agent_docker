"""Bounded adapter between Legacy ReAct tools and Runtime V2.

Runtime V2 already invokes the real Legacy tool objects through ``StructuredTaskExecutor``.
This adapter adds the R2.5 governance missing from that bridge: a finite call budget, an explicit
call history, and fail-closed rejection when a plan tries to explore forever.
"""
from __future__ import annotations

import threading
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
    # F-1 (final audit): atomic accounting even if a future scheduler admits
    # concurrent dispatch. Today the V2 scheduler is sequential
    # (max_running_tasks=1); the lock removes the latent race entirely.
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.max_calls, int) or self.max_calls < 1:
            raise ValueError("max_calls must be a positive integer")
        if self.allowed_tools is not None and not isinstance(self.allowed_tools, frozenset):
            self.allowed_tools = frozenset(self.allowed_tools)

    def consume(self, tool_name: str) -> int:
        """Consume one call and return the count for that tool.

        Atomic under concurrent consumers: the check-and-increment is one
        critical section, so over-admission is impossible even without the
        scheduler-level sequential guarantee.
        """
        with self._lock:
            if self.allowed_tools is not None and tool_name not in self.allowed_tools:
                raise CapabilityBudgetExceededError(
                    f"tool is not enabled in Runtime V2: {tool_name}"
                )
            if sum(self.calls.values()) >= self.max_calls:
                raise CapabilityBudgetExceededError(
                    f"Runtime tool budget exhausted: max_calls={self.max_calls}"
                )
            self.calls[tool_name] += 1
            self.history.append((tool_name, self.calls[tool_name]))
            return self.calls[tool_name]

    @property
    def remaining(self) -> int:
        with self._lock:
            return self._remaining_locked()

    def _remaining_locked(self) -> int:
        return max(0, self.max_calls - sum(self.calls.values()))

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "max_calls": self.max_calls,
                "used_calls": sum(self.calls.values()),
                "remaining_calls": self._remaining_locked(),
                "calls_by_tool": dict(self.calls),
                "history": [
                    {"tool_name": name, "call_index": index}
                    for name, index in self.history
                ],
            }
