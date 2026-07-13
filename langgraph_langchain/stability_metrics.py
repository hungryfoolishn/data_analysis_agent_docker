"""Stability metrics tracking for analysis sessions.

This module tracks various stability metrics to monitor agent performance and reliability.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from pathlib import Path
import json
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class StabilityMetrics:
    """Tracks stability metrics for analysis sessions."""

    def __init__(self, metrics_file: Optional[Path] = None):
        self.metrics_file = metrics_file or Path("./workspace/.stability_metrics.json")
        self.metrics: Dict[str, Any] = self._load_metrics()

    def _load_metrics(self) -> Dict[str, Any]:
        """Load metrics from file."""
        if self.metrics_file.exists():
            try:
                with open(self.metrics_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load metrics: {e}")
        return {
            "sessions": {},
            "aggregated": {
                "total_sessions": 0,
                "successful_sessions": 0,
                "failed_sessions": 0,
                "cancelled_sessions": 0,
                "total_steps": 0,
                "total_duration_seconds": 0,
                "failure_counts_by_code": {},
                "recovery_attempts": 0,
                "successful_recoveries": 0,
            },
            "last_updated": datetime.now().isoformat(),
        }

    def _save_metrics(self):
        """Save metrics to file."""
        try:
            self.metrics["last_updated"] = datetime.now().isoformat()
            self.metrics_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.metrics_file, "w", encoding="utf-8") as f:
                json.dump(self.metrics, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save metrics: {e}")

    def record_session_start(self, session_id: str, instruction: str):
        """Record the start of an analysis session."""
        self.metrics["sessions"][session_id] = {
            "session_id": session_id,
            "instruction": instruction[:200],  # Truncate for storage
            "start_time": datetime.now().isoformat(),
            "end_time": None,
            "status": "running",
            "steps": 0,
            "duration_seconds": 0,
            "failure_code": None,
            "failure_message": None,
            "recovery_attempts": 0,
            "stage_history": [],
        }
        self.metrics["aggregated"]["total_sessions"] += 1
        self._save_metrics()

    def record_session_end(
        self,
        session_id: str,
        status: str,
        steps: int = 0,
        failure_code: Optional[str] = None,
        failure_message: Optional[str] = None,
        stage_history: Optional[List[str]] = None,
    ):
        """Record the end of an analysis session."""
        if session_id not in self.metrics["sessions"]:
            logger.warning(f"Session {session_id} not found in metrics")
            return

        session = self.metrics["sessions"][session_id]
        session["end_time"] = datetime.now().isoformat()
        session["status"] = status
        session["steps"] = steps
        session["failure_code"] = failure_code
        session["failure_message"] = failure_message[:200] if failure_message else None
        session["stage_history"] = stage_history or []

        # Calculate duration
        start_time = datetime.fromisoformat(session["start_time"])
        end_time = datetime.fromisoformat(session["end_time"])
        duration = (end_time - start_time).total_seconds()
        session["duration_seconds"] = duration

        # Update aggregated metrics
        agg = self.metrics["aggregated"]
        agg["total_steps"] += steps
        agg["total_duration_seconds"] += duration

        if status == "success":
            agg["successful_sessions"] += 1
        elif status == "failed":
            agg["failed_sessions"] += 1
            if failure_code:
                agg["failure_counts_by_code"][failure_code] = (
                    agg["failure_counts_by_code"].get(failure_code, 0) + 1
                )
        elif status == "cancelled":
            agg["cancelled_sessions"] += 1

        self._save_metrics()

    def record_recovery_attempt(self, session_id: str, success: bool):
        """Record a recovery attempt."""
        if session_id in self.metrics["sessions"]:
            self.metrics["sessions"][session_id]["recovery_attempts"] += 1

        agg = self.metrics["aggregated"]
        agg["recovery_attempts"] += 1
        if success:
            agg["successful_recoveries"] += 1

        self._save_metrics()

    def get_session_metrics(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get metrics for a specific session."""
        return self.metrics["sessions"].get(session_id)

    def get_aggregated_metrics(self) -> Dict[str, Any]:
        """Get aggregated metrics across all sessions."""
        agg = self.metrics["aggregated"]

        # Calculate derived metrics
        total = agg["total_sessions"]
        if total > 0:
            success_rate = agg["successful_sessions"] / total
            failure_rate = agg["failed_sessions"] / total
            cancellation_rate = agg["cancelled_sessions"] / total
            avg_steps = agg["total_steps"] / total
            avg_duration = agg["total_duration_seconds"] / total
        else:
            success_rate = failure_rate = cancellation_rate = 0
            avg_steps = avg_duration = 0

        recovery_attempts = agg["recovery_attempts"]
        if recovery_attempts > 0:
            recovery_success_rate = agg["successful_recoveries"] / recovery_attempts
        else:
            recovery_success_rate = 0

        return {
            "total_sessions": total,
            "successful_sessions": agg["successful_sessions"],
            "failed_sessions": agg["failed_sessions"],
            "cancelled_sessions": agg["cancelled_sessions"],
            "success_rate": round(success_rate, 3),
            "failure_rate": round(failure_rate, 3),
            "cancellation_rate": round(cancellation_rate, 3),
            "avg_steps_per_session": round(avg_steps, 1),
            "avg_duration_seconds": round(avg_duration, 1),
            "failure_counts_by_code": agg["failure_counts_by_code"],
            "recovery_attempts": recovery_attempts,
            "successful_recoveries": agg["successful_recoveries"],
            "recovery_success_rate": round(recovery_success_rate, 3),
            "last_updated": self.metrics["last_updated"],
        }

    def get_recent_sessions(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get the most recent sessions."""
        sessions = list(self.metrics["sessions"].values())
        sessions.sort(key=lambda s: s["start_time"], reverse=True)
        return sessions[:limit]

    def get_failure_breakdown(self) -> Dict[str, Any]:
        """Get a breakdown of failures by code."""
        failure_counts = self.metrics["aggregated"]["failure_counts_by_code"]
        total_failures = sum(failure_counts.values())

        breakdown = []
        for code, count in sorted(failure_counts.items(), key=lambda x: x[1], reverse=True):
            percentage = (count / total_failures * 100) if total_failures > 0 else 0
            breakdown.append({
                "failure_code": code,
                "count": count,
                "percentage": round(percentage, 1),
            })

        return {
            "total_failures": total_failures,
            "breakdown": breakdown,
        }

    def get_stage_completion_stats(self) -> Dict[str, Any]:
        """Get statistics on which stages sessions reach before completion/failure."""
        stage_counts = defaultdict(int)
        stage_success_counts = defaultdict(int)

        for session in self.metrics["sessions"].values():
            stage_history = session.get("stage_history", [])
            status = session.get("status")

            for stage in stage_history:
                stage_counts[stage] += 1
                if status == "success":
                    stage_success_counts[stage] += 1

        stats = []
        for stage in ["schema_understanding", "data_quality_check", "basic_eda", "deep_dive", "conclusion_synthesis", "report_generation"]:
            count = stage_counts.get(stage, 0)
            success_count = stage_success_counts.get(stage, 0)
            success_rate = (success_count / count) if count > 0 else 0

            stats.append({
                "stage": stage,
                "sessions_reached": count,
                "sessions_succeeded": success_count,
                "success_rate": round(success_rate, 3),
            })

        return {"stage_stats": stats}

    def reset_metrics(self):
        """Reset all metrics (use with caution)."""
        self.metrics = {
            "sessions": {},
            "aggregated": {
                "total_sessions": 0,
                "successful_sessions": 0,
                "failed_sessions": 0,
                "cancelled_sessions": 0,
                "total_steps": 0,
                "total_duration_seconds": 0,
                "failure_counts_by_code": {},
                "recovery_attempts": 0,
                "successful_recoveries": 0,
            },
            "last_updated": datetime.now().isoformat(),
        }
        self._save_metrics()
        logger.info("Metrics reset")


# Global metrics tracker instance
_metrics_tracker = StabilityMetrics()


def get_metrics_tracker() -> StabilityMetrics:
    """Get the global metrics tracker instance."""
    return _metrics_tracker
