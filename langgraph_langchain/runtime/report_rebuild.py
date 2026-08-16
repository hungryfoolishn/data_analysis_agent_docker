"""Deterministically reconstruct a report from persisted run evidence."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from langgraph_langchain.runtime.finding_lineage import expand_finding_lineage


def _clean(value: Any) -> str:
    return str(value or "").replace("\n", " ").strip()


def _list(values: list[Any]) -> str:
    cleaned = [_clean(value) for value in values if _clean(value)]
    return ", ".join(cleaned) if cleaned else "未记录"


def _is_data_quality_finding(finding: dict[str, Any]) -> bool:
    text = f"{finding.get('category') or ''} {finding.get('statement') or ''}".lower()
    return any(token in text for token in (
        "data_quality", "数据质量", "缺失", "重复", "异常值", "missing", "duplicate", "outlier"
    ))


def rebuild_report_markdown(snapshot: dict[str, Any]) -> str:
    """Render stable Markdown without calling an LLM or reading the old report."""
    task = snapshot.get("task") or {}
    run = snapshot.get("run") or {}
    assets = snapshot.get("assets") or []
    findings = snapshot.get("findings") or []
    metrics = snapshot.get("metric_definitions") or []
    assumptions = snapshot.get("assumptions") or []
    artifacts = snapshot.get("artifacts") or []
    if not findings:
        raise ValueError("Run has no structured findings to rebuild a report from")

    lineage = {
        finding.get("finding_id"): expand_finding_lineage(snapshot, finding.get("finding_id"))
        for finding in findings
    }
    complete_count = sum(bool(item and item.get("is_complete")) for item in lineage.values())
    time_columns = sorted({
        column
        for asset in assets
        for column in (asset.get("schema_snapshot") or {}).get("time_columns") or []
    })
    metric_windows = sorted({
        _clean(metric.get("time_window"))
        for metric in metrics
        if _clean(metric.get("time_window"))
    })

    lines = [
        "# Rebuilt Analysis Report",
        "",
        "> This report was deterministically rebuilt from the persisted Run snapshot. "
        "No language model was called and no original report text was required.",
        "",
        "## Summary",
        "",
        f"- Task: {_clean(task.get('question')) or '未记录'}",
        f"- Run: `{_clean(run.get('run_id'))}`; status: `{_clean(run.get('status'))}`; attempt: {run.get('attempt', 1)}.",
        f"- Rebuilt from {len(findings)} structured findings; {complete_count}/{len(findings)} have complete runtime lineage.",
    ]
    for finding in findings[:3]:
        lines.append(f"- {finding.get('finding_id')}: {_clean(finding.get('statement'))}")

    lines.extend(["", "## Data Context", ""])
    for asset in assets:
        schema = asset.get("schema_snapshot") or {}
        lines.append(
            f"- Asset `{_clean(asset.get('asset_id'))}`: `{_clean(asset.get('name'))}`, "
            f"{schema.get('row_count', 0)} rows x {schema.get('column_count', 0)} columns, "
            f"SHA-256 `{_clean(asset.get('content_hash')) or 'not-recorded'}`."
        )
    if not time_columns:
        lines.append("- Time range: not applicable; no datetime field was identified, so this is a static cross-sectional analysis.")
    elif metric_windows:
        lines.append(f"- Time fields: {_list(time_columns)}; declared windows: {_list(metric_windows)}.")
    else:
        lines.append(
            f"- Time fields: {_list(time_columns)}. Exact min/max bounds were not persisted, so no time boundary is inferred."
        )
    if metrics:
        lines.extend(["", "### Metric Definitions", ""])
        for metric in metrics:
            details = [f"definition={_clean(metric.get('definition_text'))}"]
            for key in ("time_window", "dedup_rule", "denominator", "semantic_uncertainty"):
                if metric.get(key):
                    details.append(f"{key}={_clean(metric.get(key))}")
            lines.append(f"- **{_clean(metric.get('metric_name'))}**: {'; '.join(details)}")
    else:
        lines.append("- Metric definitions: none persisted; metric semantics cannot be reconstructed beyond Finding evidence.")
    if assumptions:
        lines.extend(["", "### Assumptions", ""])
        for assumption in assumptions:
            lines.append(
                f"- [{_clean(assumption.get('risk_level')) or 'unknown'} risk] "
                f"{_clean(assumption.get('assumption_text'))}"
            )
    else:
        lines.append("- Assumptions: none persisted.")

    lines.extend(["", "## Key Findings", ""])
    for finding in findings:
        lines.append(
            f"- **{finding.get('finding_id')}** [{_clean(finding.get('evidence_level'))}, "
            f"confidence={_clean(finding.get('confidence_level'))}]: {_clean(finding.get('statement'))}"
        )

    quality_findings = [finding for finding in findings if _is_data_quality_finding(finding)]
    lines.extend(["", "## Data Quality", ""])
    if quality_findings:
        for finding in quality_findings:
            lines.append(f"- {finding.get('finding_id')}: {_clean(finding.get('statement'))}")
    else:
        lines.append("- No dedicated data-quality Finding was recorded; this rebuild does not infer one from narrative text.")

    lines.extend(["", "## Analysis", ""])
    for finding in findings:
        finding_id = finding.get("finding_id")
        detail = lineage.get(finding_id) or {}
        lines.extend([f"### {finding_id}", "", _clean(finding.get("statement")), ""])
        for index, evidence_detail in enumerate(detail.get("evidence_lineage") or [], start=1):
            evidence = evidence_detail.get("evidence") or {}
            lines.append(f"- Evidence {index}: {_clean(evidence.get('evidence_text'))}")
            lines.append(f"  - Fields: {_list(evidence.get('source_fields') or [])}")
            lines.append(f"  - Filters: {_list(evidence.get('filters') or [])}")
            lines.append(f"  - Calculation: {_clean(evidence.get('calculation_method')) or '未记录'}")
            lines.append(
                "  - Executions: "
                + _list([item.get("execution_id") for item in evidence_detail.get("executions") or []])
            )
            lines.append(
                "  - Artifacts: "
                + _list([item.get("name") for item in evidence_detail.get("artifacts") or []])
            )
        if detail.get("is_complete"):
            lines.append("- Lineage status: complete.")
        else:
            lines.append(f"- Lineage status: partial ({_list(detail.get('warnings') or [])}).")

    charts = [artifact for artifact in artifacts if artifact.get("artifact_type") == "chart"]
    lines.extend(["", "## Visualizations", ""])
    if charts:
        for chart in charts:
            supporting = []
            artifact_id = chart.get("artifact_id")
            for finding in findings:
                if any(
                    artifact_id in (evidence.get("source_artifact_ids") or [])
                    for evidence in finding.get("evidence") or []
                ):
                    supporting.append(finding.get("finding_id"))
            lines.append(
                f"- `{_clean(chart.get('name'))}`: supports {_list(supporting)}; "
                f"SHA-256 `{_clean(chart.get('content_hash')) or 'not-recorded'}`."
            )
    else:
        lines.append("- No chart artifact was persisted for this Run.")

    lines.extend([
        "",
        "## Recommendations",
        "",
        "- No deterministic recommendation is added unless it was recorded as a structured Finding. "
        "Review the evidence and assumptions before taking action.",
        "",
        "## Rebuild Provenance",
        "",
        f"- Run updated at: {_clean(run.get('updated_at')) or '未记录'}",
        "- Source of truth: Task, Run, Asset, Execution, Artifact, Finding, MetricDefinition, and Assumption records.",
        "- Original report content: not used.",
        "",
    ])
    return "\n".join(lines)


def write_rebuilt_report(snapshot: dict[str, Any], workspace_root: Path) -> tuple[Path, str]:
    workspace_root = workspace_root.resolve()
    session_id = _clean((snapshot.get("task") or {}).get("session_id"))
    run_id = _clean((snapshot.get("run") or {}).get("run_id"))
    session_dir = (workspace_root / session_id).resolve()
    session_dir.relative_to(workspace_root)
    if not session_dir.is_dir() or not run_id:
        raise FileNotFoundError("Run workspace no longer exists")
    report = rebuild_report_markdown(snapshot)
    report_dir = session_dir / ".analysis_rebuilds"
    report_dir.mkdir(parents=True, exist_ok=True)
    target = report_dir / f"{run_id}.md"
    fd, temp_name = tempfile.mkstemp(dir=str(report_dir), prefix=f".{run_id}_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(report)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return target, hashlib.sha256(report.encode("utf-8")).hexdigest()
