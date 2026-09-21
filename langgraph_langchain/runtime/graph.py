"""LangGraph controller for Runtime V2.

This module keeps the graph layer thin.  The scheduler owns task readiness and
ordering; ``TaskRunner`` owns the execution loop; ``TaskExecutor`` owns tool,
ReAct, and Python execution.  The graph nodes only advance the runner and carry
results through graph state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional, Sequence, TypedDict

from langgraph.graph import END, START, StateGraph

from langgraph_langchain.execution.python_task_executor import PythonTaskExecutor
from langgraph_langchain.execution.react_executor import ReactTaskExecutor
from langgraph_langchain.execution.structured_executor import StructuredTaskExecutor
from langgraph_langchain.execution.task_models import TaskExecutionRequest
from langgraph_langchain.runtime.executor import TaskExecutor
from langgraph_langchain.runtime.models import ExecutionResult
from langgraph_langchain.schemas import EvidenceItem, VerificationResult
from langgraph_langchain.runtime.plans import AnalysisPlan
from langgraph_langchain.runtime.runner import TaskRunner, TaskRunnerStep
from langgraph_langchain.runtime.scheduler import TaskScheduler
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.skill_retriever import SkillRetriever
from langgraph_langchain.schemas import AnalysisTask

if TYPE_CHECKING:
    from langgraph_langchain.runtime.context import AnalysisRuntime


class RuntimeV2State(TypedDict, total=False):
    """Minimal graph state; large run objects stay in the controller."""

    run_id: str
    session_id: str
    status: str
    action: str
    reason: str
    task_id: str
    execution_id: str
    result: ExecutionResult


class RuntimeV2Controller:
    """A compiled graph plus the Runtime objects it controls."""

    def __init__(
        self,
        *,
        scheduler: TaskScheduler,
        runner: TaskRunner,
        run_id: str,
        session_id: str,
        on_task_start: Optional[Callable[[AnalysisTask], None]] = None,
        on_task_finish: Optional[Callable[[ExecutionResult], None]] = None,
        skill_retriever: Optional[SkillRetriever] = None,
        analysis_runtime: Optional["AnalysisRuntime"] = None,
    ) -> None:
        self.scheduler = scheduler
        self.runner = runner
        self.run_id = run_id
        self.session_id = session_id
        self.on_task_start = on_task_start
        self.on_task_finish = on_task_finish
        self.skill_retriever = skill_retriever
        self.analysis_runtime = analysis_runtime
        self.graph = self._build_graph()

    def _build_graph(self):
        def execute_task(state: RuntimeV2State) -> RuntimeV2State:
            next_task = self.scheduler.next_task()
            if next_task is not None and self.on_task_start is not None:
                self.on_task_start(next_task)

            if self.analysis_runtime is not None:
                result = self.analysis_runtime.execute_next_task(
                    request_factory=self._request_factory,
                )
                if result is None:
                    failed_tasks = [
                        task for task in self.scheduler.tasks
                        if task.status == "failed"
                    ]
                    action = "failed" if failed_tasks else "completed"
                    step = TaskRunnerStep(
                        action=action,
                        result=None,
                        reason=(
                            "Runtime has no ready tasks; failed tasks remain"
                            if failed_tasks
                            else "Runtime has no ready tasks"
                        ),
                    )
                else:
                    action = (
                        "failed"
                        if result.status == "failed"
                        else "executed"
                    )
                    step = TaskRunnerStep(action=action, result=result)
            else:
                step: TaskRunnerStep = self.runner.execute_next(
                    run_id=self.run_id,
                    request_factory=self._request_factory,
                )

            if step.result is not None and self.on_task_finish is not None:
                self.on_task_finish(step.result)

            update: RuntimeV2State = {
                "run_id": self.run_id,
                "session_id": self.session_id,
                "status": self._status_from_action(step),
                "action": step.action,
            }
            if step.reason is not None:
                update["reason"] = step.reason
            if step.result is not None:
                update["task_id"] = step.result.task_id or ""
                update["execution_id"] = step.result.execution_id
                update["result"] = step.result
            return update

        def route_after_task(state: RuntimeV2State) -> str:
            return "execute_task" if state.get("action") == "executed" else END

        graph = StateGraph(RuntimeV2State)
        graph.add_node("execute_task", execute_task)
        graph.add_edge(START, "execute_task")
        graph.add_conditional_edges(
            "execute_task",
            route_after_task,
            {"execute_task": "execute_task", END: END},
        )
        return graph.compile()

    @staticmethod
    def _status_from_action(step: TaskRunnerStep) -> str:
        if step.action == "completed":
            return "completed"
        if step.action == "failed":
            return "failed"
        if step.action == "blocked":
            return "blocked"
        if step.action in {"incomplete", "limit_reached"}:
            return "incomplete"
        return "running"

    def _request_factory(self, task: AnalysisTask) -> TaskExecutionRequest:
        if self.analysis_runtime is not None:
            # Runtime owns workspace, namespace, timeout, and persistence-oriented
            # request fields; the controller only augments it with skill metadata.
            request = self.analysis_runtime.build_execution_request(task)
        else:
            arguments = task.constraints.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}

            request = TaskExecutionRequest(
                task=task,
                run_id=self.run_id,
                arguments=arguments,
                code=task.constraints.get("code"),
                workspace_dir=task.constraints.get("workspace_dir"),
                source_path=task.constraints.get("source_path"),
                timeout_seconds=float(task.constraints.get("timeout_seconds", 60.0)),
                max_output_chars=int(task.constraints.get("max_output_chars", 3000)),
            )

        metadata: dict[str, Any] = {}
        if self.skill_retriever is not None:
            match = self.skill_retriever.retrieve_for_task(task)
            metadata["skill"] = {
                "name": match.skill_name,
                "version": match.skill_version,
                "hash": match.skill_hash,
                "score": match.score,
                "source": match.source,
                "fallback": match.fallback,
                "reason": match.reason,
            }

        request.metadata = metadata
        return request


def build_runtime_v2_controller(
    *,
    plan: AnalysisPlan,
    session_id: str,
    run_id: str,
    tool_resolver: Optional[Callable[[str], Any]] = None,
    structured_executor: Optional[StructuredTaskExecutor] = None,
    react_executor: Optional[ReactTaskExecutor] = None,
    python_executor: Optional[PythonTaskExecutor] = None,
    max_running_tasks: int = 1,
    argument_defaults_by_method: Optional[dict[str, dict[str, Any]]] = None,
    on_task_start: Optional[Callable[[AnalysisTask], None]] = None,
    on_task_finish: Optional[Callable[[ExecutionResult], None]] = None,
    verifier: Optional[Callable[[ExecutionResult], Sequence[VerificationResult]]] = None,
    evidence_factory: Optional[
        Callable[[ExecutionResult, Sequence[VerificationResult]], EvidenceItem]
    ] = None,
    skill_retriever: Optional[SkillRetriever] = None,
    analysis_runtime: Optional["AnalysisRuntime"] = None,
) -> RuntimeV2Controller:
    """Build the Runtime V2 graph with explicit Structured/ReAct/Python workers."""
    scheduler = TaskScheduler.from_plan(
        plan,
        session_id=session_id,
        max_running_tasks=max_running_tasks,
        argument_defaults_by_method=argument_defaults_by_method,
    )
    if structured_executor is None:
        if tool_resolver is None:
            raise ValueError("structured_executor or tool_resolver is required")
        structured_executor = StructuredTaskExecutor(tool_resolver=tool_resolver)
    executor = TaskExecutor(
        structured=structured_executor,
        react=react_executor,
        python=python_executor,
    )
    runner = TaskRunner(
        scheduler=scheduler,
        executor=executor,
        verifier=verifier,
        evidence_factory=evidence_factory,
    )
    return RuntimeV2Controller(
        scheduler=scheduler,
        runner=runner,
        run_id=run_id,
        session_id=session_id,
        on_task_start=on_task_start,
        on_task_finish=on_task_finish,
        skill_retriever=skill_retriever,
        analysis_runtime=analysis_runtime,
    )
