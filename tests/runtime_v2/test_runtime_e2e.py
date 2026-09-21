from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from langgraph_langchain.execution.structured_executor import StructuredTaskExecutor
from langgraph_langchain.langgraph_agent import (
    _complete_runtime_step,
    _make_tools,
    _Session,
    _skills_loader,
)
from langgraph_langchain.runtime.executor import TaskExecutor
from langgraph_langchain.runtime.graph import RuntimeV2Controller
from langgraph_langchain.runtime.models import RuntimePlanStep
from langgraph_langchain.runtime.plans import AnalysisPlan
from langgraph_langchain.runtime.skill_retriever import SkillRetriever
from langgraph_langchain.state_machine import AnalysisStage
from langgraph_langchain.verification import verify_execution_result


QUESTION = (
    "分析各部门销售额同比变化，找出下降最大的部门，并说明主要贡献因素。"
)


def write_grouped_sales(workspace: Path) -> Path:
    source = workspace / "grouped_sales.csv"
    rows = [
        ("date", "department", "units", "revenue"),
        ("2026-01-05", "East", "120", "36000"),
        ("2026-01-12", "West", "90", "27000"),
        ("2026-01-19", "North", "80", "24000"),
        ("2026-02-03", "East", "140", "42000"),
        ("2026-02-10", "West", "100", "30000"),
        ("2026-02-17", "North", "45", "13500"),
    ]
    source.write_text("\n".join(",".join(row) for row in rows) + "\n", encoding="utf-8")
    return source


def build_plan() -> AnalysisPlan:
    load_step = RuntimePlanStep(
        objective="Load and inspect grouped sales data",
        method="load_data",
        expected_outputs=["schema_snapshot"],
    )
    metric_step = RuntimePlanStep(
        objective="Declare revenue metric definition",
        method="declare_metric",
        expected_outputs=["metric_definition"],
        depends_on=[load_step.step_id],
    )
    eda_step = RuntimePlanStep(
        objective="Profile sales data quality and distributions",
        method="eda_profile",
        expected_outputs=["eda_profile"],
        depends_on=[metric_step.step_id],
    )
    period_step = RuntimePlanStep(
        objective="Compare monthly sales periods",
        method="analyze_time_trend",
        expected_outputs=["period_comparison"],
        depends_on=[eda_step.step_id],
    )
    group_step = RuntimePlanStep(
        objective="Compare sales across departments",
        method="compare_groups",
        expected_outputs=["group_comparison"],
        depends_on=[eda_step.step_id],
    )
    contribution_step = RuntimePlanStep(
        objective="Decompose monthly sales change by department",
        method="decompose_contribution",
        expected_outputs=["contribution_decomposition"],
        depends_on=[eda_step.step_id],
    )
    finding_east = RuntimePlanStep(
        objective="Record East department sales finding",
        method="record_finding",
        expected_outputs=["finding"],
        depends_on=[period_step.step_id, group_step.step_id],
    )
    finding_west = RuntimePlanStep(
        objective="Record West department sales finding",
        method="record_finding",
        expected_outputs=["finding"],
        depends_on=[period_step.step_id, group_step.step_id],
    )
    finding_north = RuntimePlanStep(
        objective="Record North department decline and contribution finding",
        method="record_finding",
        expected_outputs=["finding"],
        depends_on=[period_step.step_id, contribution_step.step_id],
    )
    report_step = RuntimePlanStep(
        objective="Generate the final evidence-backed report",
        method="finish_report",
        expected_outputs=["report"],
        depends_on=[
            finding_east.step_id,
            finding_west.step_id,
            finding_north.step_id,
        ],
    )
    return AnalysisPlan(
        goal=QUESTION,
        success_criteria=[
            "All conclusions are backed by verified execution evidence",
            "The report identifies the department with the largest sales decline",
        ],
        metrics=["revenue"],
        dimensions=["department"],
        time_field="date",
        steps=[
            load_step,
            metric_step,
            eda_step,
            period_step,
            group_step,
            contribution_step,
            finding_east,
            finding_west,
            finding_north,
            report_step,
        ],
    )


