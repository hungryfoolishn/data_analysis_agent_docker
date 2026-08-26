"""Pure state and HTML helpers for the Streamlit analysis workbench."""

from __future__ import annotations

import html
from copy import deepcopy
from typing import Any
from urllib.parse import urlparse


STATUS_LABELS = {
    "pending": "等待",
    "running": "运行中",
    "succeeded": "完成",
    "needs_revision": "待修正",
    "failed": "失败",
    "skipped": "跳过",
    "completed": "已完成",
    "cancelled": "已取消",
    "paused": "已暂停",
    "awaiting_confirmation": "待确认",
    "confirmed": "已确认",
    "active": "执行中",
}


def empty_workbench_state() -> dict[str, Any]:
    return {
        "task_id": None,
        "run_id": None,
        "run_status": "pending",
        "plan_status": "active",
        "plan_version": 1,
        "plan_revisions": [],
        "plan_confirmation": None,
        "pause_reason": None,
        "semantic_provider": None,
        "semantic_context_version": None,
        "question": "",
        "steps": [],
        "assets": [],
        "executions": [],
        "artifacts": [],
        "findings": [],
        "quality": None,
        "task_status": "idle",
        "event_cursor": 0,
    }


def _merge_by_id(items: list[dict], incoming: list[dict], key: str) -> list[dict]:
    merged = [deepcopy(item) for item in items]
    positions = {item.get(key): index for index, item in enumerate(merged) if item.get(key)}
    for item in incoming:
        item_id = item.get(key)
        if item_id and item_id in positions:
            merged[positions[item_id]].update(deepcopy(item))
        else:
            if item_id:
                positions[item_id] = len(merged)
            merged.append(deepcopy(item))
    return merged


def apply_analysis_event(
    state: dict[str, Any],
    event: dict[str, Any] | None,
    artifacts: list[dict] | None = None,
) -> dict[str, Any]:
    """Reduce one additive SSE event into stable workbench state."""
    updated = deepcopy(state or empty_workbench_state())
    event = event or {}
    for key in (
        "task_id",
        "run_id",
        "run_status",
        "plan_status",
        "plan_version",
        "semantic_provider",
        "semantic_context_version",
    ):
        if event.get(key) is not None:
            updated[key] = event[key]
    step = event.get("step")
    if isinstance(step, dict) and step.get("step_id"):
        updated["steps"] = _merge_by_id(updated.get("steps", []), [step], "step_id")
    if artifacts:
        updated["artifacts"] = _merge_by_id(
            updated.get("artifacts", []), artifacts, "artifact_id"
        )
    return updated


def apply_runtime_snapshot(
    state: dict[str, Any], snapshot: dict[str, Any] | None
) -> dict[str, Any]:
    """Merge the authoritative persisted runtime snapshot into UI state."""
    updated = deepcopy(state or empty_workbench_state())
    if not snapshot:
        return updated
    task = snapshot.get("task") or {}
    run = snapshot.get("run") or {}
    updated.update(
        {
            "task_id": task.get("task_id") or updated.get("task_id"),
            "run_id": run.get("run_id") or updated.get("run_id"),
            "run_status": run.get("status") or updated.get("run_status"),
            "plan_status": run.get("plan_status") or updated.get("plan_status"),
            "plan_version": run.get("plan_version") or updated.get("plan_version", 1),
            "plan_revisions": deepcopy(run.get("plan_revisions") or []),
            "plan_confirmation": deepcopy(run.get("plan_confirmation")),
            "pause_reason": run.get("pause_reason"),
            "question": task.get("question") or updated.get("question", ""),
            "semantic_provider": (task.get("external_context") or {}).get("provider"),
            "semantic_context_version": (task.get("external_context") or {}).get(
                "context_version"
            ),
            "steps": deepcopy(run.get("steps") or updated.get("steps", [])),
            "assets": deepcopy(snapshot.get("assets") or updated.get("assets", [])),
            "executions": deepcopy(
                snapshot.get("executions") or updated.get("executions", [])
            ),
            "findings": deepcopy(
                snapshot.get("findings") or updated.get("findings", [])
            ),
        }
    )
    updated["artifacts"] = _merge_by_id(
        updated.get("artifacts", []), snapshot.get("artifacts") or [], "artifact_id"
    )
    return updated


def resolve_artifact_url(file_url: str, file_server_base: str) -> str:
    if not file_url:
        return ""
    if file_url.startswith(("http://", "https://")):
        path = urlparse(file_url).path
    else:
        path = file_url if file_url.startswith("/") else f"/{file_url}"
    return f"{file_server_base.rstrip('/')}{path}" if file_server_base else path


