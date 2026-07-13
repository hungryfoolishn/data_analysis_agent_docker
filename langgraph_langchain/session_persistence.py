"""
Session Persistence & Resume

Serializes _Session state to JSON after each significant tool call,
enabling analysis resume after backend restart or interruption.

Design adapted from hermes-agent's session persistence patterns, simplified
for our workspace-based architecture.

State file: ``workspace/<session_id>/.session_state.json``
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from langgraph_langchain.schemas import (
    AnalysisAssumption,
    Finding,
    MetricDefinition,
)

logger = logging.getLogger(__name__)

_STATE_FILE = ".session_state.json"


class SessionPersistence:
    """Serialize and restore _Session state for resume capability.

    Usage::

        persistence = SessionPersistence()
        persistence.save(session)

        # On resume:
        state = persistence.restore(workspace_dir)
        if state:
            session = _Session(..., restore_state=state)
    """

    # ── Save ───────────────────────────────────────────────────────────────

    def save(self, session) -> None:
        """Save session state to ``workspace/.session_state.json`` (atomic write)."""
        state = self._serialize(session)
        dest = session.workspace_dir / _STATE_FILE
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(session.workspace_dir),
                suffix=".tmp",
                prefix=".state_",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(state, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, str(dest))
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
            logger.debug(
                "Session state saved: %s (step %d)",
                session.session_id,
                session.total_steps,
            )
        except Exception as exc:
            logger.warning("Failed to save session state: %s", exc)

    # ── Restore ────────────────────────────────────────────────────────────

    def restore(self, workspace_dir: Path) -> Optional[Dict[str, Any]]:
        """Load saved session state from workspace.

        Returns ``None`` if no state exists or state is invalid / already
        completed.
        """
        path = workspace_dir / _STATE_FILE
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            state = json.loads(raw)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read session state: %s", exc)
            return None

        # Validate minimum fields
        required = ["session_id", "source_path", "current_stage"]
        if not all(k in state for k in required):
            logger.warning("Session state incomplete, skipping restore")
            return None

        # Don't restore completed sessions (report already generated)
        if state.get("report"):
            logger.info("Session already has report, no resume needed")
            return None

        # Source file must still exist
        source_path = state.get("source_path", "")
        if not source_path or not Path(source_path).exists():
            logger.warning(
                "Session source file missing: %s, cannot resume", source_path
            )
            return None

        logger.info(
            "Found resumable session state: %s (stage=%s, steps=%d)",
            state.get("session_id", "?"),
            state.get("current_stage", "?"),
            state.get("total_steps", 0),
        )
        return state

    def can_resume(self, workspace_dir: Path) -> bool:
        """Check if a workspace has resumable state."""
        return self.restore(workspace_dir) is not None

    def clear(self, workspace_dir: Path) -> None:
        """Remove saved session state (e.g., after successful resume or deletion)."""
        path = workspace_dir / _STATE_FILE
        try:
            if path.exists():
                path.unlink()
                logger.debug("Session state cleared: %s", path)
        except OSError as exc:
            logger.warning("Failed to clear session state: %s", exc)

    # ── Serialization ──────────────────────────────────────────────────────

    def _serialize(self, session) -> Dict[str, Any]:
        """Convert session attributes to a JSON-safe dict."""
        return {
            "version": 2,
            "session_id": session.session_id,
            "user_question": session.user_question,
            "source_path": session.source_path,
            "current_stage": session.current_stage,
            "total_steps": session.total_steps,
            "consecutive_python_errors": session.consecutive_python_errors,
            "last_progress_marker": session.last_progress_marker,
            # Structured analysis data
            "findings": [f.model_dump() for f in session.findings],
            "metric_definitions": [
                m.model_dump() for m in session.metric_definitions
            ],
            "assumptions": [a.model_dump() for a in session.assumptions],
            # Stage history & failures
            "stage_history": [
                {
                    "stage": sr.stage,
                    "status": sr.status,
                    "started_at": sr.started_at.isoformat() if sr.started_at else None,
                    "completed_at": sr.completed_at.isoformat() if sr.completed_at else None,
                }
                for sr in session.stage_history
            ],
            "stage_failures": [f.model_dump() for f in session.stage_failures],
            # State machine
            "state_machine": self._serialize_state_machine(session.state_machine),
            # Known artifacts
            "known_image_files": [str(p) for p in session.known_image_files],
            "new_artifacts": list(session.new_artifacts),
            # Report state
            "report": session.report,
            "pending_report_markdown": session.pending_report_markdown,
            # Timestamp
            "saved_at": datetime.now().isoformat(),
        }

    @staticmethod
    def _serialize_state_machine(sm) -> Dict[str, Any]:
        """Serialize AnalysisStateMachine to a JSON-safe dict."""
        return {
            "current_stage": sm.current_stage.value,
            "stage_history": [s.value for s in sm.stage_history],
            "conditions_met": sorted(sm.conditions_met),
            "tools_used": list(sm.tools_used),
            "stage_step_count": {
                s.value: c for s, c in sm.stage_step_count.items()
            },
        }

    # ── Deserialization helpers ────────────────────────────────────────────

    @staticmethod
    def deserialize_findings(raw: List[Dict]) -> List[Finding]:
        """Reconstruct Finding objects from JSON dicts."""
        return [Finding(**f) for f in raw]

    @staticmethod
    def deserialize_metric_definitions(raw: List[Dict]) -> List[MetricDefinition]:
        """Reconstruct MetricDefinition objects from JSON dicts."""
        return [MetricDefinition(**m) for m in raw]

    @staticmethod
    def deserialize_assumptions(raw: List[Dict]) -> List[AnalysisAssumption]:
        """Reconstruct AnalysisAssumption objects from JSON dicts."""
        return [AnalysisAssumption(**a) for a in raw]


# Module-level singleton
_persistence = SessionPersistence()


def get_session_persistence() -> SessionPersistence:
    """Return the module-level SessionPersistence singleton."""
    return _persistence
