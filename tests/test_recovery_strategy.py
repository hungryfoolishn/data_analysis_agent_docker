"""
Test recovery strategy implementation with step tracking.
"""
import json
import tempfile
from pathlib import Path
import sys
import asyncio
import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from langgraph_langchain.schemas import FailureCode
from langgraph_langchain.recovery import RecoveryExecutor, RecoveryStrategy as RecoveryStrategyClass


def test_step_tracking():
    """Test that session metadata is saved and can be read."""
    print("\n=== Test 1: Step Tracking ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)

        # Simulate saving session metadata
        metadata = {
            "total_steps": 15,
            "session_id": "test-session-123",
            "completed_at": 1234567890.0
        }
        metadata_file = workspace / "session_metadata.json"
        metadata_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        # Verify metadata can be read
        loaded = json.loads(metadata_file.read_text(encoding="utf-8"))
        assert loaded["total_steps"] == 15
        assert loaded["session_id"] == "test-session-123"
        print("✓ Session metadata saved and loaded correctly")
        print(f"  Steps: {loaded['total_steps']}")
        print(f"  Session ID: {loaded['session_id']}")


def test_recovery_strategies():
    """Test recovery strategy selection for different failure codes."""
    print("\n=== Test 2: Recovery Strategy Selection ===")

    # Read failure policy from api_server_langgraph.py to understand recovery actions
    from langgraph_langchain.api_server_langgraph import _failure_policy

    # Test different failure codes (using actual FailureCode values)
    test_cases = [
        ("missing_data_file", "user_action_required"),
        ("python_execution_error", "retry_narrower_scope"),
        ("max_steps_exceeded", "retry_narrower_scope"),
        ("timeout", "retry_narrower_scope"),
        ("cancelled", "retry_same_scope"),
    ]

    for failure_code, expected_action in test_cases:
        policy = _failure_policy(failure_code)  # Call the function
        actual_action = policy.get("recovery_action", "user_action_required")
        assert actual_action == expected_action, f"Expected {expected_action}, got {actual_action}"
        print(f"✓ {failure_code} -> {actual_action}")


@pytest.mark.asyncio
async def test_recovery_execution():
    """Test recovery execution logic."""
    print("\n=== Test 3: Recovery Execution ===")

    executor = RecoveryExecutor()

    # Create a failure detail
    failure_detail = {
        "code": "python_execution_error",
        "message": "Division by zero",
        "recovery_action": "retry_same_scope",
        "retryable": True,
        "hint": "Check for zero values"
    }

    # Test 1: First recovery attempt
    result = await executor.attempt_recovery(
        session_id="test-session-1",
        failure_detail=failure_detail,
        original_instruction="Analyze sales data",
        retry_count=0
    )
    assert result is not None
    assert result["strategy"] == "retry_same_scope"
    assert result["retry_count"] == 1
    print(f"✓ First recovery: strategy={result['strategy']}, count={result['retry_count']}")

    # Test 2: Recovery with narrower scope
    failure_detail_timeout = {
        "code": "max_steps_exceeded",
        "message": "Analysis took too many steps",
        "recovery_action": "retry_narrower_scope",
        "retryable": True,
        "hint": "Simplify analysis"
    }

    result = await executor.attempt_recovery(
        session_id="test-session-2",
        failure_detail=failure_detail_timeout,
        original_instruction="Analyze all metrics",
        retry_count=0
    )
    assert result is not None
    assert result["strategy"] == "retry_narrower_scope"
    assert "IMPORTANT" in result["modified_instruction"]
    print(f"✓ Narrower scope recovery: strategy={result['strategy']}")
    print(f"  Modified instruction includes hint: {('IMPORTANT' in result['modified_instruction'])}")

    # Test 3: User action required
    failure_detail_user = {
        "code": "missing_data_file",
        "message": "Data file not found",
        "recovery_action": "user_action_required",
        "retryable": False,
        "hint": "Please provide valid data file"
    }

    result = await executor.attempt_recovery(
        session_id="test-session-3",
        failure_detail=failure_detail_user,
        original_instruction="Analyze data",
        retry_count=0
    )
    # Non-retryable failures return None
    assert result is None
    print(f"✓ User action required: result=None (not retryable)")


@pytest.mark.asyncio
async def test_recovery_history():
    """Test recovery history tracking."""
    print("\n=== Test 4: Recovery History ===")

    executor = RecoveryExecutor()

    failure_detail = {
        "code": "timeout",
        "message": "Timeout",
        "recovery_action": "retry_narrower_scope",
        "retryable": True,
        "hint": "Simplify"
    }

    # Execute multiple recoveries
    await executor.attempt_recovery(
        session_id="test-session-4",
        failure_detail=failure_detail,
        original_instruction="Test",
        retry_count=0
    )

    await executor.attempt_recovery(
        session_id="test-session-4",
        failure_detail=failure_detail,
        original_instruction="Test",
        retry_count=1
    )

    # Check history
    history = executor.recovery_history.get("test-session-4", [])
    assert len(history) == 2
    print(f"✓ Recovery history tracked: {len(history)} attempts")
    for i, attempt in enumerate(history, 1):
        print(f"  Attempt {i}: {attempt.get('failure_code')}")


if __name__ == "__main__":
    try:
        test_step_tracking()
        test_recovery_strategies()
        asyncio.run(test_recovery_execution())
        asyncio.run(test_recovery_history())

        print("\n" + "="*70)
        print("✅ All recovery strategy tests passed!")
        print("="*70)

    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
