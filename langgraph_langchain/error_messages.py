"""
User-Friendly Error Messages

This module provides user-friendly error messages for all failure codes.
Each error includes:
- title: Short, user-friendly title
- message: Clear explanation of what went wrong
- suggestions: Actionable steps the user can take
- technical_details: Optional technical details (can be collapsed in UI)
"""

from typing import Dict, List, Optional
from langgraph_langchain.schemas import FailureCode


class ErrorMessage:
    """User-friendly error message"""

    def __init__(
        self,
        title: str,
        message: str,
        suggestions: List[str],
        recovery_hint: Optional[str] = None,
    ):
        self.title = title
        self.message = message
        self.suggestions = suggestions
        self.recovery_hint = recovery_hint


# Error message templates for all failure codes
ERROR_MESSAGES: Dict[str, ErrorMessage] = {
    "missing_data_file": ErrorMessage(
        title="数据文件未找到",
        message="系统无法找到您指定的数据文件",
        suggestions=[
            "检查文件路径是否正确",
            "确认文件已上传到正确位置",
            "尝试重新上传数据文件",
        ],
        recovery_hint="请提供正确的文件路径或重新上传文件",
    ),

    "session_not_found": ErrorMessage(
        title="会话不存在",
        message="系统无法找到您的分析会话",
        suggestions=[
            "会话可能已过期或被删除",
            "请创建新的分析会话",
            "检查会话 ID 是否正确",
        ],
        recovery_hint="请创建新的分析会话",
    ),

    "session_workspace_missing": ErrorMessage(
        title="工作空间丢失",
        message="分析会话的工作空间目录不存在",
        suggestions=[
            "工作空间可能已被清理",
            "请创建新的分析会话",
            "联系管理员检查存储配置",
        ],
        recovery_hint="请创建新的分析会话",
    ),

    "session_expired": ErrorMessage(
        title="会话已过期",
        message="您的分析会话已超时",
        suggestions=[
            "会话在一段时间不活动后会自动过期",
            "请创建新的分析会话",
            "如需保留结果，请及时下载报告",
        ],
        recovery_hint="请创建新的分析会话",
    ),

    "python_execution_error": ErrorMessage(
        title="代码执行错误",
        message="分析代码执行时遇到错误",
        suggestions=[
            "数据格式可能不符合预期",
            "尝试简化分析问题",
            "检查数据中是否有异常值或缺失值",
            "如果问题持续，请联系技术支持",
        ],
        recovery_hint="系统将尝试使用更简单的分析方法重试",
    ),

    "max_steps_exceeded": ErrorMessage(
        title="分析步骤超限",
        message="分析过程使用了过多步骤，可能陷入循环",
        suggestions=[
            "尝试将问题拆分为更小的子问题",
            "简化分析需求",
            "提供更明确的分析目标",
        ],
        recovery_hint="请缩小分析范围后重试",
    ),

    "report_rejected": ErrorMessage(
        title="报告质量不达标",
        message="生成的分析报告未达到质量要求",
        suggestions=[
            "系统会自动重试并改进报告",
            "如果多次失败，请简化分析问题",
            "确保数据质量良好",
        ],
        recovery_hint="系统将自动重试生成更完整的报告",
    ),

    "cancelled": ErrorMessage(
        title="分析已取消",
        message="分析过程被用户取消",
        suggestions=[
            "如需继续分析，请重新提交",
            "可以调整分析问题后再试",
        ],
        recovery_hint="请重新开始分析",
    ),

    "schema_understanding_failed": ErrorMessage(
        title="数据结构理解失败",
        message="系统无法理解您上传的数据格式或结构",
        suggestions=[
            "确保数据文件格式正确（支持 CSV、Excel）",
            "检查文件是否包含表头行",
            "确认数据编码正确（推荐使用 UTF-8）",
            "尝试简化数据结构后重新上传",
        ],
        recovery_hint="请检查数据格式后重试",
    ),

    "field_semantic_unclear": ErrorMessage(
        title="字段含义不明确",
        message="系统无法确定某些字段的业务含义",
        suggestions=[
            "在问题描述中补充字段说明",
            "重命名字段为更清晰的名称（如 'user_id' 而非 'uid'）",
            "提供数据字典或字段说明文档",
            "在问题中明确说明关键字段的含义",
        ],
        recovery_hint="请在问题中补充字段说明",
    ),

    "tool_execution_failed": ErrorMessage(
        title="工具执行失败",
        message="分析工具执行时遇到错误",
        suggestions=[
            "数据可能包含异常值",
            "检查数据类型是否正确",
            "尝试清理数据后重试",
        ],
        recovery_hint="系统将尝试使用替代方法",
    ),

    "reasoning_drift": ErrorMessage(
        title="分析偏离主题",
        message="分析过程偏离了原始问题",
        suggestions=[
            "尝试更明确地描述分析目标",
            "将复杂问题拆分为多个简单问题",
            "提供更多上下文信息",
        ],
        recovery_hint="系统将重新聚焦到原始问题",
    ),

    "report_generation_failed": ErrorMessage(
        title="报告生成失败",
        message="无法生成最终分析报告",
        suggestions=[
            "分析结果可能不完整",
            "尝试简化分析问题",
            "检查是否有足够的数据支持分析",
        ],
        recovery_hint="系统将尝试生成简化版报告",
    ),

    "timeout": ErrorMessage(
        title="分析超时",
        message="分析过程耗时过长，已超时",
        suggestions=[
            "数据量可能过大，尝试采样或筛选数据",
            "简化分析问题",
            "将复杂分析拆分为多个步骤",
        ],
        recovery_hint="请减少数据量或简化分析后重试",
    ),

    "session_interrupted": ErrorMessage(
        title="会话中断",
        message="分析会话被意外中断",
        suggestions=[
            "可能是网络连接问题",
            "可能是服务器重启",
            "请重新开始分析",
        ],
        recovery_hint="请重新开始分析",
    ),

    "disk_space_exhausted": ErrorMessage(
        title="磁盘空间不足",
        message="工作空间磁盘空间已满，无法保存分析结果",
        suggestions=[
            "减少生成的图表数量",
            "删除不必要的中间文件",
            "联系管理员扩展存储空间",
        ],
        recovery_hint="系统将尝试使用采样数据重试",
    ),

    "memory_limit_exceeded": ErrorMessage(
        title="内存超限",
        message="数据量过大，分析过程超出了内存限制",
        suggestions=[
            "尝试上传较小的数据文件",
            "仅选择关键列进行分析",
            "系统将自动采样数据进行重试",
        ],
        recovery_hint="系统将尝试使用数据采样重试",
    ),

    "network_timeout": ErrorMessage(
        title="网络超时",
        message="分析服务网络连接超时",
        suggestions=[
            "检查网络连接是否正常",
            "稍后重试分析",
            "如果问题持续，联系技术支持",
        ],
        recovery_hint="网络超时通常是暂时的，请稍后重试",
    ),

    "corrupted_data_file": ErrorMessage(
        title="数据文件损坏",
        message="上传的数据文件格式异常或已损坏",
        suggestions=[
            "检查文件是否完整（非空文件）",
            "尝试重新导出数据文件",
            "确认文件编码（推荐 UTF-8）",
            "检查文件是否包含非法字符",
        ],
        recovery_hint="请修复或重新导出数据文件后重试",
    ),

    "permission_denied": ErrorMessage(
        title="权限不足",
        message="系统没有足够的权限访问文件或目录",
        suggestions=[
            "联系管理员检查文件权限",
            "确认工作空间目录可写",
        ],
        recovery_hint="请联系管理员检查权限配置",
    ),

    "missing_api_key": ErrorMessage(
        title="API 密钥缺失",
        message="系统未配置 AI 模型的 API 密钥",
        suggestions=[
            "联系管理员配置 DEEPSEEK_API_KEY 环境变量",
            "检查 .env 文件中的 API 密钥设置",
        ],
        recovery_hint="请联系管理员配置 API 密钥",
    ),
}


