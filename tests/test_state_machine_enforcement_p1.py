"""
Test P1.1: State Machine Enforcement

Verify that the state machine properly enforces stage transitions and prevents
invalid tool calls in wrong stages.
"""

import pytest
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from langgraph_langchain.state_machine import AnalysisStage, AnalysisStateMachine


class TestStateMachineEnforcement:
    """Test state machine enforcement in analysis workflow"""

    def test_initial_state_is_init(self):
        """State machine should start in INIT stage"""
        sm = AnalysisStateMachine()
        assert sm.current_stage == AnalysisStage.INIT
        assert len(sm.stage_history) == 1
        assert sm.stage_history[0] == AnalysisStage.INIT

    def test_valid_transition_from_init_to_schema_understanding(self):
        """Should allow transition from INIT to SCHEMA_UNDERSTANDING"""
        sm = AnalysisStateMachine()
        can_transition, reason = sm.can_transition_to(AnalysisStage.SCHEMA_UNDERSTANDING)
        assert can_transition is True
        assert reason is None

        success = sm.transition_to(AnalysisStage.SCHEMA_UNDERSTANDING)
        assert success is True
        assert sm.current_stage == AnalysisStage.SCHEMA_UNDERSTANDING

    def test_invalid_transition_from_init_to_deep_dive(self):
        """Should prevent skipping stages (INIT -> DEEP_DIVE)"""
        sm = AnalysisStateMachine()
        can_transition, reason = sm.can_transition_to(AnalysisStage.DEEP_DIVE)
        assert can_transition is False
        assert "Cannot transition" in reason

    def test_transition_requires_entry_conditions(self):
        """Should enforce entry conditions for stage transitions"""
        sm = AnalysisStateMachine()

        # Transition to SCHEMA_UNDERSTANDING
        sm.transition_to(AnalysisStage.SCHEMA_UNDERSTANDING)

        # Try to transition to DATA_QUALITY_CHECK without meeting conditions
        can_transition, reason = sm.can_transition_to(AnalysisStage.DATA_QUALITY_CHECK)
        assert can_transition is False
        assert "Missing entry conditions" in reason
        assert "schema_documented" in reason

    def test_transition_succeeds_after_conditions_met(self):
        """Should allow transition after entry conditions are met"""
        sm = AnalysisStateMachine()

        # Transition to SCHEMA_UNDERSTANDING
        sm.transition_to(AnalysisStage.SCHEMA_UNDERSTANDING)

        # Record tool use and add conditions
        sm.record_tool_use("load_data")
        sm.add_condition("schema_documented")
        sm.add_condition("fields_understood")

        # Now transition should succeed
        can_transition, reason = sm.can_transition_to(AnalysisStage.DATA_QUALITY_CHECK)
        assert can_transition is True
        assert reason is None

        success = sm.transition_to(AnalysisStage.DATA_QUALITY_CHECK)
        assert success is True
        assert sm.current_stage == AnalysisStage.DATA_QUALITY_CHECK

    def test_full_workflow_progression(self):
        """Test complete workflow from INIT to COMPLETED"""
        sm = AnalysisStateMachine()

        # Stage 1: INIT -> SCHEMA_UNDERSTANDING
        sm.transition_to(AnalysisStage.SCHEMA_UNDERSTANDING)
        sm.record_tool_use("load_data")
        sm.add_condition("schema_documented")
        sm.add_condition("fields_understood")

        # Stage 2: SCHEMA_UNDERSTANDING -> DATA_QUALITY_CHECK
        sm.transition_to(AnalysisStage.DATA_QUALITY_CHECK)
        sm.record_tool_use("eda_profile")
        sm.add_condition("quality_assessed")
        sm.add_condition("issues_documented")

        # Stage 3: DATA_QUALITY_CHECK -> BASIC_EDA
        sm.transition_to(AnalysisStage.BASIC_EDA)
        sm.add_condition("distributions_analyzed")
        sm.add_condition("correlations_checked")

        # Stage 4: BASIC_EDA -> DEEP_DIVE
        sm.transition_to(AnalysisStage.DEEP_DIVE)
        sm.record_tool_use("record_finding")
        sm.add_condition("question_addressed")
        sm.add_condition("findings_recorded")

        # Stage 5: DEEP_DIVE -> CONCLUSION_SYNTHESIS
        sm.transition_to(AnalysisStage.CONCLUSION_SYNTHESIS)
        sm.add_condition("findings_organized")
        sm.add_condition("evidence_linked")
        sm.add_condition("min_findings_count")

        # Stage 6: CONCLUSION_SYNTHESIS -> REPORT_GENERATION
        sm.transition_to(AnalysisStage.REPORT_GENERATION)
        sm.record_tool_use("finish_report")
        sm.add_condition("report_generated")

        # Stage 7: REPORT_GENERATION -> COMPLETED
        can_transition, _ = sm.can_transition_to(AnalysisStage.COMPLETED)
        assert can_transition is True
        sm.transition_to(AnalysisStage.COMPLETED)

        assert sm.current_stage == AnalysisStage.COMPLETED
        assert len(sm.stage_history) == 8  # INIT + 7 transitions

    def test_stage_step_counting(self):
        """Should track steps per stage"""
        sm = AnalysisStateMachine()
        sm.transition_to(AnalysisStage.SCHEMA_UNDERSTANDING)

        # Record multiple steps
        sm.record_step()
        sm.record_step()
        sm.record_step()

        assert sm.stage_step_count[AnalysisStage.SCHEMA_UNDERSTANDING] == 3

    def test_stage_limits_enforcement(self):
        """Should detect when stage exceeds max steps"""
        sm = AnalysisStateMachine()
        sm.transition_to(AnalysisStage.SCHEMA_UNDERSTANDING)

        # SCHEMA_UNDERSTANDING has max_steps=3
        sm.record_step()
        sm.record_step()
        sm.record_step()

        # Within limits
        within_limits, warning = sm.check_stage_limits()
        assert within_limits is True

        # Exceed limits
        sm.record_step()
        within_limits, warning = sm.check_stage_limits()
        assert within_limits is False
        assert "exceeded max steps" in warning

    def test_get_next_recommended_stage(self):
        """Should recommend next stage based on current state"""
        sm = AnalysisStateMachine()

        # From INIT, should recommend SCHEMA_UNDERSTANDING
        next_stage = sm.get_next_recommended_stage()
        assert next_stage == AnalysisStage.SCHEMA_UNDERSTANDING

        # After transitioning and meeting conditions
        sm.transition_to(AnalysisStage.SCHEMA_UNDERSTANDING)
        sm.add_condition("schema_documented")
        sm.add_condition("fields_understood")

        next_stage = sm.get_next_recommended_stage()
        assert next_stage == AnalysisStage.DATA_QUALITY_CHECK

    def test_tool_usage_tracking(self):
        """Should track which tools have been used"""
        sm = AnalysisStateMachine()

        assert len(sm.tools_used) == 0

        sm.record_tool_use("load_data")
        assert "load_data" in sm.tools_used

        sm.record_tool_use("eda_profile")
        assert "eda_profile" in sm.tools_used
        assert len(sm.tools_used) == 2

    def test_auto_condition_detection_from_tools(self):
        """Should automatically add conditions based on tool usage"""
        sm = AnalysisStateMachine()

        # load_data should auto-add "data_loaded"
        sm.record_tool_use("load_data")
        assert "data_loaded" in sm.conditions_met

        # eda_profile should auto-add "quality_assessed"
        sm.record_tool_use("eda_profile")
        assert "quality_assessed" in sm.conditions_met

        # record_finding should auto-add "findings_recorded"
        sm.record_tool_use("record_finding")
        assert "findings_recorded" in sm.conditions_met

        # finish_report should auto-add "report_generated"
        sm.record_tool_use("finish_report")
        assert "report_generated" in sm.conditions_met

    def test_cannot_skip_to_report_without_findings(self):
        """Should prevent generating report without sufficient findings"""
        sm = AnalysisStateMachine()

        # Fast-forward to CONCLUSION_SYNTHESIS
        sm.transition_to(AnalysisStage.SCHEMA_UNDERSTANDING)
        sm.add_condition("schema_documented")
        sm.add_condition("fields_understood")
        sm.transition_to(AnalysisStage.DATA_QUALITY_CHECK)
        sm.add_condition("quality_assessed")
        sm.add_condition("issues_documented")
        sm.transition_to(AnalysisStage.BASIC_EDA)
        sm.add_condition("distributions_analyzed")
        sm.add_condition("correlations_checked")
        sm.transition_to(AnalysisStage.DEEP_DIVE)
        sm.add_condition("question_addressed")
        sm.add_condition("findings_recorded")
        sm.transition_to(AnalysisStage.CONCLUSION_SYNTHESIS)
        sm.add_condition("findings_organized")
        sm.add_condition("evidence_linked")

        # Try to transition to REPORT_GENERATION without min_findings_count
        can_transition, reason = sm.can_transition_to(AnalysisStage.REPORT_GENERATION)
        assert can_transition is False
        assert "min_findings_count" in reason

    def test_get_stage_progress(self):
        """Should provide comprehensive progress information"""
        sm = AnalysisStateMachine()
        sm.transition_to(AnalysisStage.SCHEMA_UNDERSTANDING)
        sm.record_tool_use("load_data")
        sm.add_condition("schema_documented")  # Add exit condition for SCHEMA_UNDERSTANDING
        sm.add_condition("fields_understood")  # Add exit condition for SCHEMA_UNDERSTANDING
        sm.record_step()
        sm.record_step()

        progress = sm.get_stage_progress()

        assert progress["current_stage"] == "schema_understanding"
        assert "init" in progress["stage_history"]
        assert "schema_understanding" in progress["stage_history"]
        assert "data_loaded" in progress["conditions_met"]
        assert "load_data" in progress["tools_used"]
        assert progress["stage_step_counts"]["schema_understanding"] == 2
        assert progress["next_recommended_stage"] == "data_quality_check"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
