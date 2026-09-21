from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from langgraph_langchain.state_machine import AnalysisStage

FailureCode = Literal[
    "missing_data_file",
    "session_not_found",
    "session_workspace_missing",
    "session_expired",
    "python_execution_error",
    "max_steps_exceeded",
    "report_rejected",
    "cancelled",
    "schema_understanding_failed",
    "field_semantic_unclear",
    "tool_execution_failed",
    "reasoning_drift",
    "report_generation_failed",
    "timeout",
    "session_interrupted",
    "disk_space_exhausted",
    "memory_limit_exceeded",
    "network_timeout",
    "corrupted_data_file",
    "permission_denied",
    "missing_api_key",
    "overloaded",
    "server_error",
    "rate_limit",
    "unknown",
]

StageStatus = Literal["started", "completed", "failed"]
RecoveryAction = Literal[
    "retry_same_scope",
    "retry_narrower_scope",
    "retry_with_sampled_data",
    "user_action_required",
]


# Runtime V2 task/executor contracts.
# These types are intentionally broad enough for the first V2 scheduler and
# executors, while remaining compatible with structured plans already in use.
TaskType = Literal[
    "schema",
    "profile",
    "metric",
    "comparison",
    "trend",
    "breakdown",
    "contribution",
    "anomaly",
    "correlation",
    "statistical_test",
    "root_cause",
    "visualization",
    "report",
]

ExecutorType = Literal[
    "structured",
    "react",
    "python",
]

TaskStatus = Literal[
    "pending",
    "running",
    "succeeded",
    "failed",
    "skipped",
    "cancelled",
]

VerificationCheckType = Literal[
    "numeric_consistency",
    "time_consistency",
    "aggregation_consistency",
    "artifact_existence",
    "evidence_existence",
    "custom",
]

VerificationStatus = Literal[
    "passed",
    "failed",
    "skipped",
    "not_applicable",
]


class AnalysisTask(BaseModel):
    """A schedulable unit in Runtime V2.

    This model is the canonical schema-level contract. The existing
    ``runtime.models.AnalysisTask`` is retained for current run snapshots and
    will be migrated to this contract in the scheduler/executor commits.
    """

    task_id: str = Field(
        default_factory=lambda: f"task_{uuid4().hex}",
        description="Task unique identifier",
    )
    session_id: str = Field(..., min_length=1, description="Owning session ID")
    question: str = Field(..., min_length=1, description="What this task answers")
    task_type: TaskType = Field(default="profile", description="Analysis task type")
    executor_type: ExecutorType = Field(
        default="python",
        description="Executor that owns this task",
    )
    method: Optional[str] = Field(
        default=None,
        description="Optional tool/skill method name; for example load_data or compare_periods",
    )
    plan_step_id: Optional[str] = Field(
        default=None,
        description="Linked RuntimePlanStep ID when the task comes from a plan",
    )
    status: TaskStatus = Field(default="pending", description="Task status")
    depends_on: List[str] = Field(default_factory=list, description="Upstream task IDs")
    required_inputs: List[str] = Field(default_factory=list, description="Required input names")
    expected_outputs: List[str] = Field(default_factory=list, description="Expected output names")
    input_asset_ids: List[str] = Field(default_factory=list, description="Input dataset asset IDs")
    constraints: Dict[str, Any] = Field(default_factory=dict, description="Task constraints")
    external_context: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Read-only context supplied by callers",
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Creation timestamp in ISO format",
    )


