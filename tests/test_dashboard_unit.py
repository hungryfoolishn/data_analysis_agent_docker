#!/usr/bin/env python3
"""
测试仪表板功能（单元测试，不需要运行服务器）
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from langgraph_langchain.stability_metrics import StabilityMetrics
from datetime import datetime
import tempfile
import json


def test_stability_metrics_collection():
    """测试稳定性指标收集"""
    print("测试 1: 稳定性指标收集")

    # 创建临时文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_file = Path(f.name)

    try:
        metrics = StabilityMetrics(metrics_file=temp_file)

        # 记录会话开始
        metrics.record_session_start("test_session_1", "分析销售数据")
        assert "test_session_1" in metrics.metrics["sessions"]
        print("✅ 会话开始记录成功")

        # 记录会话结束（成功）
        metrics.record_session_end(
            "test_session_1",
            status="success",
            steps=10,
            stage_history=["schema_understanding", "basic_eda", "deep_dive", "report_generation"]
        )
        session = metrics.metrics["sessions"]["test_session_1"]
        assert session["status"] == "success"
        assert session["steps"] == 10
        print("✅ 会话结束记录成功")

        # 记录失败会话
        metrics.record_session_start("test_session_2", "分析用户行为")
        metrics.record_session_end(
            "test_session_2",
            status="failed",
            steps=5,
            failure_code="python_execution_error",
            failure_message="Division by zero"
        )
        print("✅ 失败会话记录成功")

        # 记录恢复尝试
        metrics.record_recovery_attempt("test_session_2", success=True)
        assert metrics.metrics["aggregated"]["recovery_attempts"] == 1
        assert metrics.metrics["aggregated"]["successful_recoveries"] == 1
        print("✅ 恢复尝试记录成功")

    finally:
        temp_file.unlink()


def test_aggregated_metrics():
    """测试聚合指标计算"""
    print("\n测试 2: 聚合指标计算")

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_file = Path(f.name)

    try:
        metrics = StabilityMetrics(metrics_file=temp_file)

        # 添加多个会话
        for i in range(10):
            session_id = f"session_{i}"
            metrics.record_session_start(session_id, f"分析任务 {i}")

            if i < 7:  # 70% 成功
                metrics.record_session_end(session_id, status="success", steps=10 + i)
            elif i < 9:  # 20% 失败
                metrics.record_session_end(
                    session_id,
                    status="failed",
                    steps=5,
                    failure_code="max_steps_exceeded"
                )
            else:  # 10% 取消
                metrics.record_session_end(session_id, status="cancelled", steps=3)

        # 获取聚合指标
        agg = metrics.get_aggregated_metrics()

        assert agg["total_sessions"] == 10
        assert agg["successful_sessions"] == 7
        assert agg["failed_sessions"] == 2
        assert agg["cancelled_sessions"] == 1
        assert agg["success_rate"] == 0.7
        assert agg["failure_rate"] == 0.2
        print("✅ 聚合指标计算正确")

        # 检查平均值
        assert agg["avg_steps_per_session"] > 0
        assert agg["avg_duration_seconds"] >= 0
        print("✅ 平均值计算正确")

    finally:
        temp_file.unlink()


def test_failure_breakdown():
    """测试失败分析"""
    print("\n测试 3: 失败分析")

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_file = Path(f.name)

    try:
        metrics = StabilityMetrics(metrics_file=temp_file)

        # 添加不同类型的失败
        failure_types = [
            "python_execution_error",
            "python_execution_error",
            "max_steps_exceeded",
            "report_rejected",
            "python_execution_error",
        ]

        for i, failure_code in enumerate(failure_types):
            session_id = f"failed_session_{i}"
            metrics.record_session_start(session_id, f"任务 {i}")
            metrics.record_session_end(
                session_id,
                status="failed",
                steps=5,
                failure_code=failure_code
            )

        # 获取失败分析
        breakdown = metrics.get_failure_breakdown()

        assert breakdown["total_failures"] == 5
        assert len(breakdown["breakdown"]) == 3  # 3 种不同的失败类型

        # 检查最常见的失败类型
        top_failure = breakdown["breakdown"][0]
        assert top_failure["failure_code"] == "python_execution_error"
        assert top_failure["count"] == 3
        assert top_failure["percentage"] == 60.0

        print("✅ 失败分析正确")

    finally:
        temp_file.unlink()


def test_stage_completion_stats():
    """测试阶段完成统计"""
    print("\n测试 4: 阶段完成统计")

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_file = Path(f.name)

    try:
        metrics = StabilityMetrics(metrics_file=temp_file)

        # 添加会话，模拟不同阶段的完成情况
        sessions = [
            ("s1", "success", ["schema_understanding", "basic_eda", "deep_dive", "report_generation"]),
            ("s2", "success", ["schema_understanding", "basic_eda", "deep_dive", "report_generation"]),
            ("s3", "failed", ["schema_understanding", "basic_eda"]),
            ("s4", "success", ["schema_understanding", "basic_eda", "deep_dive", "report_generation"]),
        ]

        for session_id, status, stages in sessions:
            metrics.record_session_start(session_id, "测试任务")
            metrics.record_session_end(
                session_id,
                status=status,
                steps=len(stages),
                stage_history=stages
            )

        # 获取阶段统计
        stage_stats = metrics.get_stage_completion_stats()

        stats_list = stage_stats["stage_stats"]
        assert len(stats_list) > 0

        # 检查 schema_understanding 阶段（所有会话都到达）
        schema_stat = next(s for s in stats_list if s["stage"] == "schema_understanding")
        assert schema_stat["sessions_reached"] == 4
        assert schema_stat["sessions_succeeded"] == 3  # 3 个成功会话
        assert schema_stat["success_rate"] == 0.75

        print("✅ 阶段完成统计正确")

    finally:
        temp_file.unlink()


def test_recent_sessions():
    """测试最近会话查询"""
    print("\n测试 5: 最近会话查询")

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_file = Path(f.name)

    try:
        metrics = StabilityMetrics(metrics_file=temp_file)

        # 添加多个会话
        for i in range(15):
            session_id = f"session_{i}"
            metrics.record_session_start(session_id, f"任务 {i}")
            metrics.record_session_end(session_id, status="success", steps=10)

        # 获取最近 10 个会话
        recent = metrics.get_recent_sessions(limit=10)

        assert len(recent) == 10
        # 应该按时间倒序排列（最新的在前）
        assert recent[0]["session_id"] == "session_14"
        assert recent[9]["session_id"] == "session_5"

        print("✅ 最近会话查询正确")

    finally:
        temp_file.unlink()


def test_dashboard_data_structure():
    """测试仪表板数据结构"""
    print("\n测试 6: 仪表板数据结构")

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_file = Path(f.name)

    try:
        metrics = StabilityMetrics(metrics_file=temp_file)

        # 添加一些测试数据
        for i in range(5):
            session_id = f"session_{i}"
            metrics.record_session_start(session_id, f"任务 {i}")
            metrics.record_session_end(
                session_id,
                status="success" if i < 4 else "failed",
                steps=10,
                failure_code="python_execution_error" if i == 4 else None,
                stage_history=["schema_understanding", "basic_eda"]
            )

        # 模拟仪表板数据结构
        dashboard_data = {
            "overview": {
                "total_sessions": metrics.get_aggregated_metrics()["total_sessions"],
                "success_rate": metrics.get_aggregated_metrics()["success_rate"],
                "failure_rate": metrics.get_aggregated_metrics()["failure_rate"],
                "avg_steps": metrics.get_aggregated_metrics()["avg_steps_per_session"],
                "avg_duration": metrics.get_aggregated_metrics()["avg_duration_seconds"],
                "recovery_success_rate": metrics.get_aggregated_metrics()["recovery_success_rate"],
            },
            "detailed_metrics": metrics.get_aggregated_metrics(),
            "stage_stats": metrics.get_stage_completion_stats(),
            "failure_breakdown": metrics.get_failure_breakdown(),
            "recent_sessions": metrics.get_recent_sessions(limit=10),
            "timestamp": datetime.now().isoformat(),
        }

        # 验证数据结构
        assert "overview" in dashboard_data
        assert "detailed_metrics" in dashboard_data
        assert "stage_stats" in dashboard_data
        assert "failure_breakdown" in dashboard_data
        assert "recent_sessions" in dashboard_data
        assert "timestamp" in dashboard_data

        print("✅ 仪表板数据结构正确")

        # 打印示例数据
        print("\n📊 仪表板数据示例:")
        print(json.dumps(dashboard_data, indent=2, ensure_ascii=False, default=str))

    finally:
        temp_file.unlink()


def main():
    """运行所有测试"""
    print("=" * 60)
    print("监控仪表板单元测试")
    print("=" * 60)

    test_stability_metrics_collection()
    test_aggregated_metrics()
    test_failure_breakdown()
    test_stage_completion_stats()
    test_recent_sessions()
    test_dashboard_data_structure()

    print("\n" + "=" * 60)
    print("✅ 所有测试通过 (6/6)")
    print("=" * 60)


if __name__ == "__main__":
    main()
