"""finish_report tool — validate and submit the final analysis report."""

from __future__ import annotations

import json
import logging
import re

from langchain_core.tools import tool

from langgraph_langchain.tools.registry import registry
from langgraph_langchain.tools._shared import (
    _contains_evidence_marker,
    _contains_quantitative_evidence,
    _extract_section,
    _has_group_reference,
    _has_time_window_reference,
    _is_rd_domain_session,
    _stage_rank,
    _validate_tool_stage_factory,
)
from langgraph_langchain.runtime import SessionExecutionRecorder
from langgraph_langchain.tracing import get_trace_context

logger = logging.getLogger(__name__)


def _factory(session):
    _validate = _validate_tool_stage_factory(session)

    @tool
    def finish_report(markdown: str) -> str:
        """Submit the final analysis report. Call once after a report preflight.

        The report must contain Summary, Data Context, Key Findings, Data
        Quality, and Analysis headings. Data Context must state the time range,
        metric definitions, deduplication rule, and assumptions. If the data has
        no time field, explicitly state that the time range is not applicable
        and this is a static cross-sectional analysis. When charts exist, add a
        Visualizations section that explains the numerical conclusion each chart
        supports instead of merely listing filenames.

        Args:
            markdown: Complete markdown report with findings and insights.
        """
        if session.report is not None:
            return "Report already submitted. Do not call finish_report again."
        # Validate stage before execution
        error_msg = _validate("finish_report")
        if error_msg:
            return f"[ERROR] {error_msg}"
        recorder = SessionExecutionRecorder(session, tool_name="finish_report")

        trace_ctx = get_trace_context(session.session_id)
        if trace_ctx:
            trace_ctx.start_span(
                "finish_report",
                attributes={
                    "report_length": len(markdown),
                    "tool": "finish_report",
                },
            )

        session.structured_logger.log_tool_call(
            tool_name="finish_report",
            stage=session.state_machine.current_stage,
        )

        # Track tool usage in state machine
        session.state_machine.record_tool_use("finish_report")

        markdown = markdown.strip()
        if _stage_rank(session.current_stage) < _stage_rank("conclusion_synthesis"):
            session.pending_report_markdown = None
            session.fail_stage(
                session.current_stage,
                "report_rejected",
                "finish_report called before synthesis stage",
                retryable=True,
                hint="Complete schema understanding, EDA, and deep-dive analysis before submitting the final report.",
                recovery_action="retry_narrower_scope",
            )
            if trace_ctx:
                trace_ctx.current_span.set_attribute("validation_passed", False)
                trace_ctx.current_span.set_attribute("rejection_reason", "called before synthesis stage")
                trace_ctx.end_current_span(status="failed", error_message="Report rejected: called before synthesis stage")
            recorder.revision(
                "finish_report called before synthesis stage",
                feedback_type="ReportValidationFeedback",
            )
            return (
                "REPORT REJECTED. Fix the following before calling finish_report again:\n"
                "- finish_report can only be called after the analysis reaches synthesis/final_report stage"
            )
        session.state_machine.add_condition("findings_organized")
        session.state_machine.add_condition("evidence_linked")
        if len(session.findings) >= 2:
            session.state_machine.add_condition("min_findings_count")
        session.start_stage("report_generation")
        # Check if findings were recorded
        if len(session.findings) < 2:
            session.pending_report_markdown = None
            session.fail_stage(
                "report_generation",
                "report_rejected",
                "Insufficient structured findings recorded",
                retryable=True,
                hint="Use record_finding to document at least 2 key insights with evidence before submitting the report.",
                recovery_action="retry_narrower_scope",
            )
            if trace_ctx:
                trace_ctx.current_span.set_attribute("validation_passed", False)
                trace_ctx.current_span.set_attribute("rejection_reason", "insufficient findings")
                trace_ctx.current_span.set_attribute("findings_count", len(session.findings))
                trace_ctx.end_current_span(status="failed", error_message="Report rejected: insufficient findings")
            recorder.revision(
                "Insufficient structured findings recorded",
                feedback_type="ReportValidationFeedback",
            )
            return (
                "REPORT REJECTED. Fix the following before calling finish_report again:\n"
                f"- Only {len(session.findings)} findings recorded. Use record_finding to document at least 2 key insights with concrete evidence.\n"
                "- Each finding should include: statement, evidence_text, source_fields, and confidence_level."
            )

        session.pending_report_markdown = markdown
        markdown_lower = markdown.lower()
        headings = re.findall(r"^#{1,6}\s+(.+)$", markdown, flags=re.MULTILINE)
        issues = []

        if len(markdown) < 400:
            issues.append("report is too short (minimum 400 characters for a substantive analysis report)")
        if not headings:
            issues.append("report has no markdown headings (use ## for sections)")

        section_keywords = {
            "Summary": ["summary", "摘要", "概要"],
            "Data Context": ["data context", "数据说明", "数据背景", "分析前提", "数据范围"],
            "Key Findings": ["key finding", "key findings", "finding", "发现", "关键发现", "结论", "insight"],
            "Data Quality": ["data quality", "数据质量", "质量检查", "缺失值", "异常值"],
            "Analysis": ["analysis", "分析过程", "分析方法", "methodology", "方法论", "方法"],
            "Visualizations": ["visualizations", "visualization", "图表说明", "图表", "可视化"],
            "Recommendations": ["recommendations", "recommendation", "建议", "行动建议"],
        }

        present_sections = {
            label: any(keyword in markdown_lower for keyword in keywords)
            for label, keywords in section_keywords.items()
        }

        # Check for Data Context section (REQUIRED)
        if not present_sections["Data Context"]:
            issues.append("missing required 'Data Context' or '数据说明' section - must document time range, metric definitions, dedup rules, and key assumptions")

        required_core = ["Summary", "Key Findings", "Data Quality", "Analysis"]
        missing_core = [label for label in required_core if not present_sections[label]]
        if missing_core:
            issues.append(f"missing core sections: {', '.join(missing_core)}")

        present_count = sum(1 for is_present in present_sections.values() if is_present)
        if present_count < 5:
            issues.append("report structure is too thin; include at least 5 substantive sections including Data Context")

        bullet_lines = [
            line.strip() for line in markdown.splitlines()
            if line.strip().startswith(("- ", "* "))
        ]
        evidence_bullets = [line for line in bullet_lines if _contains_evidence_marker(line)]
        if len(evidence_bullets) < 2:
            issues.append("key findings lack enough evidence traces (numbers, percentages, groups, dates, or chart/table references)")

        data_context_section = _extract_section(markdown, "Data Context") or _extract_section(markdown, "数据说明")
        key_findings_section = _extract_section(markdown, "Key Findings") or _extract_section(markdown, "关键发现")
        data_quality_section = _extract_section(markdown, "Data Quality") or _extract_section(markdown, "数据质量")
        analysis_section = _extract_section(markdown, "Analysis") or _extract_section(markdown, "分析")
        recommendations_section = _extract_section(markdown, "Recommendations") or _extract_section(markdown, "建议")
        visualizations_section = _extract_section(markdown, "Visualizations") or _extract_section(markdown, "图表说明")

        # Validate Data Context section content
        if data_context_section:
            context_requirements = {
                "time_range": ["时间范围", "time range", "时间窗口", "time window", "分析期间", "period"],
                "metric_definition": ["指标定义", "metric definition", "指标说明", "metric", "定义"],
                "dedup_rule": ["去重", "dedup", "重复", "duplicate"],
            }
            missing_context = []
            for req_name, keywords in context_requirements.items():
                if not any(kw in data_context_section.lower() for kw in keywords):
                    missing_context.append(req_name)
            if missing_context:
                issues.append(f"Data Context section is incomplete - missing: {', '.join(missing_context)}")

        runtime = getattr(session, "analysis_runtime", None)
        sampled_assets = [
            asset for asset in getattr(runtime, "assets", {}).values()
            if getattr(asset, "source_metadata", {}).get("sampling")
        ] if runtime is not None else []
        if sampled_assets and not any(
            marker in (data_context_section or "").lower()
            for marker in ("sampling", "sampled", "sample", "抽样", "采样", "样本")
        ):
            issues.append(
                "sampling_disclosure_missing: Data Context must state the sampling method, "
                "seed, sampled rows, and original row count"
            )

        # Check if metric definitions were declared
        if len(session.metric_definitions) == 0:
            issues.append("no metrics were declared using declare_metric - use declare_metric to document key metric definitions")

        if _is_rd_domain_session(session):
            from langgraph_langchain.rd_validators import validate_rd_analysis_completeness
            is_complete, rd_warnings = validate_rd_analysis_completeness(session.findings, session.metric_definitions)
            if rd_warnings:
                for warning in rd_warnings:
                    issues.append(f"rd_analysis_completeness: {warning}")

        if key_findings_section and not _contains_evidence_marker(key_findings_section):
            issues.append("key findings section lacks concrete evidence markers such as numbers, groups, rankings, dates, or chart references")

        trend_claim_pattern = re.compile(r"(趋势|上升|下降|增长|下滑|波动|trend|increase|decrease)", flags=re.IGNORECASE)
        trend_sections = [section for section in [key_findings_section, analysis_section] if section]
        if any(trend_claim_pattern.search(section) for section in trend_sections):
            if any(trend_claim_pattern.search(section) and not _has_time_window_reference(section) for section in trend_sections):
                issues.append("trend-related conclusions should mention a time window or comparison period")

        if any(term in markdown for term in ["分组", "group", "segment", "区域", "渠道", "产品", "category", "region"]):
            if not _has_group_reference(markdown):
                issues.append("grouped conclusions should identify the grouping dimension explicitly")

        if data_quality_section:
            dq_lines = [line.strip() for line in data_quality_section.splitlines() if line.strip()]
            dq_has_evidence = any(_contains_evidence_marker(line) for line in dq_lines)
            if not dq_has_evidence:
                issues.append("data quality section should include concrete fields, counts, percentages, or anomaly bounds")

        summary_section = _extract_section(markdown, "Summary") or _extract_section(markdown, "摘要")
        metric_scope_markers = ["口径", "definition", "定义", "denominator", "分母", "dedup", "去重", "time window", "时间窗口", "scope", "窗口"]
        semantic_uncertainty_markers = ["字段语义", "semantic", "mapping", "口径", "exploratory", "假设", "需进一步验证", "uncertain"]
        ratio_claim_pattern = re.compile(r"(转化率|share|ratio|conversion|conversion rate|rate)", flags=re.IGNORECASE)
        metric_risk_pattern = re.compile(
            r"(退款|refund|duplicate orders?|重复订单|字段语义|semantic (?:risk|ambiguity)|"
            r"口径.{0,12}(?:不明|未知|风险|歧义|待确认)|mapping.{0,12}(?:risk|unknown|ambiguous))",
            flags=re.IGNORECASE,
        )
        semantic_claim_pattern = re.compile(r"(新客|老客|高价值|流失|复购|退款用户|活跃用户|付费用户|留存用户)")

        if ratio_claim_pattern.search(markdown):
            metric_scope_present = any(
                section and any(marker in section.lower() for marker in metric_scope_markers)
                for section in [summary_section, data_quality_section, analysis_section]
            )
            if not metric_scope_present:
                issues.append("missing_metric_definition: ratio/share/conversion claims should include denominator, dedup rule, or metric scope")

        if metric_risk_pattern.search(markdown):
            semantic_risk_present = any(
                section and any(marker in section.lower() for marker in semantic_uncertainty_markers)
                for section in [summary_section, data_quality_section]
            )
            if not semantic_risk_present:
                issues.append("missing_definition_risk_note: reports discussing metric-definition or semantic risk should surface an explicit caveat in Summary or Data Quality")

        if semantic_claim_pattern.search(markdown) and not any(marker in markdown_lower for marker in ["假设", "assumption", "exploratory", "需进一步验证", "uncertain", "口径"]):
            issues.append("missing_semantic_assumption_note: business-semantic labels should be marked as assumptions or caveats when not directly defined in source data")

        if present_sections["Recommendations"] and recommendations_section:
            weak_recommendation_present = any(term in recommendations_section.lower() for term in ["验证", "观察", "monitor", "validate", "follow-up", "跟踪"])
            strong_recommendation_present = bool(re.search(r"(立即调整|should|must|prioritize|increase|cut|stop|launch|立即|全面推广|马上|立刻|直接上线|全面实施)", recommendations_section, flags=re.IGNORECASE))
            if not weak_recommendation_present and not strong_recommendation_present:
                quantified_support = _contains_quantitative_evidence(key_findings_section) and (_has_time_window_reference(key_findings_section) or _has_time_window_reference(analysis_section or ""))
                if not quantified_support:
                    issues.append("recommendation_without_support: recommendations should stay in validation/observe mode unless supported by quantified findings")

        if present_sections["Visualizations"]:
            visualization_section_has_support = bool(
                re.search(r"(图|chart|figure).{0,60}(支持|显示|表明|说明)", markdown, flags=re.IGNORECASE | re.DOTALL)
                or re.search(r"(支持|显示|表明|说明).{0,60}(图|chart|figure)", markdown, flags=re.IGNORECASE | re.DOTALL)
            )
            if not visualization_section_has_support:
                issues.append("visualizations section exists but does not explain what conclusion the charts support")
            elif visualizations_section and not _contains_evidence_marker(visualizations_section):
                issues.append("visualizations section should connect charts to specific findings, periods, or group comparisons")
        elif session.known_image_files:
            issues.append("charts were generated but the report is missing a Visualizations/图表说明 section")

        vague_phrases = ["感觉", "看起来像", "大概", "猜测"]
        vague_hits = [phrase for phrase in vague_phrases if phrase in markdown]
        if vague_hits:
            issues.append(f"report uses vague wording without enough evidence: {', '.join(vague_hits)}")

        uncertain_markers = ["可能", "或许", "需进一步验证", "需要进一步验证", "假设", "推测", "线索", "hypothesis", "suggestive", "uncertainty", "uncertain"]
        evidence_level_markers = ["evidence level", "证据等级", "evidence: a", "evidence: b", "evidence: c"]
        contribution_markers = ["contribution", "贡献", "contributor", "top positive", "top negative"]
        stability_markers = ["分层", "多窗口", "去掉", "稳定性", "对照", "stratified", "control", "window", "counterfactual"]
        definition_risk_markers = ["口径", "definition", "data quality", "uncertainty", "exploratory", "业务定义"]
        driver_terms = ["原因", "归因", "驱动", "driver", "due to", "caused by"]
        strong_causal_terms = ["根因", "direct cause", "直接导致", "由", "led to", "caused by"]
        strong_action_terms = ["立即调整", "should", "must", "prioritize", "increase", "cut", "stop", "launch", "立即", "全面推广", "马上", "立刻", "直接上线", "全面实施"]
        driver_sections = [section for section in [analysis_section, key_findings_section, recommendations_section] if section]

        driver_claim_pattern = re.compile(r"(原因|归因|驱动|driver|due to|caused by)", flags=re.IGNORECASE)
        for section in driver_sections:
            if driver_claim_pattern.search(section):
                has_evidence_level = any(marker in section.lower() for marker in evidence_level_markers)
                has_contribution = any(marker in section.lower() for marker in contribution_markers)
                has_boundary = any(marker in section.lower() for marker in uncertain_markers)
                if not (has_evidence_level or has_contribution or has_boundary):
                    issues.append("missing_driver_evidence_level: driver claims must include evidence level, contribution breakdown, or explicit uncertainty/hypothesis boundary")
                    break

        strong_action_pattern = re.compile(r"(立即调整|should|must|prioritize|increase|cut|stop|launch|立即|全面推广|马上|立刻|直接上线|全面实施)", flags=re.IGNORECASE)
        if recommendations_section and strong_action_pattern.search(recommendations_section):
            has_numeric = _contains_quantitative_evidence(recommendations_section) or _contains_quantitative_evidence(key_findings_section)
            has_time = _has_time_window_reference(recommendations_section) or _has_time_window_reference(key_findings_section)
            has_group = _has_group_reference(recommendations_section) or _has_group_reference(key_findings_section)
            if not (has_numeric and has_time and has_group):
                issues.append("recommendation_without_support: strong recommendations require quantitative support, time window, and impacted group/object")

        driver_main_pattern = re.compile(
            r"(主要由[^。\n]{0,80}(驱动|导致)|mainly driven by|主驱动是|主要驱动是)",
            flags=re.IGNORECASE,
        )
        if any(driver_main_pattern.search(section) for section in driver_sections):
            has_group = any(_has_group_reference(section) for section in driver_sections if driver_main_pattern.search(section))
            has_quant = any(_contains_quantitative_evidence(section) for section in driver_sections if driver_main_pattern.search(section))
            has_contribution = any(any(marker in section.lower() for marker in contribution_markers) for section in driver_sections if driver_main_pattern.search(section))
            if not (has_group and has_quant and has_contribution):
                issues.append("driver_claim_without_contribution_breakdown: main driver statements must identify dimension, group, direction, and quantitative contribution")

        strong_causal_pattern = re.compile(r"(根因|direct cause|直接导致|led to|caused by|明确由)", flags=re.IGNORECASE)
        for section in driver_sections:
            if strong_causal_pattern.search(section):
                has_boundary = any(marker in section.lower() for marker in uncertain_markers)
                has_stability = any(marker in section.lower() for marker in stability_markers)
                if not (has_boundary or has_stability):
                    issues.append("causal_claim_without_boundary: causal language needs controls, stability checks, or explicit uncertainty")
                    break

        if any(driver_main_pattern.search(section) or strong_causal_pattern.search(section) for section in driver_sections):
            has_stability_note = any(any(marker in section.lower() for marker in stability_markers) for section in driver_sections)
            if not has_stability_note:
                issues.append("missing_stability_check_for_explanation: explanatory conclusions should mention robustness via stratification, alternate windows, or removing dominant groups")

        if any(driver_main_pattern.search(section) or strong_causal_pattern.search(section) for section in driver_sections):
            has_definition_note = bool(data_quality_section) and any(marker in data_quality_section.lower() for marker in definition_risk_markers)
            if not has_definition_note:
                issues.append("missing_definition_risk_note: explanatory conclusions should include metric-definition, data-quality, or uncertainty caveats")

        explanation_bundle = session.ns.get("explanation_bundle") or {}
        metric_decomposition = explanation_bundle.get("metric_decomposition") or {}
        driver_ranking = explanation_bundle.get("driver_ranking") or []
        definition_risk = explanation_bundle.get("definition_risk") or {}
        counterfactual_checks = explanation_bundle.get("counterfactual_checks") or []
        recommendation_candidates = explanation_bundle.get("recommendations") or []

        if driver_sections and driver_ranking:
            top_driver = driver_ranking[0]
            top_driver_group = str(top_driver.get("group", "")).lower()
            top_driver_dimension = str(top_driver.get("dimension", "")).lower()
            if top_driver_group and top_driver_group not in markdown_lower:
                issues.append(
                    f"missing_explanation_bundle_reference: report should reference top ranked driver group '{top_driver.get('group')}' from explanation_bundle"
                )
            if top_driver_dimension and top_driver_dimension not in markdown_lower:
                issues.append(
                    f"missing_explanation_bundle_reference: report should reference driver dimension '{top_driver.get('dimension')}' from explanation_bundle"
                )
            top_driver_level = str(top_driver.get("evidence_level", "")).strip().lower()
            if top_driver_level:
                relevant_driver_sections = [section for section in [key_findings_section, analysis_section] if section and (top_driver_group in section.lower() or top_driver_dimension in section.lower())]
                if relevant_driver_sections and not any(
                    ("evidence level" in section.lower())
                    or ("证据等级" in section)
                    or (f"evidence: {top_driver_level}" in section.lower())
                    for section in relevant_driver_sections
                ):
                    issues.append("missing_explanation_bundle_reference: report should cite evidence level for the top ranked driver from explanation_bundle")

        if metric_decomposition and any(driver_main_pattern.search(section) for section in driver_sections):
            period_tokens = [
                str(metric_decomposition.get("previous_period", "")).lower(),
                str(metric_decomposition.get("current_period", "")).lower(),
            ]
            if not all(token and token in markdown_lower for token in period_tokens):
                issues.append("missing_explanation_bundle_reference: explanatory report should mention the comparison window from metric decomposition")
            metric_token = str(metric_decomposition.get("metric", "")).lower()
            top_contributors = (metric_decomposition.get("top_negative_contributors") or []) + (metric_decomposition.get("top_positive_contributors") or [])
            has_contributor_reference = any(
                str(item.get("group", "")).lower() in markdown_lower or str(item.get("dimension", "")).lower() in markdown_lower
                for item in top_contributors[:3]
            )
            if metric_token and metric_token not in markdown_lower:
                issues.append("missing_explanation_bundle_reference: explanatory report should reference the changed metric from metric decomposition")
            if top_contributors and not has_contributor_reference:
                issues.append("missing_explanation_bundle_reference: explanatory report should mention at least one top contributor from metric decomposition")

        if definition_risk.get("exploratory_only") and not any(marker in markdown_lower for marker in ["exploratory", "口径", "definition", "refund", "重复", "duplicate"]):
            issues.append("missing_definition_risk_note: report should surface definition risk captured in explanation_bundle")

        if counterfactual_checks and any(driver_main_pattern.search(section) or strong_causal_pattern.search(section) for section in driver_sections):
            analysis_has_stability = bool(analysis_section) and any(
                marker in analysis_section.lower()
                for marker in ["去掉头部组", "多窗口", "稳定", "stratified", "window", "control", "counterfactual"]
            )
            if not analysis_has_stability:
                issues.append("missing_stability_check_for_explanation: report should mention robustness checks captured in explanation_bundle")

        if recommendations_section and recommendation_candidates:
            action_candidates = [item for item in recommendation_candidates if item.get("type") == "action"]
            validation_candidates = [item for item in recommendation_candidates if item.get("type") in {"validation", "observe"}]
            strong_action_language = any(term in recommendations_section.lower() for term in ["should", "must", "prioritize", "increase", "cut", "stop", "launch", "立即", "全面推广"])
            if action_candidates and strong_action_language:
                pass
            elif validation_candidates and strong_action_language:
                issues.append("missing_explanation_bundle_reference: recommendations should align with evidence strength in explanation_bundle")
            elif validation_candidates and not any(term in recommendations_section.lower() for term in ["验证", "观察", "monitor", "validate", "follow-up"]):
                issues.append("missing_explanation_bundle_reference: recommendations should align with evidence strength in explanation_bundle")

        # Week 3: Validate evidence levels for all findings
        from langgraph_langchain.evidence_validator import validate_findings_evidence_levels
        from langgraph_langchain.recommendation_validator import validate_recommendations_against_findings

        evidence_errors = validate_findings_evidence_levels(session.findings)
        if evidence_errors:
            for error in evidence_errors:
                issues.append(f"evidence_level_violation: {error}")

        recommendation_errors = validate_recommendations_against_findings(markdown, session.findings)
        if recommendation_errors:
            for error in recommendation_errors:
                issues.append(f"recommendation_evidence_mismatch: {error}")

        if issues:
            session.pending_report_markdown = None
            session.fail_stage(
                "report_generation",
                "report_rejected",
                "finish_report validation failed",
                retryable=True,
                hint="Address the reported credibility and structure issues, then resubmit the report.",
                recovery_action="retry_narrower_scope",
            )
            deduped_issues = []
            seen = set()
            for issue in issues:
                if issue not in seen:
                    seen.add(issue)
                    deduped_issues.append(issue)
            joined = "\n- ".join(deduped_issues)

            if trace_ctx:
                trace_ctx.end_current_span(status="failed", error_message=f"Validation failed: {len(deduped_issues)} issues")

            recorder.revision(
                "; ".join(deduped_issues),
                feedback_type="ReportValidationFeedback",
            )
            session.start_stage("conclusion_synthesis")
            return f"REPORT REJECTED. Fix the following before calling finish_report again:\n- {joined}"

        session.complete_stage("report_generation")
        session.report = markdown
        session.pending_report_markdown = None

        # Mark report as generated and transition to COMPLETED
        session.state_machine.add_condition("report_generated")

        # Try to transition to COMPLETED stage
        from langgraph_langchain.schemas import AnalysisStage
        can_transition, reason = session.state_machine.can_transition_to(AnalysisStage.COMPLETED)
        if can_transition:
            session.state_machine.transition_to(AnalysisStage.COMPLETED)
            session.start_stage("completed")

        # Save final report first, then append process log
        process_section = "".join(session.process_log).strip()
        full_content = markdown
        if process_section:
            full_content += (
                "\n\n---\n\n"
                "## 分析过程记录\n\n"
                f"{process_section}"
            )
        report_path = session.workspace_dir / "data_analysis_report.md"
        report_path.write_text(full_content, encoding="utf-8")
        recorder.register_artifact(report_path)

        # Save structured findings to JSON
        findings_data = {
            "findings": [f.model_dump() for f in session.findings],
            "metric_definitions": [m.model_dump() for m in session.metric_definitions],
            "assumptions": [a.model_dump() for a in session.assumptions],
        }
        findings_path = session.workspace_dir / "analysis_findings.json"
        findings_path.write_text(json.dumps(findings_data, ensure_ascii=False, indent=2), encoding="utf-8")
        recorder.register_artifact(findings_path)

        # Build lineage from authoritative runtime IDs rather than transient spans.
        from langgraph_langchain.runtime.finding_lineage import build_runtime_lineage_graph
        runtime = getattr(session, "analysis_runtime", None)
        if runtime is not None:
            for finding in session.findings:
                runtime.record_finding(finding)
            for metric_definition in session.metric_definitions:
                runtime.record_metric_definition(metric_definition)
            for assumption in session.assumptions:
                runtime.record_assumption(assumption)
        runtime_snapshot = runtime.snapshot() if runtime is not None else {
            "task": {"session_id": session.session_id},
            "run": {},
            "assets": [],
            "executions": [],
            "artifacts": list(session.new_artifacts),
            "findings": [finding.model_dump(mode="json") for finding in session.findings],
        }
        lineage_data = build_runtime_lineage_graph(runtime_snapshot)
        lineage_path = session.workspace_dir / "lineage_graph.json"
        lineage_path.write_text(json.dumps(lineage_data, ensure_ascii=False, indent=2), encoding="utf-8")
        recorder.register_artifact(lineage_path)

        # Generate trace analysis report if trace context exists
        trace_report_path = None
        if trace_ctx:
            trace_ctx.current_span.set_attribute("validation_passed", True)
            trace_ctx.current_span.set_attribute("report_file", report_path.name)
            trace_ctx.current_span.set_attribute("findings_count", len(session.findings))
            trace_ctx.end_current_span(status="completed")

            trace_ctx.end_trace()

            from langgraph_langchain.trace_analyzer import TraceAnalyzer
            analyzer = TraceAnalyzer(trace_ctx)

            trace_report = analyzer.generate_summary_report()
            trace_report_path = session.workspace_dir / "trace_analysis.txt"
            trace_report_path.write_text(trace_report, encoding="utf-8")
            recorder.register_artifact(trace_report_path)

            dashboard_data = analyzer.export_for_dashboard()
            dashboard_path = session.workspace_dir / "trace_dashboard.json"
            dashboard_path.write_text(json.dumps(dashboard_data, ensure_ascii=False, indent=2), encoding="utf-8")
            recorder.register_artifact(dashboard_path)

        session.structured_logger.log_tool_result(
            tool_name="finish_report",
            success=True,
            outputs={
                "report_file": report_path.name,
                "findings_count": len(session.findings),
                "report_length": len(markdown),
                "lineage_file": lineage_path.name,
                "trace_analysis_file": trace_report_path.name if trace_report_path else None,
            },
        )

        result = "Report submitted successfully."
        recorder.succeed(result)
        return result

    return finish_report


registry.register(
    name="finish_report",
    toolset="analysis",
    factory=_factory,
    description="Submit the final analysis report. Call exactly once when analysis is complete.",
    emoji="📄",
)