class VerificationResult(BaseModel):
    """Structured result of checking an execution, artifact, or evidence item."""

    verification_id: str = Field(
        default_factory=lambda: f"verify_{uuid4().hex}",
        description="Verification unique identifier",
    )
    run_id: Optional[str] = Field(default=None, description="Owning run ID")
    step_id: Optional[str] = Field(default=None, description="Verified runtime step ID")
    task_id: Optional[str] = Field(default=None, description="Verified task ID")
    execution_id: Optional[str] = Field(default=None, description="Verified execution ID")
    artifact_id: Optional[str] = Field(default=None, description="Verified artifact ID")
    evidence_id: Optional[str] = Field(default=None, description="Verified evidence ID")
    check_type: VerificationCheckType = Field(
        default="numeric_consistency",
        description="First-stage verification category",
    )
    status: VerificationStatus = Field(default="passed", description="Verification status")
    passed: bool = Field(..., description="Whether the check passed")
    expected: Optional[Any] = Field(default=None, description="Expected value or condition")
    actual: Optional[Any] = Field(default=None, description="Observed value or condition")
    tolerance: Optional[float] = Field(
        default=None,
        ge=0,
        description="Numeric tolerance used by the check",
    )
    message: str = Field(default="", description="Human-readable check result")
    details: Dict[str, Any] = Field(default_factory=dict, description="Additional check details")
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Creation timestamp in ISO format",
    )

    @model_validator(mode="after")
    def _check_status_consistency(self) -> "VerificationResult":
        if self.status == "passed" and not self.passed:
            raise ValueError("passed must be true when status is passed")
        if self.status == "failed" and self.passed:
            raise ValueError("passed must be false when status is failed")
        if self.status in {"skipped", "not_applicable"} and self.passed:
            raise ValueError("passed must be false when status is skipped or not_applicable")
        return self



class PlanStep(BaseModel):
    """
    每个 step 都要尽量自包含（因为我们在每一步里都会重新执行一段代码到同一个 workspace），
    从而避免依赖跨 step 的 Python 运行时变量状态。
    """

    index: int = Field(..., ge=0, description="step 序号，从 0 开始")
    title: str = Field(..., min_length=1, description="人类可读的步骤标题")
    code_task: str = Field(..., min_length=1, description="给 code 生成器的精确任务描述（包含需要做的分析/图表/保存文件名等）")
    expected_artifacts: Optional[List[str]] = Field(
        default=None, description="期望在 workspace 生成的文件名列表（可选）"
    )
    is_final_step: bool = Field(False, description="是否为最后一步（用于产出最终报告）")


class Plan(BaseModel):
    steps: List[PlanStep]


class CodeGen(BaseModel):
    """
    code 字段要求是“可执行的 Python 代码字符串”，要求保存产物时必须使用 WORKSPACE_DIR 拼路径。
    """

    code: str
    # 给调试/展示用的简短描述（不影响代码执行）
    summary: Optional[str] = None


class StepExecutionSummary(BaseModel):
    step_index: int
    title: str
    code_result: Optional[str] = None
    logs: Optional[str] = None
    artifacts: List[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    evidence_id: str = Field(
        default_factory=lambda: f"evidence_{uuid4().hex}",
        description="证据唯一标识",
    )
    verification_status: Literal["unverified", "verified", "failed"] = Field(
        default="unverified",
        description="Runtime verification state for this evidence",
    )
    verification_result_id: Optional[str] = Field(
        default=None,
        description="Linked VerificationResult ID",
    )
    evidence_text: str = Field(..., min_length=1, description="支撑结论的证据文本")
    source_fields: List[str] = Field(default_factory=list, description="证据涉及的字段")
    source_artifacts: List[str] = Field(default_factory=list, description="证据涉及的图表或文件")
    source_artifact_ids: List[str] = Field(default_factory=list, description="证据涉及的产物 ID")
    source_execution_ids: List[str] = Field(default_factory=list, description="产生证据的执行 ID")
    source_step_ids: List[str] = Field(default_factory=list, description="产生证据的步骤 ID")
    source_asset_ids: List[str] = Field(default_factory=list, description="证据使用的数据资产 ID")
    time_window: Optional[str] = Field(default=None, description="证据对应的时间窗口")
    group_dimension: Optional[str] = Field(default=None, description="证据涉及的分组维度")
    filters: List[str] = Field(default_factory=list, description="证据使用的过滤条件")
    stats: Optional[dict] = Field(default=None, description="关键统计数据（如 {'north_revenue': 420000, 'total_revenue': 1000000}）")
    calculation_method: Optional[str] = Field(default=None, description="计算方法说明")
    # Tracing fields
    span_id: Optional[str] = Field(default=None, description="生成此证据的 span ID")
    created_at: Optional[str] = Field(default=None, description="创建时间（ISO 格式）")


class AnalysisAssumption(BaseModel):
    assumption_text: str = Field(..., description="分析过程中显式声明的假设")
    risk_level: Literal["low", "medium", "high"] = Field(
        default="medium", description="假设风险等级"
    )


class StructuredLogEntry(BaseModel):
    """Structured log entry for analysis operations."""
    timestamp: str = Field(..., description="ISO 格式时间戳")
    session_id: str = Field(..., description="会话 ID")
    request_id: Optional[str] = Field(default=None, description="请求 ID")
    run_id: Optional[str] = Field(default=None, description="运行 ID")
    trace_id: Optional[str] = Field(default=None, description="Trace ID")
    span_id: Optional[str] = Field(default=None, description="Span ID")
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(..., description="日志级别")
    event_type: str = Field(..., description="事件类型（如 tool_call, stage_change, error）")
    stage: Optional[AnalysisStage] = Field(default=None, description="当前分析阶段")
    tool_name: Optional[str] = Field(default=None, description="工具名称")
    failure_code: Optional[FailureCode] = Field(default=None, description="失败代码")
    retry_count: Optional[int] = Field(default=None, description="重试次数")
    duration_ms: Optional[float] = Field(default=None, description="操作耗时（毫秒）")
    token_count: Optional[int] = Field(default=None, description="Token 消耗")
    cost_usd: Optional[float] = Field(default=None, description="成本（美元）")
    message: str = Field(..., description="日志消息")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="额外元数据")


