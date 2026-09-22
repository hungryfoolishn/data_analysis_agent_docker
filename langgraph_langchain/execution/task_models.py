"""Request/result contracts for Runtime V2 task executors."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel, Field

from langgraph_langchain.schemas import AnalysisTask

if TYPE_CHECKING:
    from langgraph_langchain.runtime.models import ExecutionResult


def _parse_json_payload(value: Any) -> Any:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return value
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return value


def extract_artifact_ids(value: Any) -> list[str]:
    """Extract artifact IDs from structured tool output when available."""
    payload = _parse_json_payload(value)
    ids: list[str] = []

    if isinstance(payload, dict):
        artifact_id = payload.get("artifact_id")
        if isinstance(artifact_id, str) and artifact_id:
            ids.append(artifact_id)

        singular_artifact = payload.get("artifact")
        if isinstance(singular_artifact, dict):
            artifact_id = singular_artifact.get("artifact_id")
            if isinstance(artifact_id, str) and artifact_id:
                ids.append(artifact_id)

        raw_ids = payload.get("artifact_ids")
        if isinstance(raw_ids, list):
            ids.extend(item for item in raw_ids if isinstance(item, str) and item)

        artifacts = payload.get("artifacts")
        if isinstance(artifacts, list):
            for artifact in artifacts:
                if isinstance(artifact, str) and artifact:
                    ids.append(artifact)
                elif isinstance(artifact, dict):
                    artifact_id = artifact.get("artifact_id")
                    if isinstance(artifact_id, str) and artifact_id:
                        ids.append(artifact_id)

    return list(dict.fromkeys(ids))


class TaskExecutionRequest(BaseModel):
    """Everything an executor needs for one schedulable task."""

    task: AnalysisTask
    run_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    code: Optional[str] = None
    namespace: dict[str, Any] = Field(default_factory=dict)
    workspace_dir: Optional[str] = None
    source_path: Optional[str] = None
    timeout_seconds: float = Field(default=60.0, gt=0)
    max_output_chars: int = Field(default=3000, ge=1)
    config: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def task_id(self) -> str:
        return self.task.task_id

    @property
    def input_asset_ids(self) -> list[str]:
        return list(self.task.input_asset_ids)


def build_execution_result(
    request: TaskExecutionRequest,
    *,
    status: str,
    tool_name: str,
    output_artifact_ids: Optional[list[str]] = None,
    stdout_preview: str = "",
    error: Optional[dict[str, Any]] = None,
    duration_ms: float = 0.0,
) -> ExecutionResult:
    """Build the existing runtime ExecutionResult from a V2 task request."""
    # Import here to avoid an initialization cycle between runtime and execution.
    from langgraph_langchain.runtime.models import ExecutionResult

    skill_payload = request.metadata.get("skill")
    skill_name = (
        skill_payload.get("name")
        if isinstance(skill_payload, dict)
        else None
    )
    skill_id = (
        skill_payload.get("id")
        if isinstance(skill_payload, dict)
        else None
    )
    skill_version = (
        skill_payload.get("version")
        if isinstance(skill_payload, dict)
        else None
    )
    skill_hash = (
        skill_payload.get("hash")
        if isinstance(skill_payload, dict)
        else None
    )
    return ExecutionResult(
        run_id=request.run_id,
        task_id=request.task.task_id,
        step_id=request.task.plan_step_id,
        tool_name=tool_name,
        status=status,  # type: ignore[arg-type]
        skill_name=skill_name,
        skill_id=skill_id,
        skill_version=skill_version,
        skill_hash=skill_hash,
        code_or_query=request.code,
        input_asset_ids=request.input_asset_ids,
        output_artifact_ids=output_artifact_ids or [],
        stdout_preview=stdout_preview[: request.max_output_chars],
        error=error,
        duration_ms=duration_ms,
    )