def task_arguments(task) -> dict[str, Any]:
    if task.method == "load_data":
        return {}
    if task.method == "declare_metric":
        return {
            "metric_name": "revenue",
            "definition_text": "Sum of revenue by department and month",
            "time_window": "2026-01 to 2026-02",
            "dedup_rule": "No deduplication; source rows are already transaction-level aggregates",
            "semantic_uncertainty": "Excludes returns and channel/timing adjustments",
        }
    if task.method == "eda_profile":
        return {"max_numeric_cols": 5, "max_cat_cols": 5}
    if task.method == "analyze_time_trend":
        return {
            "date_field": "date",
            "metric": "revenue",
            "frequency": "month",
            "aggregation": "sum",
        }
    if task.method == "compare_groups":
        return {
            "dimension": "department",
            "metric": "revenue",
            "aggregation": "sum",
        }
    if task.method == "decompose_contribution":
        return {
            "date_field": "date",
            "dimension": "department",
            "metric": "revenue",
            "frequency": "month",
        }
    if task.method == "record_finding":
        # The exact finding text is selected in on_task_start because the three
        # tasks share one structured method but have different objectives.
        return {}
    if task.method == "finish_report":
        return {}
    raise AssertionError(f"Unexpected task method: {task.method}")


def finding_arguments(
    statement: str,
    evidence_text: str,
    category: str,
    stats: dict[str, Any],
) -> dict[str, Any]:
    return {
        "statement": statement,
        "evidence_text": evidence_text,
        "confidence_level": "medium",
        "evidence_level": "A",
        "category": category,
        "source_fields": ["date", "department", "revenue"],
        "group_dimension": "department",
        "stats": stats,
        "calculation_method": "sum(revenue) grouped by department/month",
    }


def build_report(session) -> str:
    findings = session.findings
    assert len(findings) >= 3
    north = next(item for item in findings if "North" in item.statement)
    east = next(item for item in findings if "East" in item.statement)
    west = next(item for item in findings if "West" in item.statement)
    lines = [
        "# 部门销售额同比变化分析报告",
        "",
        "## Summary / 摘要",
        "",
        f"**分析任务**:{QUESTION}",
        "",
        "总销售收入从 2026-01 的 87000 降至 2026-02 的 85500，变化量为 -1500。",
        "本报告只使用 Runtime 执行、验证并生成 Evidence 的结构化发现。",
        "",
        "## Data Context / 数据说明",
        "",
        "- 时间范围:2026-01 至 2026-02。",
        "- 指标定义:revenue 为销售金额，按月份和部门求和。",
        "- 去重规则:原始行级数据，无重复去重。",
        "- 分析假设:未考虑退货、汇率和渠道口径差异；该口径存在不确定性。",
        "",
        "## Key Findings / 关键发现",
        "",
        f"- {east.statement} 证据:{east.evidence[0].evidence_text}",
        f"  - 证据等级:{east.evidence_level}；置信度:{east.confidence_level}",
        f"- {west.statement} 证据:{west.evidence[0].evidence_text}",
        f"  - 证据等级:{west.evidence_level}；置信度:{west.confidence_level}",
        f"- {north.statement} 证据:{north.evidence[0].evidence_text}",
        f"  - 贡献分解:North contribution=-10500；East contribution=6000；West contribution=3000。",
        f"  - 证据等级:{north.evidence_level}；置信度:{north.confidence_level}。",
        f"  - 不确定性:结论仅描述 2026-01 至 2026-02 的确定性对比，属于假设性业务解释，不做因果断言。",
        "",
        "## Data Quality / 数据质量",
        "",
        "- 输入数据共 6 行，100.0% 的行进入分析。",
        "- date、department、units、revenue 四个字段均无缺失值，缺失比例为 0.0%。",
        "- 未发现阻断分析的异常值；如需更强结论，应使用 IQR 界限复检。",
        "",
        "## Analysis / 分析过程",
        "",
        "- 先执行 schema 理解、指标声明和 EDA，再执行月度对比、部门拆分和贡献分解。",
        "- Revenue 在 2026-01 至 2026-02 的变化为 -1500。",
        "- North 的贡献分解为 -10500，是最大下降贡献；East 和 West 分别贡献 6000 和 3000。",
        "- 该解释基于分组贡献和显式不确定性边界，未做因果断言。",
        "",
        "## Visualizations / 图表说明",
        "",
        "- 本确定性 E2E 未生成图表；结论直接引用结构化执行结果。",
        "",
        "## Recommendations / 建议",
        "",
        "- 复核 North 部门 2026-02 销售下降和相关运行假设。",
        "- 观察后续月份的数据，验证该下降是否持续。",
        "- 在确认数据口径后，再针对 North 制定恢复计划。",
        "",
    ]
    return "\n".join(lines)


