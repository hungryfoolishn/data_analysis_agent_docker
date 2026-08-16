"""
Tool-level validators for enforcing state machine constraints.

Each tool has specific stage requirements. This module validates that tools
are only called in appropriate stages.
"""

from typing import Optional, Tuple
from langgraph_langchain.state_machine import AnalysisStage


class ToolStageValidator:
    """Validates that tools are called in appropriate analysis stages"""

    # Define which stages each tool is allowed in
    TOOL_STAGE_REQUIREMENTS = {
        "load_data": {
            "allowed_stages": [AnalysisStage.INIT, AnalysisStage.SCHEMA_UNDERSTANDING],
            "reason": "Data loading should happen at the beginning of analysis"
        },
        "eda_profile": {
            "allowed_stages": [
                AnalysisStage.SCHEMA_UNDERSTANDING,
                AnalysisStage.DATA_QUALITY_CHECK,
                AnalysisStage.BASIC_EDA
            ],
            "reason": "EDA profiling should happen after data is loaded"
        },
        "python_repl": {
            "allowed_stages": [
                AnalysisStage.SCHEMA_UNDERSTANDING,
                AnalysisStage.DATA_QUALITY_CHECK,
                AnalysisStage.BASIC_EDA,
                AnalysisStage.DEEP_DIVE,
                AnalysisStage.CONCLUSION_SYNTHESIS
            ],
            "reason": "Python code can be used throughout analysis"
        },
        "compare_groups": {
            "allowed_stages": [AnalysisStage.BASIC_EDA, AnalysisStage.DEEP_DIVE],
            "reason": "Grouped comparison requires a loaded and profiled dataset"
        },
        "analyze_time_trend": {
            "allowed_stages": [AnalysisStage.BASIC_EDA, AnalysisStage.DEEP_DIVE],
            "reason": "Trend analysis requires a loaded and profiled dataset"
        },
        "detect_anomalies": {
            "allowed_stages": [AnalysisStage.BASIC_EDA, AnalysisStage.DEEP_DIVE],
            "reason": "Anomaly detection requires a loaded and profiled dataset"
        },
        "decompose_contribution": {
            "allowed_stages": [AnalysisStage.BASIC_EDA, AnalysisStage.DEEP_DIVE, AnalysisStage.CONCLUSION_SYNTHESIS],
            "reason": "Contribution decomposition belongs to focused analysis and synthesis"
        },
        "record_finding": {
            "allowed_stages": [
                AnalysisStage.BASIC_EDA,
                AnalysisStage.DEEP_DIVE,
                AnalysisStage.CONCLUSION_SYNTHESIS
            ],
            "reason": "Findings should be recorded after initial exploration"
        },
        "declare_metric": {
            "allowed_stages": [
                AnalysisStage.SCHEMA_UNDERSTANDING,
                AnalysisStage.DATA_QUALITY_CHECK,
                AnalysisStage.BASIC_EDA,
                AnalysisStage.DEEP_DIVE
            ],
            "reason": "Metrics should be declared during analysis setup and exploration"
        },
        "declare_assumption": {
            "allowed_stages": [
                AnalysisStage.SCHEMA_UNDERSTANDING,
                AnalysisStage.DATA_QUALITY_CHECK,
                AnalysisStage.BASIC_EDA,
                AnalysisStage.DEEP_DIVE
            ],
            "reason": "Assumptions should be declared during analysis"
        },
        "finish_report": {
            "allowed_stages": [
                AnalysisStage.CONCLUSION_SYNTHESIS,
                AnalysisStage.REPORT_GENERATION
            ],
            "reason": "Report should only be generated after findings are synthesized"
        },
        "memory": {
            "allowed_stages": [
                AnalysisStage.SCHEMA_UNDERSTANDING,
                AnalysisStage.DATA_QUALITY_CHECK,
                AnalysisStage.BASIC_EDA,
                AnalysisStage.DEEP_DIVE,
                AnalysisStage.CONCLUSION_SYNTHESIS,
            ],
            "reason": "Memory can be used throughout analysis to persist observations"
        },
        "skill_view": {
            "allowed_stages": [
                AnalysisStage.SCHEMA_UNDERSTANDING,
                AnalysisStage.DATA_QUALITY_CHECK,
                AnalysisStage.BASIC_EDA,
                AnalysisStage.DEEP_DIVE,
                AnalysisStage.CONCLUSION_SYNTHESIS,
            ],
            "reason": "Skills can be loaded at any stage after data is loaded"
        },
        "skill_reference": {
            "allowed_stages": [
                AnalysisStage.SCHEMA_UNDERSTANDING,
                AnalysisStage.DATA_QUALITY_CHECK,
                AnalysisStage.BASIC_EDA,
                AnalysisStage.DEEP_DIVE,
                AnalysisStage.CONCLUSION_SYNTHESIS,
            ],
            "reason": "Skill references can be loaded at any stage after data is loaded"
        },
        "delegate_analysis": {
            "allowed_stages": [
                AnalysisStage.BASIC_EDA,
                AnalysisStage.DEEP_DIVE,
                AnalysisStage.CONCLUSION_SYNTHESIS,
            ],
            "reason": "Sub-task delegation requires data to be loaded and understood first"
        }
    }

    # Define minimum requirements before certain tools can be used
    TOOL_PREREQUISITES = {
        "eda_profile": {
            "required_tools": ["load_data"],
            "reason": "Must load data before running EDA"
        },
        "record_finding": {
            "required_tools": ["load_data", "eda_profile"],
            "reason": "Must understand data before recording findings"
        },
        "finish_report": {
            "required_tools": ["load_data", "eda_profile"],
            "min_findings": 3,
            "reason": "Must have sufficient findings before generating report"
        },
        "delegate_analysis": {
            "required_tools": ["load_data"],
            "reason": "Must load data before delegating analysis sub-tasks"
        },
        "compare_groups": {
            "required_tools": ["load_data", "eda_profile"],
            "reason": "Must profile data before grouped comparison"
        },
        "analyze_time_trend": {
            "required_tools": ["load_data", "eda_profile"],
            "reason": "Must profile data before trend analysis"
        },
        "detect_anomalies": {
            "required_tools": ["load_data", "eda_profile"],
            "reason": "Must profile data before anomaly detection"
        },
        "decompose_contribution": {
            "required_tools": ["load_data", "eda_profile"],
            "reason": "Must profile data before contribution decomposition"
        }
    }

    @classmethod
    def validate_tool_call(
        cls,
        tool_name: str,
        current_stage: AnalysisStage,
        tools_used: list[str],
        findings_count: int = 0
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate if a tool can be called in the current stage.

        Returns:
            (is_valid, error_message)
        """
        # Check stage requirements
        if tool_name in cls.TOOL_STAGE_REQUIREMENTS:
            requirements = cls.TOOL_STAGE_REQUIREMENTS[tool_name]
            allowed_stages = requirements["allowed_stages"]

            if current_stage not in allowed_stages:
                allowed_names = [s.value for s in allowed_stages]
                return False, (
                    f"Tool '{tool_name}' cannot be called in stage '{current_stage.value}'. "
                    f"Allowed stages: {', '.join(allowed_names)}. "
                    f"Reason: {requirements['reason']}"
                )

        # Check prerequisites
        if tool_name in cls.TOOL_PREREQUISITES:
            prereqs = cls.TOOL_PREREQUISITES[tool_name]

            # Check required tools
            if "required_tools" in prereqs:
                required = prereqs["required_tools"]
                missing = [t for t in required if t not in tools_used]
                if missing:
                    return False, (
                        f"Tool '{tool_name}' requires these tools to be called first: {', '.join(missing)}. "
                        f"Reason: {prereqs['reason']}"
                    )

            # Check minimum findings
            if "min_findings" in prereqs:
                min_findings = prereqs["min_findings"]
                if findings_count < min_findings:
                    return False, (
                        f"Tool '{tool_name}' requires at least {min_findings} findings to be recorded. "
                        f"Currently have {findings_count}. "
                        f"Reason: {prereqs['reason']}"
                    )

        return True, None

    @classmethod
    def get_allowed_tools(cls, current_stage: AnalysisStage) -> list[str]:
        """Get list of tools allowed in current stage"""
        allowed = []
        for tool_name, requirements in cls.TOOL_STAGE_REQUIREMENTS.items():
            if current_stage in requirements["allowed_stages"]:
                allowed.append(tool_name)
        return allowed

    @classmethod
    def get_next_recommended_tool(
        cls,
        current_stage: AnalysisStage,
        tools_used: list[str]
    ) -> Optional[str]:
        """Recommend next tool based on current stage and tools used"""

        # Stage-based recommendations
        if current_stage == AnalysisStage.INIT:
            return "load_data"

        if current_stage == AnalysisStage.SCHEMA_UNDERSTANDING:
            if "load_data" not in tools_used:
                return "load_data"
            if "eda_profile" not in tools_used:
                return "eda_profile"

        if current_stage == AnalysisStage.DATA_QUALITY_CHECK:
            if "eda_profile" not in tools_used:
                return "eda_profile"

        if current_stage == AnalysisStage.BASIC_EDA:
            return "compare_groups"

        if current_stage == AnalysisStage.DEEP_DIVE:
            return "record_finding"

        if current_stage == AnalysisStage.CONCLUSION_SYNTHESIS:
            return "record_finding"

        if current_stage == AnalysisStage.REPORT_GENERATION:
            return "finish_report"

        return None
