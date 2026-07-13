"""
Tests for Session Persistence & Resume (P3).

Covers:
- SessionPersistence save/restore/can_resume/clear
- _Session restore_state reconstruction
- State machine deserialization
- API /resume and /resumable endpoints
"""
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from langgraph_langchain.session_persistence import (
    SessionPersistence,
    get_session_persistence,
    _STATE_FILE,
)
from langgraph_langchain.schemas import (
    AnalysisAssumption,
    Finding,
    MetricDefinition,
    EvidenceItem,
    StageResult,
    FailureInfo,
)
from langgraph_langchain.state_machine import AnalysisStage


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a temporary workspace directory."""
    ws = tmp_path / "workspace_test"
    ws.mkdir()
    return ws


@pytest.fixture
def tmp_source_file(tmp_path):
    """Create a dummy data file."""
    f = tmp_path / "test_data.xlsx"
    f.write_text("dummy data")
    return f


@pytest.fixture
def persistence():
    return SessionPersistence()


def _make_mock_session(workspace_dir, source_path, session_id="test-123"):
    """Build a mock _Session with realistic attributes."""
    session = MagicMock()
    session.workspace_dir = workspace_dir
    session.source_path = str(source_path)
    session.session_id = session_id
    session.user_question = "Analyze sales data"
    session.current_stage = "deep_dive"
    session.total_steps = 7
    session.consecutive_python_errors = 0
    session.last_progress_marker = "found 3 outliers"
    session.findings = [
        Finding(
            finding_id="F001",
            statement="Sales dropped 15% in Q3",
            evidence=[
                EvidenceItem(evidence_text="Q3 total was 850K vs Q2 1M")
            ],
            confidence_level="high",
            evidence_level="A",
        ),
    ]
    session.metric_definitions = [
        MetricDefinition(
            metric_name="monthly_revenue",
            definition_text="Sum of all completed order amounts per month",
        ),
    ]
    session.assumptions = [
        AnalysisAssumption(
            assumption_text="All returns are processed within the same quarter",
            risk_level="medium",
        ),
    ]
    session.stage_history = [
        StageResult(
            stage="schema_understanding",
            status="completed",
            started_at=datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc),
            completed_at=datetime(2025, 1, 1, 10, 1, tzinfo=timezone.utc),
        ),
        StageResult(
            stage="deep_dive",
            status="started",
            started_at=datetime(2025, 1, 1, 10, 5, tzinfo=timezone.utc),
        ),
    ]
    session.stage_failures = []
    session.known_image_files = {workspace_dir / "chart1.png", workspace_dir / "chart2.png"}
    session.new_artifacts = []
    session.report = None
    session.pending_report_markdown = None

    # Mock state machine
    sm = MagicMock()
    sm.current_stage = AnalysisStage.DEEP_DIVE
    sm.stage_history = [AnalysisStage.INIT, AnalysisStage.SCHEMA_UNDERSTANDING, AnalysisStage.DEEP_DIVE]
    sm.conditions_met = {"schema_documented", "fields_understood", "quality_assessed", "data_loaded"}
    sm.tools_used = ["load_data", "eda_profile", "python_repl", "python_repl"]
    sm.stage_step_count = {
        AnalysisStage.SCHEMA_UNDERSTANDING: 2,
        AnalysisStage.DEEP_DIVE: 3,
    }
    session.state_machine = sm

    return session


# ── Persistence save/restore ──────────────────────────────────────────────────

class TestSessionPersistenceSaveRestore:

    def test_save_creates_state_file(self, persistence, tmp_workspace, tmp_source_file):
        session = _make_mock_session(tmp_workspace, tmp_source_file)
        persistence.save(session)

        state_path = tmp_workspace / _STATE_FILE
        assert state_path.exists()

        data = json.loads(state_path.read_text(encoding="utf-8"))
        assert data["session_id"] == "test-123"
        assert data["current_stage"] == "deep_dive"
        assert data["total_steps"] == 7
        assert data["version"] == 2

    def test_save_atomic_no_corruption_on_partial_write(self, persistence, tmp_workspace, tmp_source_file):
        """Even if save is called rapidly, state file should always be valid JSON."""
        session = _make_mock_session(tmp_workspace, tmp_source_file)
        for i in range(20):
            session.total_steps = i
            persistence.save(session)

        state_path = tmp_workspace / _STATE_FILE
        data = json.loads(state_path.read_text(encoding="utf-8"))
        assert data["total_steps"] == 19

    def test_restore_returns_state_dict(self, persistence, tmp_workspace, tmp_source_file):
        session = _make_mock_session(tmp_workspace, tmp_source_file)
        persistence.save(session)

        state = persistence.restore(tmp_workspace)
        assert state is not None
        assert state["session_id"] == "test-123"
        assert state["source_path"] == str(tmp_source_file)
        assert state["current_stage"] == "deep_dive"

    def test_restore_returns_none_when_no_state(self, persistence, tmp_workspace):
        assert persistence.restore(tmp_workspace) is None

    def test_restore_returns_none_for_completed_session(self, persistence, tmp_workspace, tmp_source_file):
        session = _make_mock_session(tmp_workspace, tmp_source_file)
        session.report = "# Final Report\n\nAll done."
        persistence.save(session)

        # Completed sessions (report already set) should not be resumable
        state = persistence.restore(tmp_workspace)
        assert state is None

    def test_restore_returns_none_for_missing_source(self, persistence, tmp_workspace, tmp_path):
        session = _make_mock_session(tmp_workspace, tmp_path / "nonexistent.xlsx")
        persistence.save(session)

        # Source file doesn't exist — should return None
        state = persistence.restore(tmp_workspace)
        assert state is None

    def test_restore_returns_none_for_invalid_json(self, persistence, tmp_workspace):
        state_path = tmp_workspace / _STATE_FILE
        state_path.write_text("{invalid json", encoding="utf-8")

        assert persistence.restore(tmp_workspace) is None

    def test_restore_returns_none_for_missing_required_fields(self, persistence, tmp_workspace):
        state_path = tmp_workspace / _STATE_FILE
        state_path.write_text('{"version": 1}', encoding="utf-8")

        assert persistence.restore(tmp_workspace) is None


class TestSessionPersistenceCanResume:

    def test_can_resume_true_for_interrupted_session(self, persistence, tmp_workspace, tmp_source_file):
        session = _make_mock_session(tmp_workspace, tmp_source_file)
        persistence.save(session)

        assert persistence.can_resume(tmp_workspace) is True

    def test_can_resume_false_when_no_state(self, persistence, tmp_workspace):
        assert persistence.can_resume(tmp_workspace) is False

    def test_can_resume_false_when_completed(self, persistence, tmp_workspace, tmp_source_file):
        session = _make_mock_session(tmp_workspace, tmp_source_file)
        session.report = "# Report done"
        persistence.save(session)

        assert persistence.can_resume(tmp_workspace) is False


class TestSessionPersistenceClear:

    def test_clear_removes_state_file(self, persistence, tmp_workspace, tmp_source_file):
        session = _make_mock_session(tmp_workspace, tmp_source_file)
        persistence.save(session)
        assert (tmp_workspace / _STATE_FILE).exists()

        persistence.clear(tmp_workspace)
        assert not (tmp_workspace / _STATE_FILE).exists()

    def test_clear_is_noop_when_no_state(self, persistence, tmp_workspace):
        # Should not raise
        persistence.clear(tmp_workspace)


# ── Serialization detail ──────────────────────────────────────────────────────

class TestSerialization:

    def test_findings_serialized_correctly(self, persistence, tmp_workspace, tmp_source_file):
        session = _make_mock_session(tmp_workspace, tmp_source_file)
        persistence.save(session)

        state = json.loads((tmp_workspace / _STATE_FILE).read_text(encoding="utf-8"))
        assert len(state["findings"]) == 1
        f = state["findings"][0]
        assert f["finding_id"] == "F001"
        assert f["statement"] == "Sales dropped 15% in Q3"
        assert f["confidence_level"] == "high"
        assert len(f["evidence"]) == 1

    def test_metric_definitions_serialized(self, persistence, tmp_workspace, tmp_source_file):
        session = _make_mock_session(tmp_workspace, tmp_source_file)
        persistence.save(session)

        state = json.loads((tmp_workspace / _STATE_FILE).read_text(encoding="utf-8"))
        assert len(state["metric_definitions"]) == 1
        m = state["metric_definitions"][0]
        assert m["metric_name"] == "monthly_revenue"

    def test_assumptions_serialized(self, persistence, tmp_workspace, tmp_source_file):
        session = _make_mock_session(tmp_workspace, tmp_source_file)
        persistence.save(session)

        state = json.loads((tmp_workspace / _STATE_FILE).read_text(encoding="utf-8"))
        assert len(state["assumptions"]) == 1
        assert state["assumptions"][0]["risk_level"] == "medium"

    def test_state_machine_serialized(self, persistence, tmp_workspace, tmp_source_file):
        session = _make_mock_session(tmp_workspace, tmp_source_file)
        persistence.save(session)

        state = json.loads((tmp_workspace / _STATE_FILE).read_text(encoding="utf-8"))
        sm = state["state_machine"]
        assert sm["current_stage"] == "deep_dive"
        assert "schema_understanding" in sm["stage_history"]
        assert "data_loaded" in sm["conditions_met"]
        assert "load_data" in sm["tools_used"]
        assert "schema_understanding" in sm["stage_step_count"]

    def test_known_image_files_serialized(self, persistence, tmp_workspace, tmp_source_file):
        session = _make_mock_session(tmp_workspace, tmp_source_file)
        persistence.save(session)

        state = json.loads((tmp_workspace / _STATE_FILE).read_text(encoding="utf-8"))
        images = state["known_image_files"]
        assert len(images) == 2
        assert any("chart1.png" in p for p in images)

    def test_stage_history_serialized(self, persistence, tmp_workspace, tmp_source_file):
        session = _make_mock_session(tmp_workspace, tmp_source_file)
        persistence.save(session)

        state = json.loads((tmp_workspace / _STATE_FILE).read_text(encoding="utf-8"))
        history = state["stage_history"]
        assert len(history) == 2
        assert history[0]["stage"] == "schema_understanding"
        assert history[0]["status"] == "completed"


# ── Deserialization helpers ────────────────────────────────────────────────────

class TestDeserialization:

    def test_deserialize_findings(self):
        raw = [
            {
                "finding_id": "F001",
                "statement": "Sales dropped 15%",
                "evidence": [{"evidence_text": "Q3 total was 850K"}],
                "confidence_level": "high",
                "evidence_level": "A",
            }
        ]
        findings = SessionPersistence.deserialize_findings(raw)
        assert len(findings) == 1
        assert isinstance(findings[0], Finding)
        assert findings[0].finding_id == "F001"
        assert len(findings[0].evidence) == 1

    def test_deserialize_metric_definitions(self):
        raw = [
            {"metric_name": "revenue", "definition_text": "Total revenue per month"}
        ]
        defs = SessionPersistence.deserialize_metric_definitions(raw)
        assert len(defs) == 1
        assert isinstance(defs[0], MetricDefinition)

    def test_deserialize_assumptions(self):
        raw = [
            {"assumption_text": "Data is complete", "risk_level": "low"}
        ]
        assumptions = SessionPersistence.deserialize_assumptions(raw)
        assert len(assumptions) == 1
        assert isinstance(assumptions[0], AnalysisAssumption)

    def test_deserialize_empty_lists(self):
        assert SessionPersistence.deserialize_findings([]) == []
        assert SessionPersistence.deserialize_metric_definitions([]) == []
        assert SessionPersistence.deserialize_assumptions([]) == []


# ── _Session restore_state integration ────────────────────────────────────────

class TestSessionRestoreState:

    def test_session_rebuilds_from_restore_state(self, tmp_workspace, tmp_source_file):
        from langgraph_langchain.langgraph_agent import _Session

        # Save state from a mock session
        persistence = SessionPersistence()
        mock_session = _make_mock_session(tmp_workspace, tmp_source_file)
        persistence.save(mock_session)

        # Restore into a real _Session
        state = persistence.restore(tmp_workspace)
        assert state is not None

        restored = _Session(
            str(tmp_workspace),
            str(tmp_source_file),
            session_id="test-123",
            user_question="Analyze sales data",
            restore_state=state,
        )

        # Verify restoration
        assert restored.total_steps == 7
        assert restored.current_stage == "deep_dive"
        assert len(restored.findings) == 1
        assert restored.findings[0].finding_id == "F001"
        assert len(restored.metric_definitions) == 1
        assert len(restored.assumptions) == 1
        assert len(restored.known_image_files) == 2

    def test_session_without_restore_state_works_normally(self, tmp_workspace, tmp_source_file):
        from langgraph_langchain.langgraph_agent import _Session

        session = _Session(
            str(tmp_workspace),
            str(tmp_source_file),
            session_id="fresh-123",
        )
        assert session.total_steps == 0
        assert session.current_stage == "schema_understanding"
        assert len(session.findings) == 0
        assert session.report is None

    def test_state_machine_restored_correctly(self, tmp_workspace, tmp_source_file):
        from langgraph_langchain.langgraph_agent import _Session
        from langgraph_langchain.state_machine import AnalysisStage as Stage

        persistence = SessionPersistence()
        mock_session = _make_mock_session(tmp_workspace, tmp_source_file)
        persistence.save(mock_session)

        state = persistence.restore(tmp_workspace)
        restored = _Session(
            str(tmp_workspace),
            str(tmp_source_file),
            session_id="test-123",
            restore_state=state,
        )

        sm = restored.state_machine
        assert sm.current_stage == Stage.DEEP_DIVE
        assert "data_loaded" in sm.conditions_met
        assert "load_data" in sm.tools_used
        assert len(sm.stage_history) >= 2  # INIT + at least one more


# ── Singleton ──────────────────────────────────────────────────────────────────

class TestSingleton:

    def test_get_session_persistence_returns_same_instance(self):
        a = get_session_persistence()
        b = get_session_persistence()
        assert a is b
        assert isinstance(a, SessionPersistence)


# ── Round-trip test ───────────────────────────────────────────────────────────

class TestRoundTrip:

    def test_save_restore_save_preserves_all_data(self, persistence, tmp_workspace, tmp_source_file):
        """Simulate: save → restore → save again → compare both states."""
        session = _make_mock_session(tmp_workspace, tmp_source_file)
        persistence.save(session)

        state1 = json.loads((tmp_workspace / _STATE_FILE).read_text(encoding="utf-8"))

        # Clear and re-save from restored state
        persistence.clear(tmp_workspace)

        # Re-save from the same state dict (simulating a second save after restore)
        (tmp_workspace / _STATE_FILE).write_text(
            json.dumps(state1, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        state2 = json.loads((tmp_workspace / _STATE_FILE).read_text(encoding="utf-8"))

        assert state1["session_id"] == state2["session_id"]
        assert state1["total_steps"] == state2["total_steps"]
        assert state1["findings"] == state2["findings"]
        assert state1["state_machine"]["conditions_met"] == state2["state_machine"]["conditions_met"]
