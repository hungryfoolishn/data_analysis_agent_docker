"""
测试 API 服务器错误消息集成
"""
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def test_failure_detail_integration():
    """测试 _failure_detail 函数集成用户友好错误消息"""
    from langgraph_langchain.api_server_langgraph import _failure_detail

    # 测试数据文件未找到
    detail = _failure_detail(
        code="missing_data_file",
        message="FileNotFoundError: data.csv not found",
        status_code=400
    )

    assert detail["code"] == "missing_data_file"
    assert detail["title"] == "数据文件未找到"
    assert "无法找到您指定的数据文件" in detail["message"]
    assert len(detail["suggestions"]) >= 3
    assert "检查文件路径是否正确" in detail["suggestions"]
    assert detail["technical_message"] == "FileNotFoundError: data.csv not found"
    print("✓ 数据文件未找到错误消息正确")


def test_failure_detail_with_stage():
    """测试带阶段信息的错误详情"""
    from langgraph_langchain.api_server_langgraph import _failure_detail

    detail = _failure_detail(
        code="timeout",
        message="Analysis exceeded 300s timeout",
        status_code=408,
        stage="eda"
    )

    assert detail["title"] == "分析超时"
    assert "分析过程耗时过长" in detail["message"]
    assert detail["stage"] == "eda"
    assert "简化分析问题" in detail["suggestions"]
    print("✓ 带阶段信息的错误消息正确")


def test_failure_detail_retryable():
    """测试可重试错误的标记"""
    from langgraph_langchain.api_server_langgraph import _failure_detail

    detail = _failure_detail(
        code="python_execution_error",
        message="API rate limit exceeded",
        status_code=429,
        retryable=True
    )

    assert detail["retryable"] is True
    assert detail["title"] == "代码执行错误"
    assert len(detail["suggestions"]) > 0
    print("✓ 可重试错误标记正确")


def test_all_failure_codes_have_messages():
    """测试所有 FailureCode 都有对应的用户友好消息"""
    from langgraph_langchain.api_server_langgraph import _failure_detail

    # 使用 schemas.py 中实际定义的 FailureCode
    failure_codes = [
        "missing_data_file",
        "session_not_found",
        "session_workspace_missing",
        "session_expired",
        "python_execution_error",
        "max_steps_exceeded",
        "report_rejected",
        "cancelled",
        "schema_understanding_failed",
        "field_semantic_unclear",
        "tool_execution_failed",
        "reasoning_drift",
        "report_generation_failed",
        "timeout",
        "session_interrupted",
    ]

    for code in failure_codes:
        detail = _failure_detail(
            code=code,
            message=f"Test error for {code}",
            status_code=500
        )

        assert "title" in detail, f"{code} 缺少 title"
        assert "message" in detail, f"{code} 缺少 message"
        assert "suggestions" in detail, f"{code} 缺少 suggestions"
        assert len(detail["suggestions"]) > 0, f"{code} suggestions 为空"
        assert detail["technical_message"] == f"Test error for {code}"

    print(f"✓ 所有 {len(failure_codes)} 个 FailureCode 都有用户友好消息")


def test_error_response_structure():
    """测试错误响应的完整结构"""
    from langgraph_langchain.api_server_langgraph import _failure_detail

    detail = _failure_detail(
        code="report_rejected",
        message="Report validation failed: missing key sections",
        status_code=400,
        retryable=False,
        hint="Try to include more analysis details"
    )

    # 验证必需字段
    required_fields = ["type", "code", "message", "title", "suggestions", "retryable", "technical_message"]
    for field in required_fields:
        assert field in detail, f"缺少必需字段: {field}"

    # 验证可选字段
    assert "hint" in detail
    assert detail["hint"] == "Try to include more analysis details"

    # 验证内容质量
    assert detail["title"] == "报告质量不达标"
    assert "生成的分析报告未达到质量要求" in detail["message"]
    assert len(detail["suggestions"]) >= 3

    print("✓ 错误响应结构完整且正确")


if __name__ == "__main__":
    test_failure_detail_integration()
    test_failure_detail_with_stage()
    test_failure_detail_retryable()
    test_all_failure_codes_have_messages()
    test_error_response_structure()
    print("\n✅ 所有 API 错误集成测试通过 (5/5)")
