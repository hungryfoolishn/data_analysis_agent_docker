"""record_finding tool — record structured findings with evidence."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List

from langchain_core.tools import tool

from langgraph_langchain.tools.registry import registry
from langgraph_langchain.tools._shared import (
    _is_rd_domain_session,
    _validate_tool_stage_factory,
)
from langgraph_langchain.schemas import EvidenceItem, Finding, VerificationResult
from langgraph_langchain.rd_validators import validate_rd_finding
from langgraph_langchain.runtime import SessionExecutionRecorder
from langgraph_langchain.tracing import get_trace_context

logger = logging.getLogger(__name__)


def _available_artifact_aliases(session) -> dict:
    """Return registered artifacts keyed by stable id and user-facing aliases."""
    artifacts = []
    runtime = getattr(session, "analysis_runtime", None)
    runtime_artifacts = getattr(runtime, "artifacts", {}) or {}
    for artifact in runtime_artifacts.values():
        artifacts.append(
            artifact.model_dump() if hasattr(artifact, "model_dump") else artifact
        )
    artifacts.extend(getattr(session, "new_artifacts", []) or [])

    aliases = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        for key in ("artifact_id", "name", "path", "relative_path", "url"):
            value = artifact.get(key)
            if value:
                aliases[str(value)] = artifact

    # LLMs often cite the chart section title rather than the generated file.
    # Add normalized title aliases so "Outliers" can resolve to
    # eda_outliers.png and "Distributions" to eda_distributions.png.
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        name = str(artifact.get("name") or "")
        normalized_name = name.casefold().replace("-", " ").replace("_", " ")
        for key in ("artifact_id", "name", "path", "relative_path", "url"):
            value = artifact.get(key)
            if not value:
                continue
            reference = str(value)
            base = reference.casefold().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            base = base.rsplit(".", 1)[0].replace("-", " ").replace("_", " ")
            if base and base in normalized_name:
                aliases.setdefault(base.strip(), artifact)
            for token in base.split():
                if len(token) >= 4 and token in normalized_name:
                    aliases.setdefault(token, artifact)
    return aliases


def _resolve_artifact_reference(reference: str, aliases: dict) -> dict | None:
    """Resolve an artifact reference exactly, normalized, or by title token."""
    artifact = aliases.get(str(reference))
    if artifact is not None:
        return artifact

    normalized = str(reference).casefold().replace("-", " ").replace("_", " ")
    candidates = []
    for alias, artifact in aliases.items():
        name = str(artifact.get("name") or "")
        normalized_name = name.casefold().replace("-", " ").replace("_", " ")
        if normalized == normalized_name:
            candidates.append((2, artifact))
        elif normalized in normalized_name:
            candidates.append((1, artifact))
        elif normalized in alias.casefold():
            candidates.append((1, artifact))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _resolve_evidence_lineage(
    session,
    *,
    source_fields: List[str],
    source_artifacts: List[str],
    source_execution_ids: List[str],
) -> dict:
    """Resolve user-facing evidence references to stable runtime identifiers."""
    runtime = getattr(session, "analysis_runtime", None)
    aliases = _available_artifact_aliases(session)
    artifacts = []
    unresolved_artifacts = []
    seen_artifacts = set()
    for reference in source_artifacts:
        artifact = _resolve_artifact_reference(str(reference), aliases)
        artifact_id = artifact.get("artifact_id") if artifact else None
        if artifact is None:
            if str(reference) not in unresolved_artifacts:
                unresolved_artifacts.append(str(reference))
            continue
        if artifact_id and artifact_id not in seen_artifacts:
            seen_artifacts.add(artifact_id)
            artifacts.append(artifact)

    executions_by_id = {
        item.execution_id: item
        for item in (getattr(runtime, "executions", []) or [])
    }
    execution_ids = []
    for execution_id in source_execution_ids:
        if execution_id in executions_by_id and execution_id not in execution_ids:
            execution_ids.append(execution_id)
    for artifact in artifacts:
        execution_id = artifact.get("execution_id")
        if execution_id in executions_by_id and execution_id not in execution_ids:
            execution_ids.append(execution_id)

    if not execution_ids:
        eligible = [
            item
            for item in executions_by_id.values()
            if item.status == "succeeded"
            and item.tool_name in {"python_repl", "eda_profile", "load_data"}
        ]
        if eligible:
            field_tokens = [str(field).lower() for field in source_fields if field]
            ranked = []
            for position, execution in enumerate(eligible):
                searchable = f"{execution.code_or_query or ''}\n{execution.stdout_preview or ''}".lower()
                score = sum(1 for token in field_tokens if token in searchable)
                ranked.append((score, position, execution))
            execution_ids.append(max(ranked, key=lambda item: (item[0], item[1]))[2].execution_id)

    selected_executions = [executions_by_id[item] for item in execution_ids]
    step_ids = []
    asset_ids = []
    for execution in selected_executions:
        if execution.step_id and execution.step_id not in step_ids:
            step_ids.append(execution.step_id)
        for asset_id in execution.input_asset_ids:
            if asset_id not in asset_ids:
                asset_ids.append(asset_id)
    for artifact in artifacts:
        step_id = artifact.get("step_id")
        if step_id and step_id not in step_ids:
            step_ids.append(step_id)
        for asset_id in artifact.get("input_asset_ids") or []:
            if asset_id not in asset_ids:
                asset_ids.append(asset_id)
    if not asset_ids and runtime is not None:
        asset_ids.extend(getattr(runtime.task, "input_asset_ids", []) or [])

    return {
        "aliases": aliases,
        "artifact_ids": [item["artifact_id"] for item in artifacts],
        "resolved_artifact_names": [item.get("name") or item.get("artifact_id") for item in artifacts],
        "unresolved_artifacts": unresolved_artifacts,
        "execution_ids": execution_ids,
        "step_ids": step_ids,
        "asset_ids": asset_ids,
    }


def _factory(session):
    _validate = _validate_tool_stage_factory(session)

    @tool
    def record_finding(
        statement: str,
        evidence_text: str,
        confidence_level: str = "medium",
        evidence_level: str = "B",
        hypothesis_flag: bool = False,
        category: str = None,
        source_fields: List[str] = None,
        source_artifacts: List[str] = None,
        source_execution_ids: List[str] = None,
        time_window: str = None,
        group_dimension: str = None,
        filters: List[str] = None,
        stats: dict = None,
        calculation_method: str = None,
    ) -> str:
        """Record a structured finding with evidence during analysis.

        Call this tool whenever you discover an important insight, pattern, or conclusion.
        Each finding must be backed by concrete evidence.

        EVIDENCE LEVEL GUIDELINES:
        - Level A (Facts): Pure factual descriptions without relationship claims
          Example: "North region Q3 revenue is $420K, accounting for 42% of total"
        - Level B (Correlations): Observed patterns, trends, or correlations
          Example: "Revenue decline coincides with order count decrease in the same period"
        - Level C (Causal): Claims about causation (requires strong evidence)
          Example: "Price increase caused 15% drop in conversion rate"
          Requirements: temporal ordering, control groups, mechanism explanation, alternatives ruled out

        Args:
            statement: The core conclusion or insight (e.g., "North region accounts for 42% of Q3 revenue")
            evidence_text: Detailed evidence supporting this finding (numbers, stats, observations)
            confidence_level: Your confidence in this finding - "low", "medium", or "high"
            evidence_level: Evidence quality - "A" (facts), "B" (correlations), "C" (causal claims). Default: "B"
            hypothesis_flag: True if this is a hypothesis requiring validation, False if it is an observation
            category: Finding category (e.g., "trend", "anomaly", "comparison", "attribution")
            source_fields: List of data fields used (e.g., ["region", "revenue", "date"])
            source_artifacts: List of charts or files supporting this (e.g., ["revenue_by_region.png"])
            source_execution_ids: Optional execution IDs when the caller has an explicit runtime reference
            time_window: Time period for this finding (e.g., "2025-Q3", "2025-07-01 to 2025-09-30")
            group_dimension: Grouping dimension if applicable (e.g., "region", "product_category")
            filters: Any filters applied (e.g., ["revenue > 0", "status = 'completed'"])
            stats: Key statistics as dict (e.g., {"north_revenue": 420000, "total_revenue": 1000000})
            calculation_method: How the finding was calculated (e.g., "SUM(revenue) GROUP BY region")

        Returns:
            Confirmation message with finding ID
        """
        # Validate stage before execution
        error_msg = _validate("record_finding")
        if error_msg:
            return f"[ERROR] {error_msg}"
        recorder = SessionExecutionRecorder(
            session,
            tool_name="record_finding",
            code_or_query=statement,
        )

        # Track tool usage in state machine
        session.state_machine.record_tool_use("record_finding")

        # Log tool call start
        session.structured_logger.log_tool_call(
            tool_name="record_finding",
            stage=session.state_machine.current_stage,
        )

        # Start trace span for this finding
        trace_ctx = get_trace_context(session.session_id)
        span_id = None
        if trace_ctx:
            span = trace_ctx.start_span(
                "record_finding",
                attributes={
                    "statement": statement,
                    "evidence_level": evidence_level,
                    "category": category,
                },
            )
            span_id = span.span_id

        # Pre-validate critical fields before Pydantic to give clearer errors
        if not evidence_text or not evidence_text.strip():
            recorder.fail("evidence_text is empty", error_type="ValidationError")
            if trace_ctx:
                trace_ctx.end_current_span(status="failed", error_message="evidence_text is empty")
            return "[ERROR] evidence_text is empty — provide concrete evidence (numbers, stats, observations)."

        lineage = _resolve_evidence_lineage(
            session,
            source_fields=source_fields or [],
            source_artifacts=source_artifacts or [],
            source_execution_ids=source_execution_ids or [],
        )
        unresolved_artifacts = lineage.get("unresolved_artifacts") or []
        if unresolved_artifacts:
            message = (
                "Unknown artifact references: "
                + ", ".join(sorted(unresolved_artifacts))
                + ". Ensure artifact is created before referencing it."
            )
            recorder.fail(message, error_type="ArtifactValidationError")
            if trace_ctx:
                trace_ctx.end_current_span(
                    status="failed",
                    error_message=message,
                )
            return f"[ERROR] {message}"

        try:
            evidence_item = EvidenceItem(
                evidence_text=evidence_text,
                source_fields=source_fields or [],
                source_artifacts=lineage["resolved_artifact_names"],
                source_artifact_ids=lineage["artifact_ids"],
                source_execution_ids=lineage["execution_ids"],
                source_step_ids=lineage["step_ids"],
                source_asset_ids=lineage["asset_ids"],
                time_window=time_window,
                group_dimension=group_dimension,
                filters=filters or [],
                stats=stats,
                calculation_method=calculation_method,
                # Add trace info
                trace_id=trace_ctx.trace_id if trace_ctx else None,
                span_id=span_id,
                tool_name="record_finding",
                timestamp=datetime.now().isoformat() if trace_ctx else None,
            )
        except Exception as exc:
            recorder.fail(str(exc), error_type=type(exc).__name__)
            if trace_ctx:
                trace_ctx.end_current_span(status="failed", error_message=str(exc))
            return f"[ERROR] Invalid evidence: {exc}"

        runtime = getattr(session, "analysis_runtime", None)
        finding_id = f"F{len(session.findings) + 1:03d}"

        finding = Finding(
            finding_id=finding_id,
            statement=statement,
            evidence=[evidence_item],
            supported_by=[evidence_item.evidence_id],
            confidence_level=confidence_level,
            evidence_level=evidence_level,
            hypothesis_flag=hypothesis_flag,
            category=category,
            run_id=getattr(runtime, "run", None).run_id
            if runtime is not None and getattr(runtime, "run", None)
            else None,
            recorded_by_execution_id=recorder.execution_id,
            recorded_by_step_id=recorder.step_id,
            # Add trace info
            trace_id=trace_ctx.trace_id if trace_ctx else None,
        )

        is_valid, rd_errors = True, []
        if _is_rd_domain_session(session):
            is_valid, rd_errors = validate_rd_finding(finding)
        if not is_valid:
            recorder.fail(
                "; ".join(rd_errors),
                error_type="DomainValidationWarning",
            )
            if trace_ctx and span_id:
                trace_ctx.end_span(span_id, status="warning", validation_errors=rd_errors)
            return (
                f"[WARNING] Finding recorded but has R&D domain issues:\n"
                + "\n".join(f"  - {err}" for err in rd_errors)
                + f"\n\nFinding {finding_id} recorded anyway. Consider revising."
            )

        # P1.2: Validate evidence binding
        from langgraph_langchain.evidence_binding import (
            validate_evidence_binding,
            validate_evidence_completeness,
        )

        # Get available artifacts for validation
        available_artifacts = lineage["aliases"]

        # Critical validation: evidence must be properly bound
        is_valid, binding_errors = validate_evidence_binding(finding, available_artifacts)
        if not is_valid:
            error_msg = (
                "[ERROR] Evidence binding validation failed:\n"
                + "\n".join(f"  - {err}" for err in binding_errors)
            )
            if trace_ctx and span_id:
                trace_ctx.end_span(span_id, status="error", validation_errors=binding_errors)
            recorder.fail(
                "; ".join(binding_errors),
                error_type="EvidenceBindingError",
            )
            return error_msg + "\n\nFinding NOT recorded. Please provide proper evidence."

        # Completeness check: warn about missing optional fields
        is_complete, completeness_warnings = validate_evidence_completeness(
            finding, available_artifacts
        )

        runtime_v2_active = runtime is not None and runtime.scheduler is not None
        if runtime_v2_active:
            from langgraph_langchain.runtime.finding_builder import FindingProvenanceError

            executions_by_id = {
                item.execution_id: item for item in (runtime.executions or [])
            }
            execution_ids = [
                execution_id
                for execution_id in lineage["execution_ids"]
                if execution_id in executions_by_id
            ]
            if not execution_ids:
                error_msg = (
                    "[ERROR] Finding evidence must cite at least one succeeded Runtime execution. "
                    "Finding NOT recorded."
                )
                recorder.fail(
                    "evidence has no Runtime execution lineage",
                    error_type="FindingProvenanceError",
                )
                if trace_ctx and span_id:
                    trace_ctx.end_current_span(
                        status="failed",
                        error_message="evidence has no Runtime execution lineage",
                    )
                return error_msg

            source_execution = executions_by_id[execution_ids[0]]
            if source_execution.status != "succeeded":
                error_msg = (
                    "[ERROR] Finding evidence cites a Runtime execution that did not succeed. "
                    "Finding NOT recorded."
                )
                recorder.fail(
                    f"source execution {source_execution.execution_id} did not succeed",
                    error_type="FindingProvenanceError",
                )
                if trace_ctx and span_id:
                    trace_ctx.end_current_span(
                        status="failed",
                        error_message="source execution did not succeed",
                    )
                return error_msg

            source_has_passed_verification = any(
                item.execution_id == source_execution.execution_id
                and item.status == "passed"
                and item.passed is True
                for item in (runtime.verifications or [])
            )
            if not source_has_passed_verification:
                error_msg = (
                    "[ERROR] Finding evidence cites a Runtime execution without a passing "
                    "verification. Finding NOT recorded."
                )
                recorder.fail(
                    f"source execution {source_execution.execution_id} has no passing verification",
                    error_type="FindingProvenanceError",
                )
                if trace_ctx and span_id:
                    trace_ctx.end_current_span(
                        status="failed",
                        error_message="source execution has no passing verification",
                    )
                return error_msg

            verification_result = VerificationResult(
                run_id=runtime.run.run_id,
                step_id=source_execution.step_id or session.current_runtime_step_id,
                task_id=source_execution.task_id,
                execution_id=source_execution.execution_id,
                evidence_id=evidence_item.evidence_id,
                check_type="evidence_existence",
                status="passed",
                passed=True,
                expected="non-empty evidence bound to a verified Runtime execution",
                actual={"evidence_id": evidence_item.evidence_id},
                message="Finding evidence exists and is bound to a verified execution",
            )
            runtime.record_verification(verification_result)
            evidence_item.verification_status = "verified"
            evidence_item.verification_result_id = verification_result.verification_id
            runtime.record_evidence(evidence_item)

            try:
                runtime.record_verified_finding(finding)
            except Exception as exc:
                recorder.fail(str(exc), error_type=type(exc).__name__)
                if trace_ctx and span_id:
                    trace_ctx.end_current_span(status="failed", error_message=str(exc))
                return f"[ERROR] Finding provenance validation failed: {exc}\n\nFinding NOT recorded."

            session.findings.append(finding)
        elif runtime is not None:
            # Metadata-only Runtime compatibility path.  These sessions have no
            # V2 scheduler, so the existing evidence-existence check remains
            # authoritative; FindingBuilder's full lineage gate is not enabled.
            verification_result = VerificationResult(
                run_id=runtime.run.run_id,
                step_id=session.current_runtime_step_id,
                execution_id=recorder.execution_id,
                evidence_id=evidence_item.evidence_id,
                check_type="evidence_existence",
                status="passed",
                passed=True,
                expected="non-empty evidence with an ID",
                actual={"evidence_id": evidence_item.evidence_id},
                message="Finding evidence exists and is bound to this execution",
            )
            runtime.record_verification(verification_result)
            evidence_item.verification_status = "verified"
            evidence_item.verification_result_id = verification_result.verification_id
            runtime.record_evidence(evidence_item)
            runtime.record_finding(finding)
            session.findings.append(finding)
        else:
            session.findings.append(finding)

        # Build response message
        response_msg = f"Finding {finding_id} recorded: {statement[:80]}..."
        if not is_complete:
            response_msg += (
                "\n[INFO] Evidence completeness suggestions:\n"
                + "\n".join(f"  - {warn}" for warn in completeness_warnings)
            )

        # Mark findings as recorded for state machine
        session.state_machine.add_condition("findings_recorded")

        # If we have enough findings, mark for synthesis
        if len(session.findings) >= 3:
            session.state_machine.add_condition("min_findings_count")

        # A validated finding completes the deep-dive evidence requirement.
        # Advance the authoritative state machine so report validation and the
        # user-facing stage cannot diverge.
        session.try_advance_stage()

        # End trace span
        if trace_ctx and span_id:
            trace_ctx.end_span(span_id, status="completed", finding_id=finding_id)

        recorder.succeed(response_msg)
        return response_msg

    return record_finding


registry.register(
    name="record_finding",
    toolset="analysis",
    factory=_factory,
    description="Record a structured finding with evidence during analysis.",
    emoji="🔍",
)