class RunMetrics(BaseModel):
    """Run-level metrics for analysis session."""
    session_id: str
    request_id: Optional[str] = None
    run_id: Optional[str] = None
    start_time: str = Field(..., description="开始时间（ISO 格式）")
    end_time: Optional[str] = Field(default=None, description="结束时间（ISO 格式）")
    duration_seconds: Optional[float] = Field(default=None, description="总耗时（秒）")
    total_steps: int = Field(default=0, description="总步数")
    total_tokens: Optional[int] = Field(default=None, description="总 token 消耗")
    total_cost_usd: Optional[float] = Field(default=None, description="总成本（美元）")
    stage_durations: Dict[str, float] = Field(default_factory=dict, description="各阶段耗时（秒）")
    tool_call_counts: Dict[str, int] = Field(default_factory=dict, description="工具调用次数统计")
    failure_count: int = Field(default=0, description="失败次数")
    retry_count: int = Field(default=0, description="重试次数")
    final_status: Literal["completed", "failed", "cancelled", "timeout"] = Field(..., description="最终状态")
    failure_code: Optional[FailureCode] = Field(default=None, description="失败代码（如果失败）")


class MetricDefinition(BaseModel):
    metric_name: str = Field(..., description="指标名")
    definition_text: str = Field(..., description="指标定义说明")
    time_window: Optional[str] = Field(default=None, description="指标对应时间窗口")
    dedup_rule: Optional[str] = Field(default=None, description="去重规则")
    denominator: Optional[str] = Field(default=None, description="分母定义")
    semantic_uncertainty: Optional[str] = Field(default=None, description="字段语义或口径不确定性")


class Finding(BaseModel):
    finding_id: str = Field(..., min_length=1, description="结论唯一标识，如 F001")
    statement: str = Field(..., min_length=1, description="核心结论陈述")
    evidence: List[EvidenceItem] = Field(default_factory=list, description="支撑结论的证据")
    assumptions: List[AnalysisAssumption] = Field(default_factory=list, description="相关假设")
    metric_definitions: List[MetricDefinition] = Field(default_factory=list, description="相关指标定义")
    confidence_level: Literal["low", "medium", "high"] = Field(
        default="medium", description="对结论的置信程度"
    )
    evidence_level: Literal["A", "B", "C"] = Field(
        default="B", description="证据等级：A=事实描述，B=相关线索，C=因果判断"
    )
    hypothesis_flag: bool = Field(False, description="是否属于假设性结论")
    category: Optional[str] = Field(default=None, description="结论类别，如 trend/anomaly/comparison/velocity/quality/delivery")
    finding_type: Optional[TaskType] = Field(
        default=None,
        description="Runtime V2 structured conclusion type",
    )
    supported_by: List[str] = Field(
        default_factory=list,
        description="Evidence IDs that support this finding",
    )
    depends_on: List[str] = Field(
        default_factory=list,
        description="Upstream finding IDs used to derive this finding",
    )
    stats: Optional[Dict[str, Any]] = Field(default=None, description="关键统计数据")
    calculation_method: Optional[str] = Field(default=None, description="计算方法")
    # Tracing fields
    trace_id: Optional[str] = Field(default=None, description="追踪 ID")
    span_id: Optional[str] = Field(default=None, description="生成此结论的 span ID")
    source_tool: Optional[str] = Field(default=None, description="生成此结论的工具名称")
    source_step: Optional[int] = Field(default=None, description="生成此结论的步骤编号")
    run_id: Optional[str] = Field(default=None, description="所属运行 ID")
    recorded_by_execution_id: Optional[str] = Field(default=None, description="记录 Finding 的执行 ID")
    recorded_by_step_id: Optional[str] = Field(default=None, description="记录 Finding 的步骤 ID")
    created_at: Optional[str] = Field(default=None, description="创建时间（ISO 格式）")

    @model_validator(mode="after")
    def _check_evidence_consistency(self) -> "Finding":
        """High-confidence or causal claims require concrete evidence."""
        if self.confidence_level == "high" and not self.evidence:
            import warnings
            warnings.warn(
                f"Finding {self.finding_id}: high confidence but no evidence items provided.",
                stacklevel=2,
            )
        if self.evidence_level == "C" and self.hypothesis_flag is False:
            # Causal claims (level C) should be explicitly flagged as hypotheses
            import warnings
            warnings.warn(
                f"Finding {self.finding_id}: evidence_level C (causal) should set hypothesis_flag=True.",
                stacklevel=2,
            )
        return self