def render_run_summary_html(state: dict[str, Any]) -> str:
    steps = state.get("steps", [])
    completed = sum(step.get("status") == "succeeded" for step in steps)
    revisions = sum(step.get("status") == "needs_revision" for step in steps)
    failed = sum(step.get("status") == "failed" for step in steps)
    status = state.get("run_status") or "pending"
    label = STATUS_LABELS.get(status, status)
    run_id = html.escape(str(state.get("run_id") or "尚未开始"))
    semantic = " / ".join(
        str(item)
        for item in (
            state.get("semantic_provider"),
            state.get("semantic_context_version"),
        )
        if item
    ) or "本地 Schema"
    plan_status = str(state.get("plan_status") or "active")
    plan_label = STATUS_LABELS.get(plan_status, plan_status)
    plan_version = int(state.get("plan_version") or 1)
    return (
        '<div class="run-summary">'
        f'<div><span class="summary-label">运行状态</span><strong>{html.escape(label)}</strong></div>'
        f'<div><span class="summary-label">计划</span><strong>v{plan_version} · {html.escape(plan_label)}</strong></div>'
        f'<div><span class="summary-label">步骤</span><strong>{completed}/{len(steps)}</strong></div>'
        f'<div><span class="summary-label">修正</span><strong>{revisions}</strong></div>'
        f'<div><span class="summary-label">失败</span><strong>{failed}</strong></div>'
        f'<div><span class="summary-label">语义上下文</span><strong>{html.escape(semantic)}</strong></div>'
        f'<div class="run-id"><span class="summary-label">Run ID</span><code>{run_id}</code></div>'
        "</div>"
    )


def render_steps_html(steps: list[dict]) -> str:
    if not steps:
        return '<div class="empty-state">分析开始后，步骤会在这里实时出现。</div>'
    rows = []
    for index, step in enumerate(steps, start=1):
        status = str(step.get("status") or "pending")
        objective = html.escape(str(step.get("objective") or step.get("method") or "分析步骤"))
        method = html.escape(str(step.get("method") or ""))
        label = html.escape(STATUS_LABELS.get(status, status))
        error = html.escape(str(step.get("error") or ""))
        error_html = f'<div class="step-error">{error}</div>' if error else ""
        rows.append(
            f'<div class="step-row step-{html.escape(status)}">'
            f'<div class="step-index">{index}</div>'
            '<div class="step-copy">'
            f'<div class="step-title">{objective}</div>'
            f'<div class="step-meta"><code>{method}</code><span>{label}</span></div>'
            f"{error_html}</div></div>"
        )
    return '<div class="step-list">' + "".join(rows) + "</div>"


def render_artifacts_html(
    artifacts: list[dict],
    file_server_base: str,
    *,
    run_id: str | None = None,
    run_status: str | None = None,
) -> str:
    package_available = bool(run_id and run_status == "completed")
    if not artifacts and not package_available:
        return '<div class="empty-state">图表、表格和报告将在生成后集中显示。</div>'
    rows = []
    if package_available:
        package_path = f"/analysis/runs/{run_id}/package"
        rebuild_path = f"/analysis/runs/{run_id}/report/rebuild"
        package_url = (
            f"{file_server_base.rstrip('/')}{package_path}"
            if file_server_base else package_path
        )
        rebuild_url = (
            f"{file_server_base.rstrip('/')}{rebuild_path}"
            if file_server_base else rebuild_path
        )
        rows.append(
            '<div class="artifact-row analysis-package-row">'
            '<div><div class="artifact-name">可复现分析包</div>'
            '<div class="artifact-meta">zip · task / data / execution / evidence / report</div></div>'
            '<div class="artifact-actions">'
            f'<a href="{html.escape(rebuild_url, quote=True)}" target="_blank">重建报告</a>'
            f'<a href="{html.escape(package_url, quote=True)}" target="_blank">下载分析包</a>'
            '</div></div>'
        )
    for artifact in artifacts:
        name = html.escape(str(artifact.get("name") or "未命名产物"))
        artifact_type = html.escape(str(artifact.get("artifact_type") or "file"))
        tool = html.escape(str(artifact.get("created_by_tool") or "analysis"))
        url = html.escape(
            resolve_artifact_url(str(artifact.get("url") or ""), file_server_base),
            quote=True,
        )
        action = f'<a href="{url}" target="_blank">打开</a>' if url else ""
        rows.append(
            '<div class="artifact-row">'
            f'<div><div class="artifact-name">{name}</div>'
            f'<div class="artifact-meta">{artifact_type} · {tool}</div></div>{action}</div>'
        )
    return '<div class="artifact-list">' + "".join(rows) + "</div>"


