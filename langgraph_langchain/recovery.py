"""Recovery strategies for analysis failures.

This module implements automatic recovery strategies for different types of analysis failures.
"""

from typing import Dict, Any, Optional, Callable, Awaitable
import logging
from .schemas import FailureCode

logger = logging.getLogger(__name__)


class RecoveryStrategy:
    """Base class for recovery strategies."""

    def __init__(self, failure_code: str, failure_detail: Dict[str, Any]):
        self.failure_code = failure_code
        self.failure_detail = failure_detail
        self.recovery_action = failure_detail.get("recovery_action", "user_action_required")
        self.retryable = failure_detail.get("retryable", False)
        self.hint = failure_detail.get("hint", "")

    async def execute(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Execute the recovery strategy.

        Args:
            context: Recovery context containing session info, original request, etc.

        Returns:
            Recovery result with modified request or None if recovery not possible
        """
        if not self.retryable:
            logger.info(f"Failure {self.failure_code} is not retryable")
            return None

        if self.recovery_action == "retry_same_scope":
            return await self._retry_same_scope(context)
        elif self.recovery_action == "retry_narrower_scope":
            return await self._retry_narrower_scope(context)
        elif self.recovery_action == "retry_with_sampled_data":
            return await self._retry_with_sampled_data(context)
        elif self.recovery_action == "user_action_required":
            return await self._user_action_required(context)
        else:
            logger.warning(f"Unknown recovery action: {self.recovery_action}")
            return None

    async def _retry_same_scope(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Retry with the same scope (e.g., after cancellation)."""
        logger.info(f"Recovery strategy: retry_same_scope for {self.failure_code}")
        return {
            "strategy": "retry_same_scope",
            "modified_instruction": context.get("original_instruction"),
            "retry_count": context.get("retry_count", 0) + 1,
            "max_retries": 1,  # Only retry once for same scope
        }

    async def _retry_narrower_scope(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Retry with narrower scope (e.g., after max steps exceeded)."""
        logger.info(f"Recovery strategy: retry_narrower_scope for {self.failure_code}")

        original_instruction = context.get("original_instruction", "")
        retry_count = context.get("retry_count", 0)

        # Add scope-narrowing hints based on failure type
        if self.failure_code == "max_steps_exceeded":
            hint_text = (
                "\n\nIMPORTANT: Previous attempt exceeded step limit. "
                "Focus on the most critical 2-3 findings only. "
                "Skip exploratory analysis and go straight to answering the core question."
            )
        elif self.failure_code == "python_execution_error":
            hint_text = (
                "\n\nIMPORTANT: Previous attempt had repeated Python errors. "
                "Use simpler, more defensive code. Validate data types before operations. "
                "Add explicit error handling."
            )
        elif self.failure_code == "reasoning_drift":
            hint_text = (
                "\n\nIMPORTANT: Previous attempt went off track. "
                "Stay focused on the original question. "
                "Avoid tangential analysis."
            )
        elif self.failure_code == "report_generation_failed" or self.failure_code == "report_rejected":
            hint_text = (
                "\n\nIMPORTANT: Previous report was rejected. "
                "Ensure you record at least 2 findings with record_finding. "
                "Include a 'Data Context' section with metric definitions."
            )
        elif self.failure_code == "timeout":
            hint_text = (
                "\n\nIMPORTANT: Previous attempt timed out. "
                "Use smaller data samples for expensive operations. "
                "Avoid computing correlations on high-cardinality columns. "
                "Limit chart generation to top 5 columns."
            )
        elif self.failure_code == "memory_limit_exceeded":
            hint_text = (
                "\n\nIMPORTANT: Previous attempt ran out of memory. "
                "Load only necessary columns. "
                "Use the platform DataSource sampling protocol instead of ad-hoc df.sample(), "
                "and preserve its seed, source row count, and sampled row count disclosure. "
                "Avoid creating multiple copies of the DataFrame."
            )
        elif self.failure_code == "disk_space_exhausted":
            hint_text = (
                "\n\nIMPORTANT: Workspace ran out of disk space. "
                "Reduce the number of charts generated. "
                "Save only the most important visualizations. "
                "Use lower DPI (dpi=72) for matplotlib figures."
            )
        elif self.failure_code == "corrupted_data_file":
            hint_text = (
                "\n\nIMPORTANT: Data file appears corrupted. "
                "Try loading with different encoding or error handling: "
                "pd.read_csv(..., encoding_errors='replace', on_bad_lines='skip')."
            )
        elif self.failure_code == "network_timeout":
            hint_text = (
                "\n\nIMPORTANT: Network timeout occurred. "
                "This is likely a transient issue. Retry the same analysis."
            )
        else:
            hint_text = "\n\nIMPORTANT: Previous attempt failed. Try a simpler approach."

        modified_instruction = original_instruction + hint_text

        return {
            "strategy": "retry_narrower_scope",
            "modified_instruction": modified_instruction,
            "retry_count": retry_count + 1,
            "max_retries": 2,  # Allow up to 2 retries with narrower scope
        }

    async def _user_action_required(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """User action required (e.g., missing data file, unclear schema)."""
        logger.info(f"Recovery strategy: user_action_required for {self.failure_code}")
        return {
            "strategy": "user_action_required",
            "hint": self.hint,
            "retry_count": context.get("retry_count", 0),
            "max_retries": 0,  # No automatic retry
        }

    async def _retry_with_sampled_data(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Retry with a sampled subset of data (e.g., after memory/disk issues)."""
        logger.info(f"Recovery strategy: retry_with_sampled_data for {self.failure_code}")

        original_instruction = context.get("original_instruction", "")
        retry_count = context.get("retry_count", 0)

        hint_text = (
            "\n\nIMPORTANT: Previous attempt hit resource limits. "
            "Retry through the platform DataSource sampling protocol; do not call df.sample() directly. "
            "The retry must retain a deterministic seed and disclose original and sampled row counts "
            "in Data Context before drawing conclusions."
        )
        modified_instruction = original_instruction + hint_text

        return {
            "strategy": "retry_with_sampled_data",
            "modified_instruction": modified_instruction,
            "retry_count": retry_count + 1,
            "max_retries": 1,
        }


class RecoveryExecutor:
    """Executes recovery strategies for analysis failures."""

    def __init__(self):
        self.recovery_history: Dict[str, list] = {}  # session_id -> list of recovery attempts

    async def attempt_recovery(
        self,
        session_id: str,
        failure_detail: Dict[str, Any],
        original_instruction: str,
        retry_count: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """Attempt to recover from a failure.

        Args:
            session_id: Session ID
            failure_detail: Failure detail from _failure_detail()
            original_instruction: Original user instruction
            retry_count: Number of retries so far

        Returns:
            Recovery result or None if recovery not possible
        """
        failure_code = failure_detail.get("code", "unknown")

        # Track recovery history
        if session_id not in self.recovery_history:
            self.recovery_history[session_id] = []

        # Check if we've already tried to recover from this failure type too many times
        same_failure_attempts = sum(
            1 for attempt in self.recovery_history[session_id]
            if attempt.get("failure_code") == failure_code
        )

        if same_failure_attempts >= 3:
            logger.warning(
                f"Too many recovery attempts for {failure_code} in session {session_id}"
            )
            return None

        # Create recovery strategy
        strategy = RecoveryStrategy(failure_code, failure_detail)

        # Execute recovery
        context = {
            "session_id": session_id,
            "original_instruction": original_instruction,
            "retry_count": retry_count,
            "failure_detail": failure_detail,
        }

        result = await strategy.execute(context)

        # Record recovery attempt
        if result:
            self.recovery_history[session_id].append({
                "failure_code": failure_code,
                "recovery_action": strategy.recovery_action,
                "result": result,
            })
            logger.info(
                f"Recovery attempted for {failure_code} in session {session_id}: "
                f"{result.get('strategy')}"
            )

        return result

    def clear_history(self, session_id: str):
        """Clear recovery history for a session."""
        self.recovery_history.pop(session_id, None)

    def get_history(self, session_id: str) -> list:
        """Get recovery history for a session."""
        return self.recovery_history.get(session_id, [])


# Global recovery executor instance
_recovery_executor = RecoveryExecutor()


def get_recovery_executor() -> RecoveryExecutor:
    """Get the global recovery executor instance."""
    return _recovery_executor
