"""Runtime execution outcome contracts."""

from __future__ import annotations

from dataclasses import dataclass


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
