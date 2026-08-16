"""Regression coverage for authoritative analysis-stage synchronization."""

from langgraph_langchain.langgraph_agent import _Session
from langgraph_langchain.state_machine import AnalysisStage
from langgraph_langchain.tools.tool_python_repl import _is_user_artifact_path


def test_session_stage_does_not_rewind_after_completed_event(tmp_path):
    source = tmp_path / "data.csv"
    source.write_text("value\n1\n", encoding="utf-8")
    session = _Session(str(tmp_path), str(source), session_id="stage-sync")

    assert session.current_stage == AnalysisStage.SCHEMA_UNDERSTANDING
    assert session.state_machine.current_stage == AnalysisStage.SCHEMA_UNDERSTANDING

    session.state_machine.add_condition("schema_documented")
    session.state_machine.add_condition("fields_understood")
    assert session.try_advance_stage() is None
    assert session.current_stage == AnalysisStage.DATA_QUALITY_CHECK

    session.complete_stage("schema_understanding")

    assert session.current_stage == AnalysisStage.DATA_QUALITY_CHECK
    assert session.state_machine.current_stage == AnalysisStage.DATA_QUALITY_CHECK


def test_analysis_can_advance_from_eda_to_report_generation(tmp_path):
    source = tmp_path / "data.csv"
    source.write_text("value\n1\n", encoding="utf-8")
    session = _Session(str(tmp_path), str(source), session_id="stage-report")
    state = session.state_machine

    state.add_condition("schema_documented")
    state.add_condition("fields_understood")
    session.try_advance_stage()
    state.add_condition("quality_assessed")
    state.add_condition("distributions_analyzed")
    state.add_condition("correlations_checked")
    session.try_advance_stage()
    session.start_stage("deep_dive")
    state.add_condition("findings_recorded")
    session.try_advance_stage()
    state.add_condition("findings_organized")
    state.add_condition("evidence_linked")
    state.add_condition("min_findings_count")
    session.start_stage("report_generation")

    assert session.current_stage == AnalysisStage.REPORT_GENERATION
    assert state.current_stage == AnalysisStage.REPORT_GENERATION


def test_runtime_hidden_files_are_not_user_artifacts(tmp_path):
    visible = tmp_path / "chart.png"
    runtime_temp = tmp_path / ".analysis_runs" / ".run_write.tmp"
    runtime_temp.parent.mkdir()
    visible.write_bytes(b"png")
    runtime_temp.write_text("{}", encoding="utf-8")

    assert _is_user_artifact_path(visible, tmp_path) is True
    assert _is_user_artifact_path(runtime_temp, tmp_path) is False
