"""Resolve and export runtime-native Finding lineage."""

from __future__ import annotations

from typing import Any, Optional


def _maps(snapshot: dict[str, Any]):
    assets = {item.get("asset_id"): item for item in snapshot.get("assets", []) if item.get("asset_id")}
    steps = {item.get("step_id"): item for item in snapshot.get("run", {}).get("steps", []) if item.get("step_id")}
    executions = {item.get("execution_id"): item for item in snapshot.get("executions", []) if item.get("execution_id")}
    artifacts = {item.get("artifact_id"): item for item in snapshot.get("artifacts", []) if item.get("artifact_id")}
    artifact_aliases = {}
    for artifact in artifacts.values():
        for key in ("artifact_id", "name", "path", "relative_path", "url"):
            value = artifact.get(key)
            if value:
                artifact_aliases[str(value)] = artifact
    return assets, steps, executions, artifacts, artifact_aliases


def _resolve_evidence(
    evidence: dict[str, Any],
    *,
    assets: dict,
    steps: dict,
    executions: dict,
    artifacts: dict,
    artifact_aliases: dict,
) -> dict[str, Any]:
    artifact_ids = list(evidence.get("source_artifact_ids") or [])
    for reference in evidence.get("source_artifacts") or []:
        artifact = artifact_aliases.get(str(reference))
        if artifact and artifact["artifact_id"] not in artifact_ids:
            artifact_ids.append(artifact["artifact_id"])

    execution_ids = list(evidence.get("source_execution_ids") or [])
    for artifact_id in artifact_ids:
        execution_id = (artifacts.get(artifact_id) or {}).get("execution_id")
        if execution_id and execution_id not in execution_ids:
            execution_ids.append(execution_id)

    if not execution_ids:
        fields = [str(item).lower() for item in evidence.get("source_fields") or []]
        candidates = []
        for position, execution in enumerate(executions.values()):
            if execution.get("status") != "succeeded" or execution.get("tool_name") not in {
                "python_repl", "eda_profile", "load_data"
            }:
                continue
            searchable = f"{execution.get('code_or_query') or ''}\n{execution.get('stdout_preview') or ''}".lower()
            candidates.append((sum(field in searchable for field in fields), position, execution))
        if candidates:
            execution_ids.append(max(candidates, key=lambda item: (item[0], item[1]))[2]["execution_id"])

    step_ids = list(evidence.get("source_step_ids") or [])
    asset_ids = list(evidence.get("source_asset_ids") or [])
    for execution_id in execution_ids:
        execution = executions.get(execution_id) or {}
        step_id = execution.get("step_id")
        if step_id and step_id not in step_ids:
            step_ids.append(step_id)
        for asset_id in execution.get("input_asset_ids") or []:
            if asset_id not in asset_ids:
                asset_ids.append(asset_id)
    for artifact_id in artifact_ids:
        artifact = artifacts.get(artifact_id) or {}
        step_id = artifact.get("step_id")
        if step_id and step_id not in step_ids:
            step_ids.append(step_id)
        for asset_id in artifact.get("input_asset_ids") or []:
            if asset_id not in asset_ids:
                asset_ids.append(asset_id)

    warnings = []
    unknown_artifacts = [item for item in artifact_ids if item not in artifacts]
    unknown_executions = [item for item in execution_ids if item not in executions]
    unknown_steps = [item for item in step_ids if item not in steps]
    unknown_assets = [item for item in asset_ids if item not in assets]
    if unknown_artifacts:
        warnings.append(f"unknown artifact ids: {', '.join(unknown_artifacts)}")
    if unknown_executions:
        warnings.append(f"unknown execution ids: {', '.join(unknown_executions)}")
    if unknown_steps:
        warnings.append(f"unknown step ids: {', '.join(unknown_steps)}")
    if unknown_assets:
        warnings.append(f"unknown asset ids: {', '.join(unknown_assets)}")
    if not execution_ids:
        warnings.append("evidence is not linked to a computation execution")
    if not step_ids:
        warnings.append("evidence is not linked to a runtime step")
    if not asset_ids:
        warnings.append("evidence is not linked to an input data asset")

    known_fields = {
        column.get("name")
        for asset_id in asset_ids
        for column in (assets.get(asset_id) or {}).get("schema_snapshot", {}).get("columns", [])
    }
    unknown_fields = [field for field in evidence.get("source_fields") or [] if field not in known_fields]
    if unknown_fields:
        warnings.append(f"fields not found in linked asset schemas: {', '.join(unknown_fields)}")

    return {
        "evidence": evidence,
        "assets": [assets[item] for item in asset_ids if item in assets],
        "steps": [steps[item] for item in step_ids if item in steps],
        "executions": [executions[item] for item in execution_ids if item in executions],
        "artifacts": [artifacts[item] for item in artifact_ids if item in artifacts],
        "warnings": warnings,
        "is_complete": not warnings,
    }


