"""
Tests for state machine enforcement in tool calls.

Validates that tools can only be called in appropriate stages and that
prerequisites are checked before execution.
"""

import pytest
from langgraph_langchain.state_machine import AnalysisStage
from langgraph_langchain.tool_validators import ToolStageValidator


class TestToolStageValidation:
    """Test tool stage validation logic"""

    def test_load_data_allowed_in_init(self):
        """load_data should be allowed in INIT stage"""
        is_valid, error = ToolStageValidator.validate_tool_call(
            tool_name="load_data",
            current_stage=AnalysisStage.INIT,
            tools_used=[],
            findings_count=0
        )
        assert is_valid
        assert error is None

    def test_load_data_allowed_in_schema_understanding(self):
        """load_data should be allowed in SCHEMA_UNDERSTANDING stage"""
        is_valid, error = ToolStageValidator.validate_tool_call(
            tool_name="load_data",
            current_stage=AnalysisStage.SCHEMA_UNDERSTANDING,
            tools_used=[],
            findings_count=0
        )
        assert is_valid
        assert error is None

    def test_load_data_rejected_in_deep_dive(self):
        """load_data should be rejected in DEEP_DIVE stage"""
        is_valid, error = ToolStageValidator.validate_tool_call(
            tool_name="load_data",
            current_stage=AnalysisStage.DEEP_DIVE,
            tools_used=["load_data", "eda_profile"],
            findings_count=0
        )
        assert not is_valid
        assert "cannot be called in stage 'deep_dive'" in error.lower()

    def test_eda_profile_requires_load_data(self):
        """eda_profile should require load_data to be called first"""
        is_valid, error = ToolStageValidator.validate_tool_call(
            tool_name="eda_profile",
            current_stage=AnalysisStage.SCHEMA_UNDERSTANDING,
            tools_used=[],  # load_data not called yet
            findings_count=0
        )
        assert not is_valid
        assert "load_data" in error

    def test_eda_profile_allowed_after_load_data(self):
        """eda_profile should be allowed after load_data"""
        is_valid, error = ToolStageValidator.validate_tool_call(
            tool_name="eda_profile",
            current_stage=AnalysisStage.SCHEMA_UNDERSTANDING,
            tools_used=["load_data"],
            findings_count=0
        )
        assert is_valid
        assert error is None

    def test_record_finding_rejected_in_schema_understanding(self):
        """record_finding should be rejected in SCHEMA_UNDERSTANDING stage"""
        is_valid, error = ToolStageValidator.validate_tool_call(
            tool_name="record_finding",
            current_stage=AnalysisStage.SCHEMA_UNDERSTANDING,
            tools_used=["load_data"],
            findings_count=0
        )
        assert not is_valid
        assert "schema_understanding" in error.lower()

    def test_record_finding_allowed_in_basic_eda(self):
        """record_finding should be allowed in BASIC_EDA stage"""
        is_valid, error = ToolStageValidator.validate_tool_call(
            tool_name="record_finding",
            current_stage=AnalysisStage.BASIC_EDA,
            tools_used=["load_data", "eda_profile"],
            findings_count=0
        )
        assert is_valid
        assert error is None

    def test_record_finding_requires_prerequisites(self):
        """record_finding should require load_data and eda_profile"""
        is_valid, error = ToolStageValidator.validate_tool_call(
            tool_name="record_finding",
            current_stage=AnalysisStage.BASIC_EDA,
            tools_used=["load_data"],  # Missing eda_profile
            findings_count=0
        )
        assert not is_valid
        assert "eda_profile" in error

    def test_finish_report_rejected_in_basic_eda(self):
        """finish_report should be rejected in BASIC_EDA stage"""
        is_valid, error = ToolStageValidator.validate_tool_call(
            tool_name="finish_report",
            current_stage=AnalysisStage.BASIC_EDA,
            tools_used=["load_data", "eda_profile"],
            findings_count=5
        )
        assert not is_valid
        assert "basic_eda" in error.lower()

    def test_finish_report_allowed_in_conclusion_synthesis(self):
        """finish_report should be allowed in CONCLUSION_SYNTHESIS stage"""
        is_valid, error = ToolStageValidator.validate_tool_call(
            tool_name="finish_report",
            current_stage=AnalysisStage.CONCLUSION_SYNTHESIS,
            tools_used=["load_data", "eda_profile", "record_finding"],
            findings_count=3
        )
        assert is_valid
        assert error is None

    def test_finish_report_requires_minimum_findings(self):
        """finish_report should require at least 3 findings"""
        is_valid, error = ToolStageValidator.validate_tool_call(
            tool_name="finish_report",
            current_stage=AnalysisStage.CONCLUSION_SYNTHESIS,
            tools_used=["load_data", "eda_profile", "record_finding"],
            findings_count=2  # Only 2 findings, need 3
        )
        assert not is_valid
        assert "at least 3 findings" in error.lower()

    def test_python_repl_allowed_in_multiple_stages(self):
        """python_repl should be allowed in multiple stages"""
        allowed_stages = [
            AnalysisStage.SCHEMA_UNDERSTANDING,
            AnalysisStage.DATA_QUALITY_CHECK,
            AnalysisStage.BASIC_EDA,
            AnalysisStage.DEEP_DIVE,
            AnalysisStage.CONCLUSION_SYNTHESIS
        ]

        for stage in allowed_stages:
            is_valid, error = ToolStageValidator.validate_tool_call(
                tool_name="python_repl",
                current_stage=stage,
                tools_used=["load_data"],
                findings_count=0
            )
            assert is_valid, f"python_repl should be allowed in {stage.value}"

    def test_python_repl_rejected_in_report_generation(self):
        """python_repl should be rejected in REPORT_GENERATION stage"""
        is_valid, error = ToolStageValidator.validate_tool_call(
            tool_name="python_repl",
            current_stage=AnalysisStage.REPORT_GENERATION,
            tools_used=["load_data", "eda_profile"],
            findings_count=3
        )
        assert not is_valid
        assert "report_generation" in error.lower()

    def test_declare_metric_allowed_in_early_stages(self):
        """declare_metric should be allowed in early analysis stages"""
        allowed_stages = [
            AnalysisStage.SCHEMA_UNDERSTANDING,
            AnalysisStage.DATA_QUALITY_CHECK,
            AnalysisStage.BASIC_EDA,
            AnalysisStage.DEEP_DIVE
        ]

        for stage in allowed_stages:
            is_valid, error = ToolStageValidator.validate_tool_call(
                tool_name="declare_metric",
                current_stage=stage,
                tools_used=["load_data"],
                findings_count=0
            )
            assert is_valid, f"declare_metric should be allowed in {stage.value}"

    def test_declare_metric_rejected_in_conclusion_synthesis(self):
        """declare_metric should be rejected in CONCLUSION_SYNTHESIS stage"""
        is_valid, error = ToolStageValidator.validate_tool_call(
            tool_name="declare_metric",
            current_stage=AnalysisStage.CONCLUSION_SYNTHESIS,
            tools_used=["load_data", "eda_profile"],
            findings_count=3
        )
        assert not is_valid
        assert "conclusion_synthesis" in error.lower()


