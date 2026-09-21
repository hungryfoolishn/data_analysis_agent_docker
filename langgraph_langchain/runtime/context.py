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
from langgraph_langchain.runtime.scheduler import TaskScheduler
from langgraph_langchain.runtime.runner import TaskRunner
from langgraph_langchain.runtime.executor import TaskExecutor
from langgraph_langchain.runtime.finding_builder import FindingProvenanceError
from langgraph_langchain.runtime.learning import (
    LearningMemoryStore,
    LearningSnapshot,
    TaskEvaluation,
    build_task_evaluation,
)
from langgraph_langchain.schemas import Finding, EvidenceItem, VerificationResult

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
        self.plan: Optional[AnalysisPlan] = None
        self.scheduler: Optional[TaskScheduler] = None
        self.executor: Optional[TaskExecutor] = None
        self.runner: Optional[TaskRunner] = None
        self.controller: Optional[object] = None
        self.verifications: list[VerificationResult] = []
        self.evidence: list[EvidenceItem] = []
        self.failures: list[dict[str, Any]] = []
        self.evaluations: list[TaskEvaluation] = []
        self.execution_namespace: dict[str, Any] = {}
        self.learning_memory = LearningMemoryStore(
            self.workspace_dir / ".analysis_learning.json"
        )
        self.status: str = "created"
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
            self.plan = self.propose_default_plan(question)
        self.status = self.run.status
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

    def initialize_plan(
        self,
        plan: AnalysisPlan,
        *,
        max_running_tasks: int = 1,
        argument_defaults_by_method: Optional[dict[str, dict[str, Any]]] = None,
    ) -> TaskScheduler:
        """Adopt an approved plan and hand scheduling control to Runtime V2."""
        if max_running_tasks < 1:
            raise ValueError("max_running_tasks must be at least 1")
        if not plan.steps:
            raise ValueError("Plan has no steps")

        self.plan = plan.model_copy(deep=True)
        self.run.plan = self.plan.model_dump(mode="json")
        self.run.plan_version = self.plan.version
        self.run.plan_status = "active"
        self.run.steps = [
            item.model_copy(deep=True) for item in self.plan.steps
        ]
        self.run.current_step_id = None
        self.run.status = "running"
        self.run.plan_status = "active"
        self.run.updated_at = utc_now()
        self.status = "running"
        self.scheduler = TaskScheduler.from_plan(
            self.plan,
            session_id=self.session_id,
            max_running_tasks=max_running_tasks,
            argument_defaults_by_method=argument_defaults_by_method,
        )
        self._persist()
        return self.scheduler

    def attach_runner(self, runner: TaskRunner) -> TaskRunner:
        """Attach a runner whose scheduler is owned by this Runtime."""
        if self.scheduler is None:
            raise RuntimeError("Call initialize_plan before attaching a runner")
        if runner.scheduler is not self.scheduler:
            raise ValueError("Runner scheduler does not belong to this Runtime")
        self.executor = getattr(runner, "executor", None)
        self.runner = runner
        return runner

    def configure_executor(
        self,
        executor: TaskExecutor,
        *,
        verifier=None,
        evidence_factory=None,
        verification_policy=None,
    ) -> TaskRunner:
        """Attach an executor and create the Runtime-owned task runner."""
        if self.scheduler is None:
            raise RuntimeError("Call initialize_plan before configuring an executor")

        effective_verifier = verifier
        if verification_policy is not None:
            base_verifier = verifier

            def policy_verifier(result):
                task = self.scheduler.get_task(result.task_id)
                checks = list(
                    verification_policy.verify(
                        task,
                        result,
                        artifacts=self.artifacts,
                        workspace_dir=self.workspace_dir,
                    )
                )
                if base_verifier is not None:
                    checks.extend(base_verifier(result))
                return checks

            effective_verifier = policy_verifier

        runner = TaskRunner(
            scheduler=self.scheduler,
            executor=executor,
            verifier=effective_verifier,
            evidence_factory=evidence_factory,
        )
        return self.attach_runner(runner)

    @property
    def verification_results(self) -> list[VerificationResult]:
        """Compatibility alias for Runtime V6's canonical verification collection."""
        return self.verifications

    def build_execution_controller(
        self,
        *,
        structured_executor=None,
        react_executor=None,
        python_executor=None,
        on_task_start=None,
        on_task_finish=None,
        skill_retriever=None,
        verifier=None,
        evidence_factory=None,
        verification_policy=None,
    ):
        """Build the V6 controller around this Runtime-owned scheduler."""
        from langgraph_langchain.runtime.graph import RuntimeV2Controller

        if self.plan is None or self.scheduler is None:
            raise RuntimeError("Call initialize_plan before building an execution controller")

        executor = TaskExecutor(
            structured=structured_executor,
            react=react_executor,
            python=python_executor,
        )
        self.configure_executor(
            executor,
            verifier=verifier,
            evidence_factory=evidence_factory,
            verification_policy=verification_policy,
        )
        controller = RuntimeV2Controller(
            scheduler=self.scheduler,
            runner=self.runner,
            run_id=self.run.run_id,
            session_id=self.session_id,
            on_task_start=on_task_start,
            on_task_finish=on_task_finish,
            skill_retriever=skill_retriever,
            analysis_runtime=self,
        )
        self.controller = controller
        return controller

    def execute_all(
        self,
        *,
        max_steps: Optional[int] = None,
        stop_on_failure: bool = True,
    ) -> list[ExecutionResult]:
        """Execute the Runtime-owned plan until completion or an owner-visible stop."""
        if self.runner is None or self.scheduler is None:
            raise RuntimeError("Runtime has no runner; call build_execution_controller first")
        if max_steps is not None and max_steps < 1:
            raise ValueError("max_steps must be at least 1")

        results: list[ExecutionResult] = []
        remaining = max_steps
        while remaining is None or remaining > 0:
            result = self.execute_next_task()
            if result is None:
                break
            results.append(result)
            if remaining is not None:
                remaining -= 1
            if stop_on_failure and result.status == "failed":
                break
        return results

    def next_task(self):
        if self.scheduler is None:
            raise RuntimeError("Runtime has no scheduler")
        task = self.scheduler.next_task()
        if task is not None:
            self._sync_runtime_step_from_task(task)
        self.status = "running" if task is not None else self.status
        return task

    def start_task(self, task_id: str):
        if self.scheduler is None:
            raise RuntimeError("Runtime has no scheduler")
        task = self.scheduler.get_task(task_id)
        self.scheduler.mark_running(task_id)
        self._sync_runtime_step_from_task(task)
        self.run.current_step_id = task_id
        self.status = "running"
        self.run.status = "running"
        self.run.updated_at = utc_now()
        self._persist()
        return task

    def complete_task(self, task_id: str):
        if self.scheduler is None:
            raise RuntimeError("Runtime has no scheduler")
        task = self.scheduler.get_task(task_id)
        self.scheduler.mark_succeeded(task_id)
        self._sync_runtime_step_from_task(task)
        if self.run.current_step_id == task_id:
            self.run.current_step_id = None
        self.run.updated_at = utc_now()
        self._persist()
        return task

    def fail_task(self, task_id: str, *, error: Optional[str] = None):
        if self.scheduler is None:
            raise RuntimeError("Runtime has no scheduler")
        task = self.scheduler.get_task(task_id)
        self.scheduler.mark_failed(task_id, error=error)
        self._sync_runtime_step_from_task(task)
        self.failures.append(
            {
                "task_id": task_id,
                "error": error or "Task failed",
                "created_at": utc_now(),
            }
        )
        self.status = "failed"
        self.run.status = "failed"
        self.run.updated_at = utc_now()
        self._persist()
        return task

    def resolve_arguments(self, task) -> dict[str, Any]:
        arguments = task.constraints.get("arguments")
        return dict(arguments) if isinstance(arguments, dict) else {}

    def build_execution_request(self, task):
        from langgraph_langchain.execution.task_models import TaskExecutionRequest

        return TaskExecutionRequest(
            task=task,
            run_id=self.run.run_id,
            arguments=self.resolve_arguments(task),
            code=task.constraints.get("code"),
            namespace=self.execution_namespace,
            workspace_dir=str(self.workspace_dir),
            source_path=task.constraints.get("source_path"),
            timeout_seconds=float(task.constraints.get("timeout_seconds", 60.0)),
            max_output_chars=int(task.constraints.get("max_output_chars", 3000)),
        )

    def execute_next_task(self, *, request_factory=None):
        """Execute one scheduler-ready task and record its full V2 lineage."""
        if self.runner is None or self.scheduler is None:
            raise RuntimeError("Runtime has no runner; call configure_executor first")

        step = self.runner.execute_next(
            run_id=self.run.run_id,
            request_factory=request_factory or self.build_execution_request,
        )
        result = step.result
        if result is not None:
            self.record_execution_result(result)
            task = self.scheduler.get_task(result.task_id)
            self._sync_runtime_step_from_task(task)
            if result.status == "failed":
                self.status = "failed"
                self.run.status = "failed"
            elif all(
                task.status in {"succeeded", "failed", "skipped", "cancelled"}
                for task in self.scheduler.tasks
            ):
                self.status = "completed"
                self.run.status = "completed"
        elif step.action == "completed":
            self.status = "completed"
            self.run.status = "completed"
        elif step.action == "failed":
            self.status = "failed"
            self.run.status = "failed"
        self.run.updated_at = utc_now()
        self._persist()
        return result

    def record_task_evaluation(
        self,
        result: ExecutionResult,
        *,
        persist: bool = True,
    ) -> TaskEvaluation:
        """Evaluate one execution and update durable Runtime learning memory."""
        evaluation = build_task_evaluation(self, result)
        for index, item in enumerate(self.evaluations):
            if item.task_id == evaluation.task_id or item.execution_id == evaluation.execution_id:
                self.evaluations[index] = evaluation
                break
        else:
            self.evaluations.append(evaluation)
        self.learning_memory.record_evaluations([evaluation])
        if persist:
            self._persist()
        return evaluation

    def learning_summary(self) -> LearningSnapshot:
        """Return the durable learning snapshot for this session."""
        return self.learning_memory.snapshot()

    def record_execution_result(self, result: ExecutionResult) -> ExecutionResult:
        """Record one V2 result, plus verification and evidence lineage."""
        if not result.task_id:
            raise ValueError("ExecutionResult is missing task_id")

        existing = next(
            (
                item
                for item in self.executions
                if item.execution_id == result.execution_id
                or (
                    result.step_id
                    and item.step_id == result.step_id
                    and item.tool_name == result.tool_name
                )
            ),
            None,
        )
        if existing is not None:
            # Structured tools may already record the concrete execution via
            # SessionExecutionRecorder.  The V2 wrapper must adopt that ID so
            # artifacts, verification, and evidence share one execution node.
            actual_execution_id = existing.execution_id
            result.execution_id = actual_execution_id
            for verification in result.verification_results:
                verification.execution_id = actual_execution_id
            if result.evidence is not None:
                source_ids = list(
                    dict.fromkeys(
                        [actual_execution_id, *(result.evidence.source_execution_ids or [])]
                    )
                )
                result.evidence.source_execution_ids = source_ids
        # Link artifacts registered by PythonTaskExecutor even when the exact
        # execution ID was not known before the ExecutionResult was built.
        artifact_ids = list(result.output_artifact_ids)
        for artifact_id in artifact_ids:
            artifact = self.artifacts.get(artifact_id)
            if artifact is not None and not artifact.execution_id:
                artifact.execution_id = result.execution_id

        if existing is not None:
            # Reconcile artifacts registered directly by the wrapped tool.
            for artifact_id, artifact in self.artifacts.items():
                if artifact.execution_id == actual_execution_id:
                    artifact_ids.append(artifact_id)

            # Ensure each reconciled artifact has at least an existence check.
            from langgraph_langchain.verification import verify_artifact_existence

            existing_verification_ids = {
                item.artifact_id for item in result.verification_results
            }
            for artifact_id in dict.fromkeys(artifact_ids):
                artifact = self.artifacts.get(artifact_id)
                if artifact is None:
                    continue
                if artifact_id not in existing_verification_ids:
                    result.verification_results.append(
                        verify_artifact_existence(
                            artifact,
                            workspace_dir=self.workspace_dir,
                            context={
                                "run_id": result.run_id,
                                "task_id": result.task_id,
                                "step_id": result.step_id,
                                "execution_id": actual_execution_id,
                                "artifact_id": artifact_id,
                            },
                        )
                    )
            result.output_artifact_ids = list(dict.fromkeys(artifact_ids))

        if existing is None:
            self.executions.append(result.model_copy(deep=True))
        else:
            index = self.executions.index(existing)
            self.executions[index] = result.model_copy(deep=True)

        for verification in result.verification_results:
            self.record_verification(verification)

        if result.evidence is not None:
            self.record_evidence(result.evidence)

        if result.status == "failed":
            failure_payload = {
                "task_id": result.task_id,
                "error": (
                    result.error.get("message")
                    if isinstance(result.error, dict) and result.error.get("message")
                    else "Task execution failed"
                ),
                "execution_id": result.execution_id,
                "created_at": utc_now(),
            }
            failure_index = next(
                (
                    index
                    for index, item in enumerate(self.failures)
                    if item.get("task_id") == result.task_id
                ),
                None,
            )
            if failure_index is None:
                self.failures.append(failure_payload)
            else:
                self.failures[failure_index] = failure_payload

        task = self.scheduler.get_task(result.task_id) if self.scheduler else None
        if task is not None:
            self._sync_runtime_step_from_task(task)

        # The execution result is the final authority for terminal step state;
        # apply it after scheduler sync so replay/direct recording works too.
        result_like = type(
            "ResultStatusView",
            (),
            {"task_id": result.task_id, "status": result.status, "error": result.error},
        )()
        self._sync_runtime_step_from_task(result_like)

        self.record_task_evaluation(result, persist=False)

        self.run.updated_at = utc_now()
        self._persist()
        return result

    def record_verification_results(
        self,
        results,
    ) -> list[VerificationResult]:
        """Record a batch of verification results and return the canonical copies."""
        return [self.record_verification(item) for item in results]

    def record_verification(self, verification: VerificationResult) -> VerificationResult:
        for index, item in enumerate(self.verifications):
            if item.verification_id == verification.verification_id:
                self.verifications[index] = verification.model_copy(deep=True)
                return self.verifications[index]
        self.verifications.append(verification.model_copy(deep=True))
        return verification

    def record_evidence(self, evidence: EvidenceItem) -> EvidenceItem:
        for index, item in enumerate(self.evidence):
            if item.evidence_id == evidence.evidence_id:
                self.evidence[index] = evidence.model_copy(deep=True)
                return self.evidence[index]
        self.evidence.append(evidence.model_copy(deep=True))
        return evidence

    def _sync_runtime_step_from_task(self, task) -> None:
        status_map = {
            "pending": "pending",
            "running": "running",
            "succeeded": "succeeded",
            "failed": "failed",
            "skipped": "skipped",
            "cancelled": "skipped",
        }
        step = next(
            (item for item in self.run.steps if item.step_id == task.task_id),
            None,
        )
        if step is None:
            return
        mapped_status = status_map.get(task.status, step.status)
        if step.status != mapped_status:
            step.status = mapped_status
            if mapped_status == "running" and step.started_at is None:
                step.started_at = utc_now()
            if mapped_status in {"succeeded", "failed", "skipped"}:
                step.completed_at = utc_now()
        if mapped_status == "failed":
            step.error = getattr(task, "error", None)
        if self.run.current_step_id == task.task_id and task.status not in {
            "pending",
            "running",
        }:
            self.run.current_step_id = None
        self._sync_plan_steps()

    def record_verified_finding(self, finding) -> dict[str, Any]:
        """Persist a finding only after checking its complete V7 provenance."""
        from langgraph_langchain.runtime.finding_builder import FindingBuilder

        finding = (
            Finding.model_validate(finding)
            if not isinstance(finding, Finding) and isinstance(finding, dict)
            else finding
        )
        errors = FindingBuilder(self).validate_finding(finding)
        if errors:
            raise FindingProvenanceError(errors)
        return self.record_finding(finding)

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
            "verifications": [
                item.model_dump(mode="json") for item in self.verifications
            ],
            "evidence": [
                item.model_dump(mode="json") for item in self.evidence
            ],
            "failures": list(self.failures),
            "evaluations": [
                item.model_dump(mode="json") for item in self.evaluations
            ],
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
            self.verifications = [
                VerificationResult.model_validate(item)
                for item in raw.get("verifications", [])
            ]
            self.evidence = [
                EvidenceItem.model_validate(item)
                for item in raw.get("evidence", [])
            ]
            self.failures = [dict(item) for item in raw.get("failures", [])]
            self.evaluations = [
                TaskEvaluation.model_validate(item)
                for item in raw.get("evaluations", [])
            ]
            self.plan = (
                AnalysisPlan.model_validate(self.run.plan)
                if self.run.plan
                else None
            )
            self.status = self.run.status
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