def expand_finding_lineage(snapshot: dict[str, Any], finding_id: str) -> Optional[dict[str, Any]]:
    finding = next(
        (item for item in snapshot.get("findings", []) if item.get("finding_id") == finding_id),
        None,
    )
    if finding is None:
        return None
    assets, steps, executions, artifacts, artifact_aliases = _maps(snapshot)
    evidence_lineage = [
        _resolve_evidence(
            evidence,
            assets=assets,
            steps=steps,
            executions=executions,
            artifacts=artifacts,
            artifact_aliases=artifact_aliases,
        )
        for evidence in finding.get("evidence", [])
    ]
    recorded_by = executions.get(finding.get("recorded_by_execution_id"))
    warnings = [warning for item in evidence_lineage for warning in item["warnings"]]
    if finding.get("recorded_by_execution_id") and recorded_by is None:
        warnings.append("recording execution is missing")
    return {
        "run_id": snapshot.get("run", {}).get("run_id"),
        "finding": finding,
        "recorded_by": recorded_by,
        "evidence_lineage": evidence_lineage,
        "is_complete": bool(evidence_lineage) and not warnings,
        "warnings": warnings,
    }


def build_runtime_lineage_graph(snapshot: dict[str, Any]) -> dict[str, Any]:
    assets, steps, executions, artifacts, artifact_aliases = _maps(snapshot)
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []
    edge_keys = set()

    def add_node(node_id: str, label: str, node_type: str, attributes: dict) -> None:
        nodes[node_id] = {"id": node_id, "label": label, "type": node_type, "attributes": attributes}

    def add_edge(source: str, target: str, relationship: str) -> None:
        key = (source, target, relationship)
        if source in nodes and target in nodes and key not in edge_keys:
            edge_keys.add(key)
            edges.append({"source": source, "target": target, "relationship": relationship})

    for asset_id, asset in assets.items():
        add_node(f"asset:{asset_id}", asset.get("name") or asset_id, "data_source", asset)
        for column in asset.get("schema_snapshot", {}).get("columns", []):
            field = str(column.get("name"))
            add_node(f"field:{asset_id}:{field}", field, "field", column)
            add_edge(f"asset:{asset_id}", f"field:{asset_id}:{field}", "contains")
    for step_id, step in steps.items():
        add_node(f"step:{step_id}", step.get("objective") or step_id, "step", step)
    for execution_id, execution in executions.items():
        add_node(f"execution:{execution_id}", execution.get("tool_name") or execution_id, "execution", execution)
        if execution.get("step_id"):
            add_edge(f"step:{execution['step_id']}", f"execution:{execution_id}", "executes")
        for asset_id in execution.get("input_asset_ids") or []:
            add_edge(f"asset:{asset_id}", f"execution:{execution_id}", "input_to")
    for artifact_id, artifact in artifacts.items():
        add_node(f"artifact:{artifact_id}", artifact.get("name") or artifact_id, "artifact", artifact)
        if artifact.get("execution_id"):
            add_edge(f"execution:{artifact['execution_id']}", f"artifact:{artifact_id}", "produces")

    report_artifacts = [
        artifact_id for artifact_id, artifact in artifacts.items()
        if artifact.get("artifact_type") == "report"
    ]
    for finding in snapshot.get("findings", []):
        finding_id = str(finding.get("finding_id"))
        finding_node = f"finding:{finding_id}"
        add_node(finding_node, finding.get("statement") or finding_id, "finding", finding)
        if finding.get("recorded_by_execution_id"):
            add_edge(f"execution:{finding['recorded_by_execution_id']}", finding_node, "records")
        for index, evidence in enumerate(finding.get("evidence", []), start=1):
            evidence_id = evidence.get("evidence_id") or f"{finding_id}:{index}"
            evidence_node = f"evidence:{evidence_id}"
            add_node(evidence_node, evidence.get("evidence_text") or evidence_id, "evidence", evidence)
            resolved = _resolve_evidence(
                evidence,
                assets=assets,
                steps=steps,
                executions=executions,
                artifacts=artifacts,
                artifact_aliases=artifact_aliases,
            )
            for execution in resolved["executions"]:
                add_edge(f"execution:{execution['execution_id']}", evidence_node, "supports")
            for artifact in resolved["artifacts"]:
                add_edge(f"artifact:{artifact['artifact_id']}", evidence_node, "supports")
            for asset in resolved["assets"]:
                asset_id = asset["asset_id"]
                add_edge(f"asset:{asset_id}", evidence_node, "sources")
                for field in evidence.get("source_fields") or []:
                    add_edge(f"field:{asset_id}:{field}", evidence_node, "uses_field")
            add_edge(evidence_node, finding_node, "supports")
        for artifact_id in report_artifacts:
            add_edge(finding_node, f"artifact:{artifact_id}", "reported_in")

    return {
        "version": 2,
        "run_id": snapshot.get("run", {}).get("run_id"),
        "session_id": snapshot.get("task", {}).get("session_id"),
        "nodes": list(nodes.values()),
        "edges": edges,
    }