class TestGetAllowedTools:
    """Test getting allowed tools for each stage"""

    def test_init_stage_allowed_tools(self):
        """INIT stage should allow load_data"""
        allowed = ToolStageValidator.get_allowed_tools(AnalysisStage.INIT)
        assert "load_data" in allowed
        assert "eda_profile" not in allowed
        assert "record_finding" not in allowed

    def test_schema_understanding_allowed_tools(self):
        """SCHEMA_UNDERSTANDING stage should allow load_data, eda_profile, python_repl, declare_metric, declare_assumption"""
        allowed = ToolStageValidator.get_allowed_tools(AnalysisStage.SCHEMA_UNDERSTANDING)
        assert "load_data" in allowed
        assert "eda_profile" in allowed
        assert "python_repl" in allowed
        assert "declare_metric" in allowed
        assert "declare_assumption" in allowed
        assert "record_finding" not in allowed
        assert "finish_report" not in allowed

    def test_basic_eda_allowed_tools(self):
        """BASIC_EDA stage should allow python_repl, record_finding, declare_metric, declare_assumption"""
        allowed = ToolStageValidator.get_allowed_tools(AnalysisStage.BASIC_EDA)
        assert "python_repl" in allowed
        assert "record_finding" in allowed
        assert "declare_metric" in allowed
        assert "declare_assumption" in allowed
        assert "eda_profile" in allowed
        assert "finish_report" not in allowed

    def test_deep_dive_allowed_tools(self):
        """DEEP_DIVE stage should allow python_repl, record_finding, declare_metric, declare_assumption"""
        allowed = ToolStageValidator.get_allowed_tools(AnalysisStage.DEEP_DIVE)
        assert "python_repl" in allowed
        assert "record_finding" in allowed
        assert "declare_metric" in allowed
        assert "declare_assumption" in allowed
        assert "load_data" not in allowed
        assert "finish_report" not in allowed

    def test_conclusion_synthesis_allowed_tools(self):
        """CONCLUSION_SYNTHESIS stage should allow record_finding, python_repl"""
        allowed = ToolStageValidator.get_allowed_tools(AnalysisStage.CONCLUSION_SYNTHESIS)
        assert "record_finding" in allowed
        assert "python_repl" in allowed
        assert "finish_report" in allowed
        assert "declare_metric" not in allowed

    def test_report_generation_allowed_tools(self):
        """REPORT_GENERATION stage should only allow finish_report"""
        allowed = ToolStageValidator.get_allowed_tools(AnalysisStage.REPORT_GENERATION)
        assert "finish_report" in allowed
        assert "python_repl" not in allowed
        assert "record_finding" not in allowed


