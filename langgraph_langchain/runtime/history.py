"""Read-only access and metrics for immutable analysis run history."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from langgraph_langchain.runtime.models import AnalysisRun, AnalysisTask


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class RunHistoryStore:
    """Loads archived snapshots without relying on in-memory API sessions."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()

    def _snapshot_paths(self, session_id: Optional[str] = None):
        if session_id:
            session_dir = (self.workspace_root / session_id).resolve()
            try:
                session_dir.relative_to(self.workspace_root)
            except ValueError:
                return []
            return (session_dir / ".analysis_runs").glob("run_*.json")
        return self.workspace_root.glob("*/.analysis_runs/run_*.json")

    @staticmethod
    def _load_path(path: Path) -> Optional[dict[str, Any]]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            task = AnalysisTask.model_validate(raw["task"])
            run = AnalysisRun.model_validate(raw["run"])
            if path.stem != run.run_id:
                return None
            raw["task"] = task.model_dump(mode="json")
            raw["run"] = run.model_dump(mode="json")
            RunHistoryStore._normalize_recoverable_validation_statuses(raw)
            return raw
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _normalize_recoverable_validation_statuses(snapshot: dict[str, Any]) -> None:
        """Present legacy report rejections as revisions without rewriting history."""
        for step in snapshot.get("run", {}).get("steps", []):
            error = str(step.get("error") or "").lstrip()
            if (
                step.get("status") == "failed"
                and step.get("method") == "finish_report"
                and error.startswith(("REPORT REJECTED", "[REPORT REJECTED]"))
            ):
                step["status"] = "needs_revision"

        for execution in snapshot.get("executions", []):
            error = execution.get("error") or {}
            message = str(error.get("message") or "").lstrip()
            error_type = str(error.get("type") or "")
            if (
                execution.get("status") == "failed"
                and execution.get("tool_name") == "finish_report"
                and (
                    message.startswith(("REPORT REJECTED", "[REPORT REJECTED]"))
                    or error_type == "ReportValidationError"
                )
            ):
                execution["status"] = "needs_revision"
                error["type"] = "ReportValidationFeedback"

    def list_runs(self, *, session_id: Optional[str] = None, limit: int = 50) -> list[dict]:
        snapshots = [
            snapshot
            for path in self._snapshot_paths(session_id)
            if (snapshot := self._load_path(path)) is not None
        ]
        snapshots.sort(key=lambda item: item["run"]["created_at"], reverse=True)
        return [self._summary(item) for item in snapshots[: max(1, min(limit, 200))]]

    def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        if re.fullmatch(r"run_[0-9a-f]{32}", run_id) is None:
            return None
        for path in self.workspace_root.glob(f"*/.analysis_runs/{run_id}.json"):
            snapshot = self._load_path(path)
            if snapshot is not None:
                return snapshot
        return None

    def get_artifacts(self, run_id: str) -> Optional[list[dict[str, Any]]]:
        snapshot = self.get_run(run_id)
        return None if snapshot is None else list(snapshot.get("artifacts", []))

    @staticmethod
    def _summary(snapshot: dict[str, Any]) -> dict[str, Any]:
        task = snapshot["task"]
        run = snapshot["run"]
        return {
            "run_id": run["run_id"],
            "task_id": run["task_id"],
            "session_id": task["session_id"],
            "question": task["question"],
            "status": run["status"],
            "attempt": run.get("attempt", 1),
            "parent_run_id": run.get("parent_run_id"),
            "retry_of_step_id": run.get("retry_of_step_id"),
            "step_count": len(run.get("steps", [])),
            "execution_count": len(snapshot.get("executions", [])),
            "artifact_count": len(snapshot.get("artifacts", [])),
            "created_at": run["created_at"],
            "updated_at": run["updated_at"],
        }

    def metrics(self) -> dict[str, Any]:
        snapshots = [
            snapshot
            for path in self._snapshot_paths()
            if (snapshot := self._load_path(path)) is not None
        ]
        statuses = Counter(item["run"]["status"] for item in snapshots)
        retry_runs = [item for item in snapshots if item["run"].get("parent_run_id")]
        terminal_retries = [
            item for item in retry_runs
            if item["run"]["status"] in {"completed", "failed", "cancelled"}
        ]
        steps = [step for item in snapshots for step in item["run"].get("steps", [])]
        failed_steps = [step for step in steps if step.get("status") == "failed"]
        revision_steps = [
            step for step in steps if step.get("status") == "needs_revision"
        ]
        execution_failures = Counter(
            execution.get("tool_name") or "unknown"
            for item in snapshots
            for execution in item.get("executions", [])
            if execution.get("status") == "failed"
        )
        durations = []
        for item in snapshots:
            start = _parse_timestamp(item["run"].get("created_at"))
            end = _parse_timestamp(item["run"].get("updated_at"))
            if start and end:
                durations.append(max(0.0, (end - start).total_seconds()))
        return {
            "total_runs": len(snapshots),
            "runs_by_status": dict(statuses),
            "completed_runs": statuses["completed"],
            "failed_runs": statuses["failed"],
            "cancelled_runs": statuses["cancelled"],
            "running_runs": statuses["running"],
            "retry_runs": len(retry_runs),
            "successful_retry_runs": sum(
                item["run"]["status"] == "completed" for item in terminal_retries
            ),
            "retry_success_rate": round(
                sum(item["run"]["status"] == "completed" for item in terminal_retries)
                / len(terminal_retries),
                3,
            ) if terminal_retries else 0.0,
            "total_steps": len(steps),
            "failed_steps": len(failed_steps),
            "revision_steps": len(revision_steps),
            "failure_counts_by_method": dict(Counter(
                step.get("method") or "unknown" for step in failed_steps
            )),
            "revision_counts_by_method": dict(Counter(
                step.get("method") or "unknown" for step in revision_steps
            )),
            "failure_counts_by_tool": dict(execution_failures),
            "average_run_duration_seconds": round(
                sum(durations) / len(durations), 3
            ) if durations else 0.0,
        }
