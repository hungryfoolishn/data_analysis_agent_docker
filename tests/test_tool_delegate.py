"""
Tests for delegate_analysis tool (P4: Sub-Agent Delegation).

Covers:
- Tool registration and discovery
- Stage validation (allowed / blocked stages)
- Delegation execution with code
- Nested delegation prevention
- Missing DataFrame guard
- Analysis type validation
- Timeout handling
- Artifact tracking
- Output truncation
"""
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from langgraph_langchain.tools.registry import registry
from langgraph_langchain.tool_validators import ToolStageValidator
from langgraph_langchain.state_machine import AnalysisStage, AnalysisStateMachine


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_df():
    return pd.DataFrame({
        "region": ["North", "South", "East", "West", "North", "South"],
        "product": ["A", "B", "A", "B", "C", "A"],
        "sales": [100, 200, 150, 300, 250, 180],
        "quarter": ["Q1", "Q1", "Q2", "Q2", "Q3", "Q3"],
    })


@pytest.fixture
def mock_session(sample_df, tmp_path):
    """Build a mock _Session with a loaded DataFrame."""
    session = MagicMock()
    session.workspace_dir = tmp_path
    session.session_id = "test-delegate"
    session.user_question = "Analyze sales by region"
    session.current_stage = "deep_dive"
    session.findings = []
    session.metric_definitions = []
    session.assumptions = []
    session.new_artifacts = []
    session.known_image_files = set()
    session.consecutive_python_errors = 0
    session._delegation_depth = 0

    # Namespace with df
    session.ns = {"df": sample_df, "WORKSPACE_DIR": str(tmp_path)}

    # State machine in DEEP_DIVE stage with load_data already used
    sm = AnalysisStateMachine()
    sm.current_stage = AnalysisStage.DEEP_DIVE
    sm.tools_used = ["load_data", "eda_profile"]
    sm.conditions_met = {"data_loaded", "schema_documented", "quality_assessed"}
    session.state_machine = sm

    # Logger mocks
    session.logger = MagicMock()
    session.structured_logger = MagicMock()

    return session


def _get_delegate_tool(session):
    """Build the delegate_analysis tool for a given session."""
    entry = registry._tools.get("delegate_analysis")
    assert entry is not None, "delegate_analysis not registered"
    return entry.factory(session)


# ── Registration ───────────────────────────────────────────────────────────────

class TestRegistration:

    def test_tool_is_registered(self):
        assert "delegate_analysis" in registry.get_tool_names()

    def test_toolset_is_analysis(self):
        assert registry.get_toolset_for_tool("delegate_analysis") == "analysis"

    def test_emoji_is_set(self):
        assert registry.get_emoji("delegate_analysis", default="") == "🔀"


# ── Stage validation ───────────────────────────────────────────────────────────

class TestStageValidation:

    def test_allowed_in_deep_dive(self):
        is_valid, _ = ToolStageValidator.validate_tool_call(
            "delegate_analysis",
            AnalysisStage.DEEP_DIVE,
            tools_used=["load_data"],
        )
        assert is_valid

    def test_allowed_in_basic_eda(self):
        is_valid, _ = ToolStageValidator.validate_tool_call(
            "delegate_analysis",
            AnalysisStage.BASIC_EDA,
            tools_used=["load_data"],
        )
        assert is_valid

    def test_allowed_in_conclusion_synthesis(self):
        is_valid, _ = ToolStageValidator.validate_tool_call(
            "delegate_analysis",
            AnalysisStage.CONCLUSION_SYNTHESIS,
            tools_used=["load_data"],
        )
        assert is_valid

    def test_blocked_in_schema_understanding(self):
        is_valid, error_msg = ToolStageValidator.validate_tool_call(
            "delegate_analysis",
            AnalysisStage.SCHEMA_UNDERSTANDING,
            tools_used=[],
        )
        assert not is_valid
        assert "delegate_analysis" in error_msg

    def test_blocked_in_report_generation(self):
        is_valid, _ = ToolStageValidator.validate_tool_call(
            "delegate_analysis",
            AnalysisStage.REPORT_GENERATION,
            tools_used=["load_data"],
        )
        assert not is_valid

    def test_requires_load_data_prerequisite(self):
        is_valid, error_msg = ToolStageValidator.validate_tool_call(
            "delegate_analysis",
            AnalysisStage.DEEP_DIVE,
            tools_used=[],
        )
        assert not is_valid
        assert "load_data" in error_msg


