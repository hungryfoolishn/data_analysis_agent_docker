"""Runtime execution outcome contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class RuntimeOutcome:
    """Final result derived from Runtime execution, not SSE completion."""

    run_id: str
    status: str
    runtime_mode: str
    report_id: str | None
    completed_steps: int
    failed_steps: int
    evidence_ids: tuple[str, ...]
    error_code: str | None = None
    fallback_used: bool = False


def derive_runtime_status(tasks: Iterable[object]) -> str:
    """Map task states to a single terminal Runtime status."""
    statuses = [getattr(task, "status", None) for task in tasks]
    completed = sum(status == "succeeded" for status in statuses)
    failed = sum(status == "failed" for status in statuses)
    skipped = sum(status == "skipped" for status in statuses)
    cancelled = any(status == "cancelled" for status in statuses)

    if cancelled:
        return "cancelled"
    if failed and completed:
        return "partial_success"
    if failed:
        return "failed"
    if completed and skipped:
        return "partial_success"
    if completed:
        return "completed"
    return "failed"