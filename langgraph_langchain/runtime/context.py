"""Persistent metadata context layered onto the existing analysis session."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from langgraph_langchain.data.assets import DataAsset, hash_file
from langgraph_langchain.runtime.models import (
    AnalysisRun,
    AnalysisTask,
    ExecutionResult,
    PlanConfirmation,
    PlanRevision,
    RuntimeArtifact,
    RuntimePlanStep,
    new_id,
    utc_now,
)
from langgraph_langchain.runtime.plans import AnalysisPlan, validate_plan

_RUNTIME_STATE_FILE = ".analysis_runtime.json"
_RUN_HISTORY_DIR = ".analysis_runs"
logger = logging.getLogger(__name__)
_ARTIFACT_TYPES = {
    ".png": "chart",
    ".jpg": "chart",
    ".jpeg": "chart",
    ".svg": "chart",
    ".csv": "table",
    ".xlsx": "table",
    ".xls": "table",
    ".parquet": "table",
    ".md": "report",
    ".html": "report",
    ".py": "code",
}


def _semantic_context_identity(context: Optional[dict]) -> Optional[tuple[str, str, str]]:
    if not context:
        return None
    return (
        str(context.get("contract_version") or ""),
        str(context.get("provider") or ""),
        str(context.get("context_version") or ""),
    )


class AnalysisRuntime:
    """Own task/run metadata without taking ownership of dataframe memory."""

    def __init__(
        self,
        *,
        workspace_dir: Path,
        session_id: str,
        question: str,
        external_context: Optional[dict] = None,
        force_new_run: bool = False,
        parent_run_id: Optional[str] = None,
        retry_of_step_id: Optional[str] = None,
        plan_first: bool = False,
    ) -> None:
        self.workspace_dir = workspace_dir.resolve()
        self.session_id = session_id or "default"
        self.assets: dict[str, DataAsset] = {}
        self.executions: list[ExecutionResult] = []
        self.artifacts: dict[str, RuntimeArtifact] = {}
        self.findings: list[dict[str, Any]] = []
        self.metric_definitions: list[dict[str, Any]] = []
        self.assumptions: list[dict[str, Any]] = []
        if force_new_run:
            self._create_retry_run(
                question=question,
                external_context=external_context,
                parent_run_id=parent_run_id,
                retry_of_step_id=retry_of_step_id,
            )
            return
        if self._restore(question, external_context):
            return
        self.task = AnalysisTask(
            session_id=self.session_id,
            question=question,
            constraints=dict((external_context or {}).get("constraints") or {}),
            external_context=external_context,
        )
        self.run = AnalysisRun(task_id=self.task.task_id)
        if plan_first:
            self.propose_default_plan(question)
        self._persist()

    @property
    def state_path(self) -> Path:
        return self.workspace_dir / _RUNTIME_STATE_FILE

    @property
    def run_history_dir(self) -> Path:
        return self.workspace_dir / _RUN_HISTORY_DIR

    def register_dataframe(
        self,
        *,
        dataframe,
        source_path: Path,
        source_type: Optional[str] = None,
        sheet_name: Optional[str] = None,
        source_metadata: Optional[dict] = None,
    ) -> DataAsset:
        asset = DataAsset.from_dataframe(
            dataframe=dataframe,
            source_path=source_path,
            source_type=source_type,
            sheet_name=sheet_name,
            source_metadata=source_metadata,
        )
        for existing in self.assets.values():
            if (
                existing.location == asset.location
                and existing.content_hash == asset.content_hash
                and existing.sheet_name == asset.sheet_name
            ):
                return existing
        self.assets[asset.asset_id] = asset
        if asset.asset_id not in self.task.input_asset_ids:
            self.task.input_asset_ids.append(asset.asset_id)
        self._persist()
        return asset

    def register_artifact(
        self,
        path: Path,
        *,
        created_by_tool: Optional[str] = None,
        execution_id: Optional[str] = None,
        step_id: Optional[str] = None,
        input_asset_ids: Optional[list[str]] = None,
        artifact_type: Optional[str] = None,
    ) -> RuntimeArtifact:
        resolved = path.resolve()
        relative = resolved.relative_to(self.workspace_dir.parent)
        inferred_type = artifact_type or _ARTIFACT_TYPES.get(resolved.suffix.lower(), "file")
        artifact = RuntimeArtifact(
            artifact_type=inferred_type,
            name=resolved.name,
            path=str(resolved),
            relative_path=str(relative),
            url=f"/workspace/files/{relative}",
            execution_id=execution_id,
            step_id=step_id,
            input_asset_ids=list(input_asset_ids or self.task.input_asset_ids),
            created_by_tool=created_by_tool,
            content_hash=hash_file(resolved) if resolved.is_file() else None,
        )
        self.artifacts[artifact.artifact_id] = artifact
        self._persist()
        return artifact

    def record_execution(
        self,
        *,
        tool_name: str,
        status: str,
        code_or_query: Optional[str] = None,
        input_asset_ids: Optional[list[str]] = None,
        output_artifact_ids: Optional[list[str]] = None,
        stdout_preview: str = "",
        error: Optional[dict] = None,
        duration_ms: float = 0.0,
        execution_id: Optional[str] = None,
        step_id: Optional[str] = None,
    ) -> ExecutionResult:
        result = ExecutionResult(
            execution_id=execution_id or new_id("exec"),
            run_id=self.run.run_id,
            step_id=step_id,
            tool_name=tool_name,
            status=status,
            code_or_query=code_or_query,
            input_asset_ids=list(input_asset_ids or self.task.input_asset_ids),
            output_artifact_ids=list(output_artifact_ids or []),
            stdout_preview=stdout_preview[:3000],
            error=error,
            duration_ms=max(0.0, float(duration_ms)),
        )
        self.executions.append(result)
        self.run.updated_at = utc_now()
        self._persist()
        return result

    def record_finding(self, finding) -> dict[str, Any]:
        """Persist one structured finding as part of the authoritative run snapshot."""
        payload = finding.model_dump(mode="json") if hasattr(finding, "model_dump") else dict(finding)
        finding_id = payload.get("finding_id")
        if not finding_id:
            raise ValueError("Finding is missing finding_id")
        for index, existing in enumerate(self.findings):
            if existing.get("finding_id") == finding_id:
                self.findings[index] = payload
                self._persist()
                return payload
        self.findings.append(payload)
        self._persist()
        return payload

    def record_metric_definition(self, metric_definition) -> dict[str, Any]:
        payload = (
            metric_definition.model_dump(mode="json")
            if hasattr(metric_definition, "model_dump") else dict(metric_definition)
        )
        metric_name = payload.get("metric_name")
        if not metric_name:
            raise ValueError("Metric definition is missing metric_name")
        for index, existing in enumerate(self.metric_definitions):
            if existing.get("metric_name") == metric_name:
                self.metric_definitions[index] = payload
                self._persist()
                return payload
        self.metric_definitions.append(payload)
        self._persist()
        return payload

    def record_assumption(self, assumption) -> dict[str, Any]:
        payload = assumption.model_dump(mode="json") if hasattr(assumption, "model_dump") else dict(assumption)
        if payload not in self.assumptions:
            self.assumptions.append(payload)
            self._persist()
        return payload

    def propose_default_plan(self, question: str) -> AnalysisPlan:
        from langgraph_langchain.runtime.plans import default_analysis_plan

        plan = default_analysis_plan(question)
        self.run.plan = plan.model_dump(mode="json")
        self.run.plan_version = plan.version
        self.run.plan_status = "active"
        self.run.updated_at = utc_now()
        return plan

    def propose_plan(self, plan: AnalysisPlan) -> AnalysisPlan:
        """Validate and persist a complete plan before execution."""
        from langgraph_langchain.tools.registry import registry

        asset_columns = {
            asset_id: {column.name for column in asset.schema_snapshot.columns}
            for asset_id, asset in self.assets.items()
        }
        errors = validate_plan(
            plan,
            available_methods=registry.get_tool_names(),
            available_asset_ids=self.assets,
            asset_columns=asset_columns,
        )
        if errors:
            raise ValueError("Plan validation failed: " + "; ".join(errors))
        if any(step.status in {"running", "succeeded", "failed"} for step in self.run.steps):
            raise ValueError("A plan can only be proposed before execution or after pausing")
        plan.version = self.run.plan_version
        self.run.plan = plan.model_dump(mode="json")
        self.run.steps = [item.model_copy(deep=True) for item in plan.steps]
        self.run.plan_confirmation = None
        self.run.plan_status = "awaiting_confirmation" if plan.require_confirmation else "active"
        self.run.status = "awaiting_confirmation" if plan.require_confirmation else "running"
        self.run.updated_at = utc_now()
        self._persist()
        return plan

    def _sync_plan_steps(self) -> None:
        if self.run.plan is not None and self.run.plan.get("strict_execution"):
            self.run.plan["version"] = self.run.plan_version
            self.run.plan["steps"] = [
                item.model_dump(mode="json") for item in self.run.steps
            ]

    def start_step(
        self,
        *,
        objective: str,
        method: str,
        required_inputs: Optional[list[str]] = None,
        expected_outputs: Optional[list[str]] = None,
        depends_on: Optional[list[str]] = None,
    ) -> RuntimePlanStep:
        """Append and start one observable runtime step."""
        now = utc_now()
        step = next(
            (
                item
                for item in self.run.steps
                if item.status == "pending" and item.method == method
            ),
            None,
        )
        if self.run.plan and self.run.plan.get("strict_execution"):
            if step is None:
                raise ValueError(f"Method '{method}' is not present in the approved plan")
            blocked = [
                dependency
                for dependency in step.depends_on
                if not any(
                    item.step_id == dependency and item.status in {"succeeded", "skipped"}
                    for item in self.run.steps
                )
            ]
            if blocked:
                raise ValueError(
                    f"Plan step '{step.step_id}' is waiting for dependencies: "
                    + ", ".join(blocked)
                )
        if step is None:
            step = RuntimePlanStep(
                objective=objective,
                method=method,
                required_inputs=list(required_inputs or self.task.input_asset_ids),
                expected_outputs=list(expected_outputs or []),
                depends_on=list(depends_on or []),
                status="running",
                started_at=now,
            )
            self.run.steps.append(step)
        else:
            step.status = "running"
            step.started_at = now
            step.completed_at = None
            step.error = None
            if not step.required_inputs:
                step.required_inputs = list(required_inputs or self.task.input_asset_ids)
            if not step.expected_outputs:
                step.expected_outputs = list(expected_outputs or [])
        self.run.current_step_id = step.step_id
        self._sync_plan_steps()
        self.run.status = "running"
        self.run.plan_status = "active"
        self.run.updated_at = now
        self._persist()
        return step

    def complete_step(
        self,
        step_id: str,
        *,
        succeeded: bool = True,
        status: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Optional[RuntimePlanStep]:
        """Finish a step idempotently while leaving the run recoverable."""
        step = next((item for item in self.run.steps if item.step_id == step_id), None)
        if step is None:
            return None
        if step.status in {"pending", "running"}:
            resolved_status = status or ("succeeded" if succeeded else "failed")
            if resolved_status not in {"succeeded", "needs_revision", "failed", "skipped"}:
                raise ValueError(f"Unsupported terminal step status: {resolved_status}")
            step.status = resolved_status
            step.completed_at = utc_now()
            step.error = error[:1000] if error else None
        if self.run.current_step_id == step_id:
            self.run.current_step_id = None
        self._sync_plan_steps()
        self.run.updated_at = utc_now()
        self._persist()
        return step

    def set_run_status(self, status: str, *, error: Optional[str] = None) -> None:
        """Persist a run terminal state and close any still-running steps."""
        if status in {"failed", "cancelled"}:
            self.fail_open_steps(error or status, persist=False)
        self.run.status = status
        if status == "completed":
            self.run.plan_status = "completed"
        self.run.current_step_id = None
        self.run.updated_at = utc_now()
        self._persist()

    def pause_plan(self, reason: str, *, require_confirmation: bool = False) -> AnalysisRun:
        """Pause execution at a recoverable boundary and persist the control state."""
        if self.run.status in {"completed", "failed", "cancelled"}:
            raise ValueError(f"Run in status '{self.run.status}' cannot be paused")
        now = utc_now()
        for step in self.run.steps:
            if step.status == "running":
                step.status = "paused"
                step.completed_at = now
        self.run.current_step_id = None
        self.run.pause_reason = reason.strip() or "Paused by user"
        self.run.paused_at = now
        self.run.plan_status = "awaiting_confirmation" if require_confirmation else "paused"
        self.run.status = "awaiting_confirmation" if require_confirmation else "paused"
        self.run.updated_at = now
        self._persist()
        return self.run

    def revise_plan(
        self,
        *,
        steps: list[dict[str, Any]],
        reason: str,
        revised_by: str = "user",
    ) -> AnalysisRun:
        """Replace only unexecuted work and create an immutable plan revision."""
        if self.run.status not in {"paused", "awaiting_confirmation", "failed"}:
            raise ValueError("Plan can only be revised while paused or awaiting confirmation")
        if not reason.strip():
            raise ValueError("Plan revision reason must be non-empty")
        if not steps:
            raise ValueError("Plan revision must contain at least one pending step")

        preserved = [
            item
            for item in self.run.steps
            if item.status not in {"pending", "paused"}
        ]
        revised_steps: list[RuntimePlanStep] = []
        known_ids = {item.step_id for item in preserved}
        for raw in steps:
            step = RuntimePlanStep.model_validate({**raw, "status": "pending"})
            if step.step_id in known_ids:
                raise ValueError(f"Duplicate plan step id: {step.step_id}")
            if step.step_id in step.depends_on:
                raise ValueError(f"Plan step '{step.step_id}' cannot depend on itself")
            unknown = [item for item in step.depends_on if item not in known_ids]
            if unknown:
                raise ValueError(
                    f"Plan step '{step.step_id}' has unknown or forward dependencies: {', '.join(unknown)}"
                )
            known_ids.add(step.step_id)
            revised_steps.append(step)

        self.run.steps = preserved + revised_steps
        self.run.plan_version += 1
        self.run.plan_revisions.append(
            PlanRevision(
                version=self.run.plan_version,
                reason=reason.strip(),
                revised_by=revised_by.strip() or "user",
                steps=[item.model_dump(mode="json") for item in self.run.steps],
            )
        )
        self.run.plan_confirmation = None
        self.run.plan_status = "awaiting_confirmation"
        self.run.status = "awaiting_confirmation"
        self.run.pause_reason = reason.strip()
        self.run.current_step_id = None
        self._sync_plan_steps()
        self.run.updated_at = utc_now()
        self._persist()
        return self.run

    def confirm_plan(
        self,
        *,
        confirmed_by: str = "user",
        note: Optional[str] = None,
    ) -> AnalysisRun:
        if self.run.plan_status != "awaiting_confirmation":
            raise ValueError("Plan is not awaiting confirmation")
        self.run.plan_confirmation = PlanConfirmation(
            version=self.run.plan_version,
            confirmed_by=confirmed_by.strip() or "user",
            note=note.strip() if note else None,
        )
        self.run.plan_status = "confirmed"
        self.run.status = "paused"
        self.run.updated_at = utc_now()
        self._persist()
        return self.run

    def resume_plan(self) -> AnalysisRun:
        if self.run.plan_status == "awaiting_confirmation":
            raise ValueError("Plan must be confirmed before it can resume")
        if self.run.status not in {"paused", "running"}:
            raise ValueError(f"Run in status '{self.run.status}' cannot resume")
        for step in self.run.steps:
            if step.status == "paused":
                step.status = "pending"
                step.started_at = None
                step.completed_at = None
        self.run.plan_status = "active"
        self.run.status = "running"
        self.run.pause_reason = None
        self.run.paused_at = None
        self.run.updated_at = utc_now()
        self._persist()
        return self.run

    def fail_open_steps(self, error: str, *, persist: bool = True) -> None:
        now = utc_now()
        for step in self.run.steps:
            if step.status == "running":
                step.status = "failed"
                step.completed_at = now
                step.error = error[:1000]
        self.run.current_step_id = None
        self.run.updated_at = now
        if persist:
            self._persist()

    def snapshot(self) -> dict:
        return {
            "version": 1,
            "task": self.task.model_dump(mode="json"),
            "run": self.run.model_dump(mode="json"),
            "assets": [asset.model_dump(mode="json") for asset in self.assets.values()],
            "executions": [item.model_dump(mode="json") for item in self.executions],
            "artifacts": [item.model_dump(mode="json") for item in self.artifacts.values()],
            "findings": list(self.findings),
            "metric_definitions": list(self.metric_definitions),
            "assumptions": list(self.assumptions),
        }

    def _restore(self, question: str, external_context: Optional[dict]) -> bool:
        if not self.state_path.exists():
            return False
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            task = AnalysisTask.model_validate(raw["task"])
            if (
                task.session_id != self.session_id
                or task.question != question
                or _semantic_context_identity(task.external_context)
                != _semantic_context_identity(external_context)
            ):
                return False
            self.task = task
            self.run = AnalysisRun.model_validate(raw["run"])
            self.assets = {
                item["asset_id"]: DataAsset.model_validate(item)
                for item in raw.get("assets", [])
            }
            self.executions = [
                ExecutionResult.model_validate(item)
                for item in raw.get("executions", [])
            ]
            self.artifacts = {
                item["artifact_id"]: RuntimeArtifact.model_validate(item)
                for item in raw.get("artifacts", [])
            }
            self.findings = [dict(item) for item in raw.get("findings", [])]
            self.metric_definitions = [
                dict(item) for item in raw.get("metric_definitions", [])
            ]
            self.assumptions = [dict(item) for item in raw.get("assumptions", [])]
            return True
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            return False

    def _create_retry_run(
        self,
        *,
        question: str,
        external_context: Optional[dict],
        parent_run_id: Optional[str],
        retry_of_step_id: Optional[str],
    ) -> None:
        if not parent_run_id or not retry_of_step_id:
            raise ValueError("Retry runs require parent_run_id and retry_of_step_id")
        if re.fullmatch(r"run_[0-9a-f]{32}", parent_run_id) is None:
            raise ValueError("Invalid parent run id")
        parent_path = self.run_history_dir / f"{parent_run_id}.json"
        try:
            raw = json.loads(parent_path.read_text(encoding="utf-8"))
            parent_task = AnalysisTask.model_validate(raw["task"])
            parent_run = AnalysisRun.model_validate(raw["run"])
        except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Unable to restore parent run {parent_run_id}") from exc
        if parent_run.run_id != parent_run_id or parent_task.session_id != self.session_id:
            raise ValueError("Parent run does not belong to this session")
        if not any(step.step_id == retry_of_step_id for step in parent_run.steps):
            raise ValueError("Retry step does not belong to the parent run")
        if parent_task.question != question:
            raise ValueError("Retry question does not match the parent task")
        if _semantic_context_identity(parent_task.external_context) != _semantic_context_identity(
            external_context
        ):
            raise ValueError("Retry semantic context does not match the parent task")

        self.task = parent_task.model_copy(deep=True)
        self.task.input_asset_ids = []
        self.assets = {}
        self.executions = []
        self.artifacts = {}
        self.findings = []
        self.metric_definitions = []
        self.assumptions = []
        self.run = AnalysisRun(
            task_id=self.task.task_id,
            parent_run_id=parent_run_id,
            retry_of_step_id=retry_of_step_id,
            attempt=parent_run.attempt + 1,
        )
        self._persist()

    def _persist(self) -> None:
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        snapshot = self.snapshot()
        fd, temp_path = tempfile.mkstemp(
            dir=str(self.workspace_dir), prefix=".runtime_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(snapshot, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.state_path)
        except BaseException:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
        try:
            self.run_history_dir.mkdir(parents=True, exist_ok=True)
            archive_path = self.run_history_dir / f"{self.run.run_id}.json"
            archive_fd, archive_temp = tempfile.mkstemp(
                dir=str(self.run_history_dir), prefix=".run_", suffix=".tmp"
            )
            try:
                with os.fdopen(archive_fd, "w", encoding="utf-8") as handle:
                    json.dump(snapshot, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(archive_temp, archive_path)
            except BaseException:
                try:
                    os.unlink(archive_temp)
                except OSError:
                    pass
                raise
        except Exception as exc:
            logger.warning("Runtime history archival failed: %s", exc)
        try:
            from langgraph_langchain.runtime.sqlite_store import SQLiteMetadataStore
            SQLiteMetadataStore(self.workspace_dir.parent / ".analysis_metadata.sqlite").save_snapshot(snapshot)
        except Exception as exc:
            logger.warning("Runtime SQLite persistence failed: %s", exc)


def register_session_artifact(
    session,
    path: Path,
    *,
    created_by_tool: str,
    execution_id: Optional[str] = None,
    step_id: Optional[str] = None,
) -> dict:
    """Register an artifact with the new runtime and legacy session list."""
    runtime = getattr(session, "analysis_runtime", None)
    if isinstance(runtime, AnalysisRuntime):
        try:
            artifact = runtime.register_artifact(
                path,
                created_by_tool=created_by_tool,
                execution_id=execution_id,
                step_id=step_id or getattr(session, "current_runtime_step_id", None),
            )
            metadata = artifact.to_legacy_dict()
        except Exception as exc:
            logger.warning("Runtime artifact registration failed: %s", exc)
            metadata = _legacy_artifact_metadata(session, path)
    else:
        metadata = _legacy_artifact_metadata(session, path)
    session.new_artifacts.append(metadata)
    return metadata


def _legacy_artifact_metadata(session, path: Path) -> dict:
    relative = path.relative_to(session.workspace_dir.parent)
    return {
        "name": path.name,
        "path": str(path),
        "relative_path": str(relative),
        "url": f"/workspace/files/{relative}",
    }


def record_session_execution(session, **kwargs) -> Optional[ExecutionResult]:
    """Record an execution when a session has opted into the new runtime."""
    runtime = getattr(session, "analysis_runtime", None)
    if not isinstance(runtime, AnalysisRuntime):
        return None
    try:
        kwargs.setdefault("step_id", getattr(session, "current_runtime_step_id", None))
        return runtime.record_execution(**kwargs)
    except Exception as exc:
        logger.warning("Runtime execution recording failed: %s", exc)
        return None


def register_session_dataframe(session, **kwargs) -> Optional[DataAsset]:
    """Register a dataframe without making metadata failure fatal to analysis."""
    runtime = getattr(session, "analysis_runtime", None)
    if not isinstance(runtime, AnalysisRuntime):
        return None
    try:
        return runtime.register_dataframe(**kwargs)
    except Exception as exc:
        logger.warning("Runtime data asset registration failed: %s", exc)
        return None


class SessionExecutionRecorder:
    """Idempotent compatibility recorder for one synchronous tool call."""

    def __init__(
        self,
        session,
        *,
        tool_name: str,
        code_or_query: Optional[str] = None,
    ) -> None:
        self.session = session
        self.tool_name = tool_name
        self.code_or_query = code_or_query
        self.execution_id = new_id("exec")
        self.step_id = getattr(session, "current_runtime_step_id", None)
        self.started_at = time.monotonic()
        self.artifact_ids: list[str] = []
        self.completed = False

    def register_artifact(self, path: Path) -> dict:
        metadata = register_session_artifact(
            self.session,
            path,
            created_by_tool=self.tool_name,
            execution_id=self.execution_id,
            step_id=self.step_id,
        )
        artifact_id = metadata.get("artifact_id")
        if artifact_id:
            self.artifact_ids.append(artifact_id)
        return metadata

    def succeed(self, stdout_preview: str = "") -> Optional[ExecutionResult]:
        return self._complete(status="succeeded", stdout_preview=stdout_preview)

    def fail(
        self,
        message: str,
        *,
        error_type: str = "ToolExecutionError",
        stdout_preview: str = "",
    ) -> Optional[ExecutionResult]:
        return self._complete(
            status="failed",
            stdout_preview=stdout_preview,
            error={"type": error_type, "message": message[:1000]},
        )

    def revision(
        self,
        message: str,
        *,
        feedback_type: str = "ValidationFeedback",
        stdout_preview: str = "",
    ) -> Optional[ExecutionResult]:
        """Record recoverable validation feedback without counting it as failure."""
        return self._complete(
            status="needs_revision",
            stdout_preview=stdout_preview,
            error={"type": feedback_type, "message": message[:1000]},
        )

    def _complete(
        self,
        *,
        status: str,
        stdout_preview: str,
        error: Optional[dict] = None,
    ) -> Optional[ExecutionResult]:
        if self.completed:
            return None
        self.completed = True
        return record_session_execution(
            self.session,
            execution_id=self.execution_id,
            step_id=self.step_id,
            tool_name=self.tool_name,
            status=status,
            code_or_query=self.code_or_query,
            output_artifact_ids=self.artifact_ids,
            stdout_preview=stdout_preview,
            error=error,
            duration_ms=(time.monotonic() - self.started_at) * 1000,
        )