def render_assets_html(assets: list[dict]) -> str:
    if not assets:
        return '<div class="empty-state compact">尚无已识别的数据资产。</div>'
    rows = []
    for asset in assets:
        name = html.escape(str(asset.get("name") or "数据资产"))
        schema = asset.get("schema_snapshot") or {}
        rows_count = int(schema.get("row_count") or 0)
        columns_count = int(schema.get("column_count") or 0)
        time_columns = ", ".join(str(item) for item in schema.get("time_columns") or [])
        time_text = html.escape(time_columns or "未识别时间字段")
        rows.append(
            '<div class="asset-row">'
            f'<div class="asset-name">{name}</div>'
            f'<div class="asset-meta">{rows_count:,} 行 · {columns_count} 列</div>'
            f'<div class="asset-fields">{time_text}</div></div>'
        )
    return '<div class="asset-list">' + "".join(rows) + "</div>"


def render_findings_html(
    findings: list[dict],
    file_server_base: str,
    run_id: str | None,
) -> str:
    if not findings:
        return '<div class="empty-state compact">记录 Finding 后可在此反查证据血缘。</div>'
    rows = []
    for finding in findings:
        finding_id = str(finding.get("finding_id") or "")
        statement = html.escape(str(finding.get("statement") or "未命名结论"))
        evidence = finding.get("evidence") or []
        execution_ids = {
            execution_id
            for item in evidence
            for execution_id in item.get("source_execution_ids") or []
        }
        artifact_ids = {
            artifact_id
            for item in evidence
            for artifact_id in item.get("source_artifact_ids") or []
        }
        asset_ids = {
            asset_id
            for item in evidence
            for asset_id in item.get("source_asset_ids") or []
        }
        detail_path = f"/analysis/runs/{run_id}/findings/{finding_id}" if run_id and finding_id else ""
        detail_url = (
            f"{file_server_base.rstrip('/')}{detail_path}"
            if file_server_base and detail_path else detail_path
        )
        action = (
            f'<a href="{html.escape(detail_url, quote=True)}" target="_blank">查看血缘</a>'
            if detail_url else ""
        )
        rows.append(
            '<div class="finding-row">'
            f'<div><div class="finding-id">{html.escape(finding_id)}</div>'
            f'<div class="finding-statement">{statement}</div>'
            f'<div class="finding-meta">{len(evidence)} 条证据 · {len(execution_ids)} 次执行 · '
            f'{len(asset_ids)} 个数据版本 · {len(artifact_ids)} 个产物</div></div>{action}</div>'
        )
    return '<div class="finding-list">' + "".join(rows) + "</div>"


def render_executions_html(executions: list[dict]) -> str:
    if not executions:
        return '<div class="empty-state">执行代码、耗时和错误将在工具运行后显示。</div>'
    rows = []
    for execution in reversed(executions):
        tool = html.escape(str(execution.get("tool_name") or "tool"))
        status = str(execution.get("status") or "pending")
        label = html.escape(STATUS_LABELS.get(status, status))
        duration = float(execution.get("duration_ms") or 0.0)
        code = html.escape(str(execution.get("code_or_query") or "无代码或查询记录"))
        output = html.escape(str(execution.get("stdout_preview") or ""))
        error = execution.get("error") or {}
        error_text = html.escape(str(error.get("message") or ""))
        detail = f'<pre>{code}</pre>'
        if output:
            detail += f'<div class="execution-output">输出摘要</div><pre>{output}</pre>'
        if error_text:
            error_class = "execution-feedback" if status == "needs_revision" else "execution-error"
            detail += f'<div class="{error_class}">{error_text}</div>'
        rows.append(
            '<details class="execution-row">'
            f'<summary><code>{tool}</code><span>{label}</span><span>{duration:.0f} ms</span></summary>'
            f'<div class="execution-detail">{detail}</div></details>'
        )
    return '<div class="execution-list">' + "".join(rows) + "</div>"


def render_quality_html(result: dict[str, Any] | None) -> str:
    if not result:
        return '<div class="empty-state compact">尚未执行分析质量检查。</div>'
    status = "通过" if result.get("passed") else "未通过"
    issues = result.get("issues") or []
    rows = "".join(
        f'<div class="quality-{html.escape(str(item.get("severity") or "warning"))}">'
        f'{html.escape(str(item.get("code") or "issue"))}: {html.escape(str(item.get("message") or ""))}</div>'
        for item in issues
    )
    return f'<div class="quality-summary"><strong>质量门禁：{status}</strong><span>检查 {int(result.get("checked_findings") or 0)} 条 Finding</span>{rows}</div>'