class TestGetNextRecommendedTool:
    """Test tool recommendations based on stage and history"""

    def test_init_stage_recommends_load_data(self):
        """INIT stage should recommend load_data"""
        recommended = ToolStageValidator.get_next_recommended_tool(
            current_stage=AnalysisStage.INIT,
            tools_used=[]
        )
        assert recommended == "load_data"

    def test_schema_understanding_recommends_load_data_first(self):
        """SCHEMA_UNDERSTANDING stage should recommend load_data if not called"""
        recommended = ToolStageValidator.get_next_recommended_tool(
            current_stage=AnalysisStage.SCHEMA_UNDERSTANDING,
            tools_used=[]
        )
        assert recommended == "load_data"

    def test_schema_understanding_recommends_eda_profile_after_load(self):
        """SCHEMA_UNDERSTANDING stage should recommend eda_profile after load_data"""
        recommended = ToolStageValidator.get_next_recommended_tool(
            current_stage=AnalysisStage.SCHEMA_UNDERSTANDING,
            tools_used=["load_data"]
        )
        assert recommended == "eda_profile"

    def test_data_quality_check_recommends_eda_profile(self):
        """DATA_QUALITY_CHECK stage should recommend eda_profile if not called"""
        recommended = ToolStageValidator.get_next_recommended_tool(
            current_stage=AnalysisStage.DATA_QUALITY_CHECK,
            tools_used=["load_data"]
        )
        assert recommended == "eda_profile"

    def test_basic_eda_recommends_python_repl(self):
        """BASIC_EDA stage should recommend python_repl"""
        recommended = ToolStageValidator.get_next_recommended_tool(
            current_stage=AnalysisStage.BASIC_EDA,
            tools_used=["load_data", "eda_profile"]
        )
        assert recommended == "python_repl"

    def test_deep_dive_recommends_record_finding(self):
        """DEEP_DIVE stage should recommend record_finding"""
        recommended = ToolStageValidator.get_next_recommended_tool(
            current_stage=AnalysisStage.DEEP_DIVE,
            tools_used=["load_data", "eda_profile", "python_repl"]
        )
        assert recommended == "record_finding"

    def test_conclusion_synthesis_recommends_record_finding(self):
        """CONCLUSION_SYNTHESIS stage should recommend record_finding"""
        recommended = ToolStageValidator.get_next_recommended_tool(
            current_stage=AnalysisStage.CONCLUSION_SYNTHESIS,
            tools_used=["load_data", "eda_profile", "python_repl"]
        )
        assert recommended == "record_finding"

    def test_report_generation_recommends_finish_report(self):
        """REPORT_GENERATION stage should recommend finish_report"""
        recommended = ToolStageValidator.get_next_recommended_tool(
            current_stage=AnalysisStage.REPORT_GENERATION,
            tools_used=["load_data", "eda_profile", "python_repl", "record_finding"]
        )
        assert recommended == "finish_report"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
