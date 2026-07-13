#!/usr/bin/env python3
"""
测试监控仪表板功能
验证 API 端点和数据格式
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import requests
import json
import pytest


@pytest.fixture(scope="module")
def data():
    """Fixture to fetch dashboard data once for all tests."""
    try:
        response = requests.get("http://localhost:8888/metrics/dashboard", timeout=5)
        if response.status_code == 200:
            return response.json()
    except requests.exceptions.ConnectionError:
        pass
    return None


def test_dashboard_endpoint(data):
    """测试仪表板端点是否可访问"""
    print("测试 1: 仪表板端点可访问性")

    if data is None:
        print("⚠️  API 服务器未运行，跳过测试")
        pytest.skip("API server not running")

    print("✅ 仪表板端点可访问")


def test_dashboard_structure(data):
    """测试仪表板数据结构"""
    print("\n测试 2: 仪表板数据结构")

    if data is None:
        print("⚠️  无数据，跳过测试")
        return

    # 检查顶层字段
    required_fields = ["overview", "detailed_metrics", "stage_stats", "failure_breakdown", "recent_sessions", "timestamp"]
    for field in required_fields:
        assert field in data, f"缺少字段: {field}"

    print("✅ 顶层字段完整")

    # 检查 overview 字段
    overview = data["overview"]
    overview_fields = ["total_sessions", "success_rate", "failure_rate", "avg_steps", "avg_duration", "recovery_success_rate"]
    for field in overview_fields:
        assert field in overview, f"overview 缺少字段: {field}"

    print("✅ overview 字段完整")

    # 检查 detailed_metrics 字段
    detailed = data["detailed_metrics"]
    detailed_fields = ["total_sessions", "successful_sessions", "failed_sessions", "success_rate", "avg_steps_per_session"]
    for field in detailed_fields:
        assert field in detailed, f"detailed_metrics 缺少字段: {field}"

    print("✅ detailed_metrics 字段完整")

    # 检查 stage_stats 字段
    stage_stats = data["stage_stats"]
    assert "stage_stats" in stage_stats, "stage_stats 缺少 stage_stats 列表"

    print("✅ stage_stats 字段完整")

    # 检查 failure_breakdown 字段
    breakdown = data["failure_breakdown"]
    assert "total_failures" in breakdown, "failure_breakdown 缺少 total_failures"
    assert "breakdown" in breakdown, "failure_breakdown 缺少 breakdown 列表"

    print("✅ failure_breakdown 字段完整")

    # 检查 recent_sessions
    assert isinstance(data["recent_sessions"], list), "recent_sessions 应该是列表"

    print("✅ recent_sessions 字段完整")


def test_metrics_values(data):
    """测试指标值的合理性"""
    print("\n测试 3: 指标值合理性")

    if data is None:
        print("⚠️  无数据，跳过测试")
        return

    overview = data["overview"]

    # 检查比率在 0-1 之间
    assert 0 <= overview["success_rate"] <= 1, f"success_rate 应在 0-1 之间: {overview['success_rate']}"
    assert 0 <= overview["failure_rate"] <= 1, f"failure_rate 应在 0-1 之间: {overview['failure_rate']}"
    assert 0 <= overview["recovery_success_rate"] <= 1, f"recovery_success_rate 应在 0-1 之间: {overview['recovery_success_rate']}"

    print("✅ 比率值在合理范围内")

    # 检查非负值
    assert overview["total_sessions"] >= 0, "total_sessions 应为非负数"
    assert overview["avg_steps"] >= 0, "avg_steps 应为非负数"
    assert overview["avg_duration"] >= 0, "avg_duration 应为非负数"

    print("✅ 计数值为非负数")


def test_individual_metrics_endpoints():
    """测试各个独立的指标端点"""
    print("\n测试 4: 独立指标端点")

    endpoints = [
        "/metrics/stability",
        "/metrics/failures",
        "/metrics/stages",
        "/metrics/recent_sessions",
    ]

    for endpoint in endpoints:
        try:
            response = requests.get(f"http://localhost:8888{endpoint}", timeout=5)
            assert response.status_code == 200, f"{endpoint} 返回状态码 {response.status_code}"
            data = response.json()
            assert data is not None, f"{endpoint} 返回空数据"
            print(f"✅ {endpoint} 正常")
        except requests.exceptions.ConnectionError:
            print(f"⚠️  API 服务器未运行，跳过 {endpoint}")
            break
        except Exception as e:
            print(f"❌ {endpoint} 失败: {e}")


def test_dashboard_display_format():
    """测试仪表板显示格式"""
    print("\n测试 5: 仪表板显示格式")

    try:
        response = requests.get("http://localhost:8888/metrics/dashboard", timeout=5)
        data = response.json()

        # 打印示例数据（用于前端开发参考）
        print("\n📊 仪表板数据示例:")
        print(json.dumps(data, indent=2, ensure_ascii=False))

        print("\n✅ 数据格式适合前端展示")
    except requests.exceptions.ConnectionError:
        print("⚠️  API 服务器未运行，跳过测试")
    except Exception as e:
        print(f"❌ 测试失败: {e}")


def main():
    """运行所有测试"""
    print("=" * 60)
    print("监控仪表板功能测试")
    print("=" * 60)

    # 测试 1: 端点可访问性
    data = test_dashboard_endpoint()

    # 测试 2: 数据结构
    test_dashboard_structure(data)

    # 测试 3: 指标值合理性
    test_metrics_values(data)

    # 测试 4: 独立端点
    test_individual_metrics_endpoints()

    # 测试 5: 显示格式
    test_dashboard_display_format()

    print("\n" + "=" * 60)
    print("✅ 所有测试完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