# ── Delegation execution ──────────────────────────────────────────────────────

class TestDelegationExecution:

    def test_basic_delegation(self, mock_session):
        tool_fn = _get_delegate_tool(mock_session)
        result = tool_fn.invoke({
            "task_description": "Sales summary by region",
            "analysis_type": "segment",
            "code": "print(df.groupby('region')['sales'].sum().to_dict())",
        })
        data = json.loads(result)
        assert data["success"] is True
        assert "Sales summary by region" in data["task"]
        assert data["analysis_type"] == "segment"
        assert "North" in data["output"]

    def test_trend_analysis(self, mock_session):
        tool_fn = _get_delegate_tool(mock_session)
        result = tool_fn.invoke({
            "task_description": "Quarterly trend",
            "analysis_type": "trend",
            "dimensions": ["quarter"],
            "code": "print(df.groupby('quarter')['sales'].sum())",
        })
        data = json.loads(result)
        assert data["success"] is True
        assert "Q1" in data["output"]

    def test_comparison_analysis(self, mock_session):
        tool_fn = _get_delegate_tool(mock_session)
        result = tool_fn.invoke({
            "task_description": "Top vs bottom products",
            "analysis_type": "comparison",
            "dimensions": ["product"],
            "code": "print(df.groupby('product')['sales'].agg(['mean','sum']))",
        })
        data = json.loads(result)
        assert data["success"] is True

    def test_sub_task_does_not_mutate_original_df(self, mock_session):
        original_len = len(mock_session.ns["df"])
        tool_fn = _get_delegate_tool(mock_session)
        tool_fn.invoke({
            "task_description": "Filter and modify",
            "code": "df = df[df['sales'] > 150]\nprint(len(df))",
        })
        # Original df should be unchanged
        assert len(mock_session.ns["df"]) == original_len

    def test_output_is_truncated_for_large_results(self, mock_session):
        tool_fn = _get_delegate_tool(mock_session)
        result = tool_fn.invoke({
            "task_description": "Large output",
            "code": "for i in range(10000): print(f'Row {i}: ' + 'x' * 50)",
        })
        data = json.loads(result)
        assert "truncated" in data["output"]

    def test_code_error_is_reported(self, mock_session):
        tool_fn = _get_delegate_tool(mock_session)
        result = tool_fn.invoke({
            "task_description": "Bad code",
            "code": "x = 1 / 0",
        })
        data = json.loads(result)
        assert data["success"] is False
        assert "[ERROR]" in data["output"]


# ── Input validation ──────────────────────────────────────────────────────────

class TestInputValidation:

    def test_empty_task_description_rejected(self, mock_session):
        tool_fn = _get_delegate_tool(mock_session)
        result = json.loads(tool_fn.invoke({
            "task_description": "",
            "code": "print('hi')",
        }))
        assert "error" in result

    def test_invalid_analysis_type_rejected(self, mock_session):
        tool_fn = _get_delegate_tool(mock_session)
        result = json.loads(tool_fn.invoke({
            "task_description": "Test",
            "analysis_type": "invalid_type",
            "code": "print('hi')",
        }))
        assert "error" in result
        assert "invalid_type" in result["error"]

    def test_no_code_rejected(self, mock_session):
        tool_fn = _get_delegate_tool(mock_session)
        result = json.loads(tool_fn.invoke({
            "task_description": "Analyze",
            "code": "",
        }))
        assert "error" in result

    def test_missing_df_rejected(self, mock_session):
        mock_session.ns = {"WORKSPACE_DIR": str(mock_session.workspace_dir)}
        tool_fn = _get_delegate_tool(mock_session)
        result = json.loads(tool_fn.invoke({
            "task_description": "Analyze",
            "code": "print(df.head())",
        }))
        assert "error" in result
        assert "DataFrame" in result["error"]