class FailureInfo(BaseModel):
    code: FailureCode
    message: str = Field(..., min_length=1)
    retryable: bool = False
    hint: Optional[str] = None
    recovery_action: Optional[RecoveryAction] = None
    stage: Optional[AnalysisStage] = None


class StageResult(BaseModel):
    stage: AnalysisStage
    status: StageStatus
    failure: Optional[FailureInfo] = None
    started_at: Optional[datetime] = Field(default=None, description="Stage start time")
    completed_at: Optional[datetime] = Field(default=None, description="Stage completion time")


class FinalReport(BaseModel):
    report_markdown: str
    findings: List[Finding] = Field(default_factory=list)
    assumptions: List[AnalysisAssumption] = Field(default_factory=list)
    metric_definitions: List[MetricDefinition] = Field(default_factory=list)


class ArtifactRef(BaseModel):
    """Reference to an artifact (file, chart, table)."""
    artifact_id: str = Field(..., description="Artifact 唯一标识")
    artifact_type: Literal["chart", "table", "file", "data"] = Field(..., description="Artifact 类型")
    file_path: Optional[str] = Field(default=None, description="文件路径")
    description: Optional[str] = Field(default=None, description="Artifact 描述")
    # Lineage fields
    created_by_tool: Optional[str] = Field(default=None, description="创建此 artifact 的工具")
    created_by_step: Optional[int] = Field(default=None, description="创建此 artifact 的步骤编号")
    created_at: Optional[str] = Field(default=None, description="创建时间（ISO 格式）")
    span_id: Optional[str] = Field(default=None, description="创建此 artifact 的 span ID")
    referenced_by_findings: List[str] = Field(default_factory=list, description="引用此 artifact 的 finding IDs")


class ConclusionTrace(BaseModel):
    """Trace from conclusion to evidence to artifacts."""
    finding_id: str = Field(..., description="结论 ID")
    finding_statement: str = Field(..., description="结论陈述")
    evidence_items: List[EvidenceItem] = Field(default_factory=list, description="证据列表")
    artifacts: List[ArtifactRef] = Field(default_factory=list, description="相关 artifacts")
    source_steps: List[int] = Field(default_factory=list, description="来源步骤编号")
    trace_path: str = Field(..., description="追踪路径描述")


class SessionLineage(BaseModel):
    """Complete lineage for an analysis session."""
    session_id: str
    request_id: Optional[str] = None
    run_id: Optional[str] = None
    user_question: Optional[str] = Field(default=None, description="用户问题")
    data_source: Optional[str] = Field(default=None, description="数据源")
    # Artifacts
    artifacts: List[ArtifactRef] = Field(default_factory=list, description="所有生成的 artifacts")
    # Findings
    findings: List[Finding] = Field(default_factory=list, description="所有 findings")
    # Traces
    conclusion_traces: List[ConclusionTrace] = Field(default_factory=list, description="结论追踪链路")
    # Report
    final_report: Optional[str] = Field(default=None, description="最终报告")
    report_references_findings: List[str] = Field(default_factory=list, description="报告引用的 finding IDs")