def test_runtime_v2_grouped_sales_e2e(tmp_path: Path):
    workspace = tmp_path / "runtime_v2_e2e"
    workspace.mkdir(parents=True)
    source = write_grouped_sales(workspace)

    session = _Session(
        workspace_dir=str(workspace),
        source_path=str(source),
        session_id="runtime_v2_e2e",
        user_question=QUESTION,
    )
    runtime = session.analysis_runtime
    assert runtime is not None

    plan = build_plan()
    runtime.initialize_plan(
        plan,
        max_running_tasks=1,
        argument_defaults_by_method={
            "load_data": {"file_path": str(source), "sheet_name": ""},
        },
    )

    # Task-specific structured arguments are part of the AnalysisTask contract.
    # Preserve Runtime-injected defaults (e.g. load_data's source path) and only
    # fill in task arguments that the planner has not already supplied.
    for task in runtime.scheduler.tasks:
        arguments = task.constraints.get("arguments") or task_arguments(task)
        if arguments:
            task.constraints["arguments"] = arguments

    tools = _make_tools(session)
    tool_map = {tool.name: tool for tool in tools}
    executed_results: dict[str, Any] = {}

    def on_task_start(task) -> None:
        session.current_runtime_step_id = task.plan_step_id or task.task_id
        if task.method == "load_data":
            session.state_machine.current_stage = AnalysisStage.SCHEMA_UNDERSTANDING
        elif task.method == "declare_metric":
            session.state_machine.current_stage = AnalysisStage.SCHEMA_UNDERSTANDING
        elif task.method == "eda_profile":
            session.state_machine.current_stage = AnalysisStage.DATA_QUALITY_CHECK
        elif task.method in {"analyze_time_trend", "compare_groups", "decompose_contribution"}:
            session.state_machine.current_stage = AnalysisStage.DEEP_DIVE
        elif task.method == "record_finding":
            session.state_machine.current_stage = AnalysisStage.CONCLUSION_SYNTHESIS
            if not task.constraints.get("arguments"):
                if task.question.startswith("Record East"):
                    args = finding_arguments(
                        "East department revenue increased by 6000.",
                        "East revenue increased from 36000 in 2026-01 to 42000 in 2026-02.",
                        "comparison",
                        {"previous_revenue": 36000, "current_revenue": 42000, "change": 6000},
                    )
                elif task.question.startswith("Record West"):
                    args = finding_arguments(
                        "West department revenue increased by 3000.",
                        "West revenue increased from 27000 in 2026-01 to 30000 in 2026-02.",
                        "comparison",
                        {"previous_revenue": 27000, "current_revenue": 30000, "change": 3000},
                    )
                else:
                    args = finding_arguments(
                        "North department had the largest revenue decline of 10500.",
                        "North revenue declined from 24000 in 2026-01 to 13500 in 2026-02; contribution is -10500.",
                        "contribution",
                        {"previous_revenue": 24000, "current_revenue": 13500, "change": -10500},
                    )
                task.constraints["arguments"] = args
        elif task.method == "finish_report":
            session.state_machine.current_stage = AnalysisStage.CONCLUSION_SYNTHESIS
            session.current_stage = AnalysisStage.CONCLUSION_SYNTHESIS
            task.constraints["arguments"] = {
                "markdown": build_report(session),
            }

    def on_task_finish(result) -> None:
        executed_results[result.task_id] = result
        runtime.record_execution_result(result)
        _complete_runtime_step(
            session,
            result.step_id or result.task_id,
            status=result.status,
            error=(
                result.error.get("message")
                if isinstance(result.error, dict) and result.error.get("message")
                else None
            ),
        )
        session.total_steps += 1
        if result.status == "succeeded":
            if result.tool_name == "load_data":
                session.complete_stage("schema_understanding")
            elif result.tool_name == "declare_metric":
                session.state_machine.current_stage = AnalysisStage.SCHEMA_UNDERSTANDING
                session.current_stage = AnalysisStage.SCHEMA_UNDERSTANDING
            elif result.tool_name == "eda_profile":
                session.complete_stage("data_quality_check")
            elif result.tool_name in {
                "analyze_time_trend",
                "compare_groups",
                "decompose_contribution",
            }:
                session.state_machine.current_stage = AnalysisStage.DEEP_DIVE
                session.current_stage = AnalysisStage.DEEP_DIVE
            elif result.tool_name == "record_finding":
                session.state_machine.current_stage = AnalysisStage.CONCLUSION_SYNTHESIS
                session.current_stage = AnalysisStage.CONCLUSION_SYNTHESIS

    executor = TaskExecutor(
        structured=StructuredTaskExecutor(tool_resolver=lambda name: tool_map.get(name)),
    )
    runtime.configure_executor(
        executor,
        verifier=lambda result: verify_execution_result(
            result,
            artifacts=runtime.artifacts,
            workspace_dir=runtime.workspace_dir,
            context={
                "run_id": result.run_id,
                "task_id": result.task_id,
                "step_id": result.step_id,
            },
        ),
        evidence_factory=lambda result, verifications: __import__(
            "langgraph_langchain.evidence", fromlist=["EvidenceCollector"]
        ).EvidenceCollector().collect(
            execution=result,
            verification_results=verifications,
            artifacts=runtime.artifacts,
        ),
    )

    controller = RuntimeV2Controller(
        scheduler=runtime.scheduler,
        runner=runtime.runner,
        run_id=runtime.run.run_id,
        session_id=runtime.session_id,
        on_task_start=on_task_start,
        on_task_finish=on_task_finish,
        skill_retriever=SkillRetriever(_skills_loader),
        analysis_runtime=runtime,
    )

    controller.graph.invoke({})

    # Scheduler and Run completed.
    assert runtime.status == "completed"
    assert runtime.run.status == "completed"
    assert all(task.status == "succeeded" for task in runtime.scheduler.tasks)
    assert [step.status for step in runtime.run.steps] == ["succeeded"] * 10

    # Every planned task produced one deduplicated execution result.
    task_ids = [task.task_id for task in runtime.scheduler.tasks]
    assert [result.task_id for result in runtime.executions] == task_ids

    # Artifact-producing tasks have existing files and passing verification.
    artifact_results = [
        result for result in runtime.executions if result.output_artifact_ids
    ]
    assert artifact_results
    for result in artifact_results:
        assert result.status == "succeeded"
        assert result.verification_results
        assert all(item.passed is True for item in result.verification_results)
        for artifact_id in result.output_artifact_ids:
            artifact = runtime.artifacts[artifact_id]
            assert Path(artifact.path).is_file()

    # Evidence and findings are present in the Runtime lineage.
    assert runtime.evidence
    assert all(item.verification_status == "verified" for item in runtime.evidence)
    assert len(session.metric_definitions) == 1
    assert len(session.findings) == 3
    assert len(runtime.findings) == 3
    assert {item["statement"] for item in runtime.findings} == {
        item.statement for item in session.findings
    }

    # The report task produced the real report artifact.
    report_result = next(
        result for result in runtime.executions if result.tool_name == "finish_report"
    )
    assert report_result.status == "succeeded"
    report_ids = report_result.output_artifact_ids
    assert report_ids
    report_artifact = runtime.artifacts[report_ids[0]]
    assert Path(report_artifact.path).is_file()
    assert "North department had the largest revenue decline" in Path(
        report_artifact.path
    ).read_text(encoding="utf-8")
    assert session.report is not None
    assert "North department had the largest revenue decline" in session.report

    # Provenance can be walked backwards from the North finding.
    north_finding = next(
        item for item in runtime.findings
        if "North" in item.get("statement", "")
    )
    assert north_finding["evidence"]
    north_evidence_id = north_finding["evidence"][0]["evidence_id"]
    north_evidence = next(
        item for item in runtime.evidence
        if item.evidence_id == north_evidence_id
    )
    assert north_evidence.source_execution_ids
    north_execution_id = north_evidence.source_execution_ids[0]
    north_execution = next(
        item for item in runtime.executions
        if item.execution_id == north_execution_id
    )
    assert north_execution.task_id
    assert runtime.scheduler.get_task(north_execution.task_id).status == "succeeded"

    # V8.5: every report claim can be traced through Finding → Evidence → Artifact
    # → Execution, and the mapping is published as a report artifact.
    report_claim_artifact = next(
        artifact
        for artifact in runtime.artifacts.values()
        if artifact.name == "report_claims.json"
    )
    assert Path(report_claim_artifact.path).is_file()
    report_claim_payload = json.loads(
        Path(report_claim_artifact.path).read_text(encoding="utf-8")
    )
    assert report_claim_payload["claims"]
    matched_claim = next(
        claim
        for claim in report_claim_payload["claims"]
        if "North" in claim["text"] and claim["finding_ids"]
    )
    assert matched_claim["verified"] is True
    assert matched_claim["evidence_ids"]
    lineage = next(
        item
        for item in report_claim_payload["lineages"]
        if item["claim_id"] == matched_claim["claim_id"]
    )
    assert lineage["finding_ids"] == matched_claim["finding_ids"]
    assert lineage["evidence_ids"] == matched_claim["evidence_ids"]
    assert lineage["artifact_ids"]
    assert lineage["execution_ids"]
    assert lineage["complete"] is True