# ── Nested delegation prevention ───────────────────────────────────────────────

class TestNestedDelegationGuard:

    def test_nested_delegation_blocked(self, mock_session):
        mock_session._delegation_depth = 1  # Already at depth 1
        tool_fn = _get_delegate_tool(mock_session)
        result = json.loads(tool_fn.invoke({
            "task_description": "Nested task",
            "code": "print('nested')",
        }))
        assert "error" in result
        assert "Nested delegation" in result["error"]

    def test_depth_resets_after_execution(self, mock_session):
        tool_fn = _get_delegate_tool(mock_session)
        # Run a delegation
        tool_fn.invoke({
            "task_description": "Task 1",
            "code": "print(1)",
        })
        assert mock_session._delegation_depth == 0

    def test_depth_resets_on_error(self, mock_session):
        tool_fn = _get_delegate_tool(mock_session)
        tool_fn.invoke({
            "task_description": "Bad task",
            "code": "raise ValueError('boom')",
        })
        assert mock_session._delegation_depth == 0


# ── Artifact tracking ─────────────────────────────────────────────────────────

class TestArtifactTracking:

    def test_new_images_tracked(self, mock_session):
        # Add save_fig to namespace so the sub-task can save charts
        import matplotlib
        matplotlib.use("Agg")

        ws = mock_session.workspace_dir

        def _save_fig(filename):
            import matplotlib.pyplot as _plt
            _plt.savefig(str(ws / filename), bbox_inches="tight", dpi=100)
            _plt.close()

        mock_session.ns["save_fig"] = _save_fig

        tool_fn = _get_delegate_tool(mock_session)
        code = (
            "import matplotlib\n"
            "matplotlib.use('Agg')\n"
            "import matplotlib.pyplot as plt\n"
            "plt.figure()\n"
            "plt.plot([1,2,3])\n"
            "save_fig('delegate_chart.png')\n"
        )
        tool_fn.invoke({
            "task_description": "Generate chart",
            "code": code,
        })
        # Check that the image was detected
        assert any(
            a["name"] == "delegate_chart.png"
            for a in mock_session.new_artifacts
        )


# ── State machine integration ─────────────────────────────────────────────────

class TestStateMachineIntegration:

    def test_tool_use_recorded(self, mock_session):
        tool_fn = _get_delegate_tool(mock_session)
        tool_fn.invoke({
            "task_description": "Test",
            "code": "print(1)",
        })
        assert "delegate_analysis" in mock_session.state_machine.tools_used

    def test_delegation_counted_as_step(self, mock_session):
        initial_tools_count = len(mock_session.state_machine.tools_used)
        tool_fn = _get_delegate_tool(mock_session)
        tool_fn.invoke({
            "task_description": "Test",
            "code": "print(1)",
        })
        assert len(mock_session.state_machine.tools_used) == initial_tools_count + 1


# ── Stage validation from tool ─────────────────────────────────────────────────

class TestToolLevelStageValidation:

    def test_rejected_in_wrong_stage(self, mock_session):
        """Tool returns error when called in wrong stage (e.g. INIT)."""
        mock_session.state_machine.current_stage = AnalysisStage.INIT
        mock_session.state_machine.tools_used = []
        tool_fn = _get_delegate_tool(mock_session)
        result = json.loads(tool_fn.invoke({
            "task_description": "Test",
            "code": "print(1)",
        }))
        assert "error" in result