def format_user_friendly_error(
    failure_code: str,
    technical_message: Optional[str] = None,
    context: Optional[Dict] = None,
) -> Dict:
    """
    Format a failure code into a user-friendly error message.

    Args:
        failure_code: The failure code from FailureCode
        technical_message: Optional technical error message
        context: Optional context dictionary with additional details

    Returns:
        Dictionary with user-friendly error information
    """
    # Get error template or use unknown error
    error_template = ERROR_MESSAGES.get(
        failure_code,
        ErrorMessage(
            title="未知错误",
            message="分析过程遇到未知错误",
            suggestions=[
                "请重试分析",
                "如果问题持续，请联系技术支持",
            ],
            recovery_hint="请重试或联系技术支持",
        ),
    )

    result = {
        "title": error_template.title,
        "message": error_template.message,
        "suggestions": error_template.suggestions,
        "recovery_hint": error_template.recovery_hint,
    }

    # Add technical details if provided
    if technical_message:
        result["technical_details"] = technical_message

    # Add context if provided
    if context:
        result["context"] = context

    return result


def format_error_for_display(
    failure_code: str,
    technical_message: Optional[str] = None,
    context: Optional[Dict] = None,
    include_technical: bool = False,
) -> str:
    """
    Format error as a markdown string for display.

    Args:
        failure_code: The failure code
        technical_message: Optional technical error message
        context: Optional context dictionary
        include_technical: Whether to include technical details

    Returns:
        Formatted markdown string
    """
    error_info = format_user_friendly_error(failure_code, technical_message, context)

    lines = [
        f"## ❌ {error_info['title']}",
        "",
        error_info['message'],
        "",
        "### 建议操作",
    ]

    for i, suggestion in enumerate(error_info['suggestions'], 1):
        lines.append(f"{i}. {suggestion}")

    if error_info.get('recovery_hint'):
        lines.extend([
            "",
            f"**下一步**: {error_info['recovery_hint']}",
        ])

    if include_technical and technical_message:
        lines.extend([
            "",
            "<details>",
            "<summary>技术细节（点击展开）</summary>",
            "",
            "```",
            technical_message,
            "```",
            "",
            "</details>",
        ])

    return "\n".join(lines)


def get_error_title(failure_code: str) -> str:
    """Get the user-friendly title for a failure code"""
    error_template = ERROR_MESSAGES.get(failure_code)
    if error_template:
        return error_template.title
    return "未知错误"


def get_error_suggestions(failure_code: str) -> List[str]:
    """Get suggestions for a failure code"""
    error_template = ERROR_MESSAGES.get(failure_code)
    if error_template:
        return error_template.suggestions
    return ["请重试分析", "如果问题持续，请联系技术支持"]
