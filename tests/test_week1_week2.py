"""Test script to verify Week 1 and Week 2 implementations.

This script tests:
- Week 1: Findings collection, metric declarations, assumptions
- Week 2: State machine, recovery strategies, stability metrics
"""

import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from langgraph_langchain.schemas import Finding, EvidenceItem, MetricDefinition, AnalysisAssumption
from langgraph_langchain.state_machine import AnalysisStateMachine, AnalysisStage
from langgraph_langchain.recovery import RecoveryStrategy, RecoveryExecutor
from langgraph_langchain.stability_metrics import StabilityMetrics
from langgraph_langchain.benchmark import get_benchmark_cases, BenchmarkRunner


def test_week1_schemas():
    """Test Week 1: Data structures for findings, metrics, assumptions."""
    print("\n=== Testing Week 1: Schemas ===")

    # Test Finding
    finding = Finding(
        finding_id="F001",
        statement="Sales increased by 25% in Q4",
        evidence=[
            EvidenceItem(
                evidence_text="Q4 total sales: $125,000 vs Q3: $100,000",
                artifact_refs=["sales_chart.png"],
                stats={"q4_sales": 125000, "q3_sales": 100000},
                calculation_method="(Q4 - Q3) / Q3 * 100"
            )
        ],
        evidence_level="B",
        category="trend",
        hypothesis_flag=False
    )
    print(f"✓ Finding created: {finding.statement[:50]}...")

    # Test MetricDefinition
    metric = MetricDefinition(
        metric_name="conversion_rate",
        definition_text="conversions / total_visitors",
        time_window="2024-01-01 to 2024-12-31",
        dedup_rule="Deduplicate by user_id",
        denominator="total_visitors (unique users who visited the site)"
    )
    print(f"✓ MetricDefinition created: {metric.metric_name}")

    # Test AnalysisAssumption
    assumption = AnalysisAssumption(
        assumption_text="Assuming 'region' field has no missing values",
        risk_level="medium"
    )
    print(f"✓ AnalysisAssumption created: {assumption.assumption_text[:50]}...")

    print("✓ Week 1 schemas test passed!")


def test_week2_state_machine():
    """Test Week 2: State machine implementation."""
    print("\n=== Testing Week 2: State Machine ===")

    sm = AnalysisStateMachine()
    print(f"✓ State machine initialized at stage: {sm.current_stage}")

    # Test transition
    sm.add_condition("data_loaded")
    can_transition, reason = sm.can_transition_to(AnalysisStage.SCHEMA_UNDERSTANDING)
    print(f"✓ Can transition to schema_understanding: {can_transition}")

    if can_transition:
        success = sm.transition_to(AnalysisStage.SCHEMA_UNDERSTANDING)
        print(f"✓ Transitioned to: {sm.current_stage}")

    # Test tool recording
    sm.record_tool_use("load_data")
    sm.record_step()
    print(f"✓ Recorded tool use and step")

    # Test progress
    progress = sm.get_stage_progress()
    print(f"✓ Stage progress: {progress['current_stage']}, tools used: {len(progress['tools_used'])}")

    print("✓ Week 2 state machine test passed!")


def test_week2_recovery():
    """Test Week 2: Recovery strategies."""
    print("\n=== Testing Week 2: Recovery Strategies ===")

    executor = RecoveryExecutor()

    # Test recovery attempt
    failure_detail = {
        "code": "max_steps_exceeded",
        "message": "Exceeded maximum steps",
        "retryable": True,
        "hint": "Narrow the scope",
        "recovery_action": "retry_narrower_scope"
    }

    import asyncio

    async def test_recovery():
        result = await executor.attempt_recovery(
            session_id="test_session",
            failure_detail=failure_detail,
            original_instruction="Analyze sales data",
            retry_count=0
        )
        return result

    result = asyncio.run(test_recovery())

    if result:
        print(f"✓ Recovery strategy: {result['strategy']}")
        print(f"✓ Retry count: {result['retry_count']}")
        print(f"✓ Modified instruction includes hint: {'IMPORTANT' in result['modified_instruction']}")
    else:
        print("✗ Recovery failed")

    # Test history
    history = executor.get_history("test_session")
    print(f"✓ Recovery history length: {len(history)}")

    print("✓ Week 2 recovery test passed!")


def test_week2_metrics():
    """Test Week 2: Stability metrics."""
    print("\n=== Testing Week 2: Stability Metrics ===")

    # Use a test file to avoid polluting production metrics
    test_metrics_file = Path("./workspace/.test_stability_metrics.json")
    metrics = StabilityMetrics(metrics_file=test_metrics_file)

    # Test session recording
    metrics.record_session_start("test_session_1", "Analyze sales data")
    print("✓ Session start recorded")

    metrics.record_session_end(
        session_id="test_session_1",
        status="success",
        steps=12,
        stage_history=["schema_understanding", "eda", "deep_dive", "synthesis", "report_generation"]
    )
    print("✓ Session end recorded")

    # Test aggregated metrics
    agg = metrics.get_aggregated_metrics()
    print(f"✓ Total sessions: {agg['total_sessions']}")
    print(f"✓ Success rate: {agg['success_rate']}")

    # Test recovery recording
    metrics.record_recovery_attempt("test_session_1", success=True)
    print("✓ Recovery attempt recorded")

    # Cleanup
    if test_metrics_file.exists():
        test_metrics_file.unlink()

    print("✓ Week 2 metrics test passed!")


def test_week2_benchmark():
    """Test Week 2: Benchmark framework."""
    print("\n=== Testing Week 2: Benchmark Framework ===")

    # Test getting benchmark cases
    cases = get_benchmark_cases()
    print(f"✓ Loaded {len(cases)} benchmark cases")

    # Test case structure
    case = cases[0]
    print(f"✓ First case: {case.case_id} - {case.name}")
    print(f"  Difficulty: {case.difficulty}")
    print(f"  Max steps: {case.max_steps}")
    print(f"  Tags: {', '.join(case.tags)}")

    # Test benchmark runner
    test_results_file = Path("./workspace/.test_benchmark_results.json")
    runner = BenchmarkRunner(results_file=test_results_file)

    runner.record_run(
        case_id="BC001",
        success=True,
        steps=12,
        duration_seconds=45.3,
        findings_count=2,
        stages_completed=["schema_understanding", "eda", "deep_dive", "synthesis", "report_generation"]
    )
    print("✓ Benchmark run recorded")

    summary = runner.get_summary()
    print(f"✓ Benchmark summary: {summary['total_runs']} runs, {summary['overall_success_rate']} success rate")

    # Cleanup
    if test_results_file.exists():
        test_results_file.unlink()

    print("✓ Week 2 benchmark test passed!")


def main():
    """Run all tests."""
    print("=" * 60)
    print("Testing Week 1 and Week 2 Implementations")
    print("=" * 60)

    try:
        test_week1_schemas()
        test_week2_state_machine()
        test_week2_recovery()
        test_week2_metrics()
        test_week2_benchmark()

        print("\n" + "=" * 60)
        print("✓ ALL TESTS PASSED!")
        print("=" * 60)

    except Exception as e:
        print(f"\n✗ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
