"""
Analysis State Machine for Data Analysis Agent

Defines the fixed stages of analysis and transition rules to ensure
consistent and predictable analysis flow.
"""

from enum import Enum
from typing import Dict, List, Optional, Set
from dataclasses import dataclass


class AnalysisStage(str, Enum):
    """Fixed stages of data analysis"""
    INIT = "init"
    SCHEMA_UNDERSTANDING = "schema_understanding"
    DATA_QUALITY_CHECK = "data_quality_check"
    BASIC_EDA = "basic_eda"
    DEEP_DIVE = "deep_dive"
    CONCLUSION_SYNTHESIS = "conclusion_synthesis"
    REPORT_GENERATION = "report_generation"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class StageRequirements:
    """Requirements for entering and exiting a stage"""
    entry_conditions: List[str]
    exit_conditions: List[str]
    required_tools: List[str]
    optional_tools: List[str]
    min_steps: int
    max_steps: int
    can_skip: bool = False


class AnalysisStateMachine:
    """
    State machine for data analysis workflow.

    Ensures that analysis follows a predictable path:
    1. Schema Understanding - understand data structure and fields
    2. Data Quality Check - check for missing values, duplicates, anomalies
    3. Basic EDA - exploratory data analysis, distributions, correlations
    4. Deep Dive - focused analysis based on question
    5. Conclusion Synthesis - organize findings and evidence
    6. Report Generation - generate final report
    """

    # Define stage requirements
    STAGE_REQUIREMENTS: Dict[AnalysisStage, StageRequirements] = {
        AnalysisStage.SCHEMA_UNDERSTANDING: StageRequirements(
            entry_conditions=[],  # No conditions needed to enter, but must call load_data inside
            exit_conditions=["schema_documented", "fields_understood"],
            required_tools=["load_data"],
            optional_tools=["python_repl"],
            min_steps=1,
            max_steps=3,
            can_skip=False,
        ),
        AnalysisStage.DATA_QUALITY_CHECK: StageRequirements(
            entry_conditions=["schema_documented"],
            exit_conditions=["quality_assessed", "issues_documented"],
            required_tools=["eda_profile"],
            optional_tools=["python_repl"],
            min_steps=1,
            max_steps=5,
            can_skip=False,
        ),
        AnalysisStage.BASIC_EDA: StageRequirements(
            entry_conditions=["quality_assessed"],
            exit_conditions=["distributions_analyzed", "correlations_checked"],
            required_tools=[],
            optional_tools=[
                "compare_groups",
                "analyze_time_trend",
                "detect_anomalies",
                "decompose_contribution",
                "python_repl",
                "declare_metric",
                "declare_assumption",
            ],
            min_steps=2,
            max_steps=10,
            can_skip=False,
        ),
        AnalysisStage.DEEP_DIVE: StageRequirements(
            entry_conditions=["distributions_analyzed"],
            exit_conditions=["question_addressed", "findings_recorded"],
            required_tools=["record_finding"],
            optional_tools=[
                "compare_groups",
                "analyze_time_trend",
                "detect_anomalies",
                "decompose_contribution",
                "python_repl",
                "declare_metric",
                "declare_assumption",
            ],
            min_steps=3,
            max_steps=20,
            can_skip=False,
        ),
        AnalysisStage.CONCLUSION_SYNTHESIS: StageRequirements(
            entry_conditions=["findings_recorded"],
            exit_conditions=["findings_organized", "evidence_linked"],
            required_tools=["record_finding"],
            optional_tools=["decompose_contribution", "python_repl"],
            min_steps=1,
            max_steps=5,
            can_skip=False,
        ),
        AnalysisStage.REPORT_GENERATION: StageRequirements(
            entry_conditions=["findings_organized", "min_findings_count"],
            exit_conditions=["report_generated"],
            required_tools=["finish_report"],
            optional_tools=[],
            min_steps=1,
            max_steps=2,
            can_skip=False,
        ),
    }

    # Define valid transitions
    VALID_TRANSITIONS: Dict[AnalysisStage, Set[AnalysisStage]] = {
        AnalysisStage.INIT: {AnalysisStage.SCHEMA_UNDERSTANDING},
        AnalysisStage.SCHEMA_UNDERSTANDING: {
            AnalysisStage.DATA_QUALITY_CHECK,
            AnalysisStage.FAILED,  # Can fail if schema is incomprehensible
        },
        AnalysisStage.DATA_QUALITY_CHECK: {
            AnalysisStage.BASIC_EDA,
            AnalysisStage.FAILED,  # Can fail if data quality is too poor
        },
        AnalysisStage.BASIC_EDA: {
            AnalysisStage.DEEP_DIVE,
            AnalysisStage.CONCLUSION_SYNTHESIS,  # Can skip deep dive if question is simple
        },
        AnalysisStage.DEEP_DIVE: {
            AnalysisStage.CONCLUSION_SYNTHESIS,
            AnalysisStage.BASIC_EDA,  # Can go back if need more context
        },
        AnalysisStage.CONCLUSION_SYNTHESIS: {
            AnalysisStage.REPORT_GENERATION,
            AnalysisStage.DEEP_DIVE,  # Can go back if findings are insufficient
        },
        AnalysisStage.REPORT_GENERATION: {
            AnalysisStage.COMPLETED,
            AnalysisStage.CONCLUSION_SYNTHESIS,  # Can go back if report is rejected
        },
        AnalysisStage.COMPLETED: set(),
        AnalysisStage.FAILED: {
            AnalysisStage.SCHEMA_UNDERSTANDING,  # Recovery: restart from data loading
            AnalysisStage.BASIC_EDA,             # Recovery: retry with simpler scope
        },
    }

    def __init__(self):
        self.current_stage = AnalysisStage.INIT
        self.stage_history: List[AnalysisStage] = [AnalysisStage.INIT]
        self.conditions_met: Set[str] = set()
        self.tools_used: List[str] = []
        self.stage_step_count: Dict[AnalysisStage, int] = {}

    def can_transition_to(self, target_stage: AnalysisStage) -> tuple[bool, Optional[str]]:
        """
        Check if transition to target stage is valid.

        Returns:
            (is_valid, reason_if_invalid)
        """
        # Check if transition is allowed
        if target_stage not in self.VALID_TRANSITIONS.get(self.current_stage, set()):
            return False, f"Cannot transition from {self.current_stage.value} to {target_stage.value}"

        # Check if entry conditions are met
        if target_stage in self.STAGE_REQUIREMENTS:
            requirements = self.STAGE_REQUIREMENTS[target_stage]
            missing_conditions = [
                cond for cond in requirements.entry_conditions
                if cond not in self.conditions_met
            ]
            if missing_conditions:
                return False, f"Missing entry conditions: {', '.join(missing_conditions)}"

        return True, None

    def transition_to(self, target_stage: AnalysisStage) -> bool:
        """
        Attempt to transition to target stage.

        Returns:
            True if transition succeeded, False otherwise
        """
        can_transition, reason = self.can_transition_to(target_stage)
        if not can_transition:
            return False

        self.current_stage = target_stage
        self.stage_history.append(target_stage)
        self.stage_step_count[target_stage] = 0
        return True

    def recover_from_failure(self, strategy: str = "restart") -> bool:
        """Attempt to recover from FAILED state.

        Args:
            strategy: Recovery strategy.
                - "restart": Go back to SCHEMA_UNDERSTANDING (full retry)
                - "simpler": Go back to BASIC_EDA (retry with narrower scope)

        Returns:
            True if recovery transition succeeded.
        """
        if self.current_stage != AnalysisStage.FAILED:
            return False

        target = (
            AnalysisStage.BASIC_EDA if strategy == "simpler"
            else AnalysisStage.SCHEMA_UNDERSTANDING
        )

        success = self.transition_to(target)
        if success:
            # Reset step count for the target stage
            self.stage_step_count[target] = 0
            # Keep conditions from previous run (data_loaded, etc.)
        return success

    def record_tool_use(self, tool_name: str):
        """Record that a tool was used"""
        self.tools_used.append(tool_name)

        # Auto-detect conditions based on tool usage
        if tool_name == "load_data":
            self.conditions_met.add("data_loaded")
        elif tool_name == "eda_profile":
            self.conditions_met.add("quality_assessed")
        elif tool_name == "record_finding":
            self.conditions_met.add("findings_recorded")
        elif tool_name == "finish_report":
            self.conditions_met.add("report_generated")

    def record_step(self):
        """Record that a step was executed in current stage"""
        if self.current_stage not in self.stage_step_count:
            self.stage_step_count[self.current_stage] = 0
        self.stage_step_count[self.current_stage] += 1

    def add_condition(self, condition: str):
        """Manually add a condition as met"""
        self.conditions_met.add(condition)

    def check_stage_limits(self) -> tuple[bool, Optional[str]]:
        """
        Check if current stage has exceeded step limits.

        Returns:
            (is_within_limits, warning_if_exceeded)
        """
        if self.current_stage not in self.STAGE_REQUIREMENTS:
            return True, None

        requirements = self.STAGE_REQUIREMENTS[self.current_stage]
        step_count = self.stage_step_count.get(self.current_stage, 0)

        if step_count < requirements.min_steps:
            return True, None

        if step_count > requirements.max_steps:
            return False, f"Stage {self.current_stage.value} has exceeded max steps ({requirements.max_steps})"

        return True, None

    def get_next_recommended_stage(self) -> Optional[AnalysisStage]:
        """Get the recommended next stage based on current state.

        Only non-terminal stages are ever recommended. Terminal stages are
        reached explicitly, never via auto-advance:

        * ``COMPLETED`` is entered by ``finish_report`` calling
          ``transition_to`` directly.
        * ``FAILED`` is entered only by an explicit ``fail_stage`` call
          (cancellation, max-steps exceeded, repeated python_repl errors).

        Previously this fell back to ``FAILED`` when no non-terminal
        transition was possible. Because ``FAILED`` has no entry conditions,
        that fallback always succeeded - so any tool that called
        ``try_advance_stage`` before the next stage's entry conditions were
        met (e.g. ``load_data`` called twice, or ``eda_profile`` called while
        still in ``SCHEMA_UNDERSTANDING``) would auto-advance the agent into
        ``FAILED``, after which every tool call is rejected by the stage
        validator and the agent spirals in a failed loop. Returning ``None``
        here leaves the stage unchanged so analysis can continue.
        """
        # Auto-advance is forward-only and deterministic. VALID_TRANSITIONS
        # also contains recovery edges; iterating that set made a normal run
        # randomly move backwards depending on hash order.
        forward_candidates = {
            AnalysisStage.INIT: (AnalysisStage.SCHEMA_UNDERSTANDING,),
            AnalysisStage.SCHEMA_UNDERSTANDING: (AnalysisStage.DATA_QUALITY_CHECK,),
            AnalysisStage.DATA_QUALITY_CHECK: (AnalysisStage.BASIC_EDA,),
            AnalysisStage.BASIC_EDA: (
                AnalysisStage.DEEP_DIVE,
                AnalysisStage.CONCLUSION_SYNTHESIS,
            ),
            AnalysisStage.DEEP_DIVE: (AnalysisStage.CONCLUSION_SYNTHESIS,),
            AnalysisStage.CONCLUSION_SYNTHESIS: (AnalysisStage.REPORT_GENERATION,),
        }
        for stage in forward_candidates.get(self.current_stage, ()):
            can_transition, _ = self.can_transition_to(stage)
            if can_transition:
                return stage
        return None

    def get_stage_progress(self) -> Dict[str, any]:
        """Get current progress information"""
        return {
            "current_stage": self.current_stage.value,
            "stage_history": [s.value for s in self.stage_history],
            "conditions_met": list(self.conditions_met),
            "tools_used": self.tools_used,
            "stage_step_counts": {
                stage.value: count
                for stage, count in self.stage_step_count.items()
            },
            "next_recommended_stage": (
                self.get_next_recommended_stage().value
                if self.get_next_recommended_stage()
                else None
            ),
        }
