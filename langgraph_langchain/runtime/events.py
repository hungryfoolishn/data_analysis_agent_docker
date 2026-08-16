"""Backward-compatible structured metadata for streamed analysis chunks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


class RuntimeStreamContextReader:
    """Refresh stream identifiers only when persisted runtime state changes."""

    def __init__(self, workspace_dir: Path) -> None:
        self.state_path = workspace_dir / ".analysis_runtime.json"
        self._state_signature: Optional[tuple[int, int, int]] = None
        self._context: dict[str, Any] = {
            "task_id": None,
            "run_id": None,
            "step_id": None,
            "run_status": None,
            "plan_status": None,
            "plan_version": None,
            "step": None,
        }

    def read(self) -> dict[str, Any]:
        try:
            stat = self.state_path.stat()
            signature = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
            if signature == self._state_signature:
                return dict(self._context)
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            task = raw.get("task", {})
            semantic_context = task.get("external_context") or {}
            run = raw.get("run", {})
            steps = run.get("steps", []) or []
            current_step_id = run.get("current_step_id")
            selected_step = next(
                (step for step in steps if step.get("step_id") == current_step_id),
                steps[-1] if steps else None,
            )
            self._context = {
                "task_id": task.get("task_id"),
                "run_id": run.get("run_id"),
                "step_id": current_step_id or (
                    selected_step.get("step_id") if selected_step else None
                ),
                "run_status": run.get("status"),
                "plan_status": run.get("plan_status", "active"),
                "plan_version": run.get("plan_version", 1),
                "step": selected_step,
                "semantic_provider": semantic_context.get("provider"),
                "semantic_context_version": semantic_context.get("context_version"),
            }
            self._state_signature = signature
        except (OSError, json.JSONDecodeError, AttributeError, TypeError):
            pass
        return dict(self._context)


def load_stream_context(workspace_dir: Path) -> dict[str, Any]:
    """Load stable task/run identifiers from a runtime state file."""
    return RuntimeStreamContextReader(workspace_dir).read()


def build_analysis_event(
    *,
    session_id: str,
    stream_context: Optional[dict[str, Any]] = None,
    text: str = "",
    artifacts: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Build additive SSE metadata without changing OpenAI-compatible fields."""
    items = artifacts or []
    artifact_ids = [item["artifact_id"] for item in items if item.get("artifact_id")]
    execution_ids = list(
        dict.fromkeys(item["execution_id"] for item in items if item.get("execution_id"))
    )
    step_ids = list(dict.fromkeys(item["step_id"] for item in items if item.get("step_id")))
    context = stream_context or {}
    return {
        "type": "artifacts_created" if items else "content_delta",
        "session_id": session_id,
        "task_id": context.get("task_id"),
        "run_id": context.get("run_id"),
        "step_id": context.get("step_id"),
        "run_status": context.get("run_status"),
        "plan_status": context.get("plan_status"),
        "plan_version": context.get("plan_version"),
        "step": context.get("step"),
        "semantic_provider": context.get("semantic_provider"),
        "semantic_context_version": context.get("semantic_context_version"),
        "step_ids": step_ids,
        "execution_ids": execution_ids,
        "artifact_ids": artifact_ids,
        "has_content": bool(text),
    }
