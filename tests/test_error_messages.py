"""
Test User-Friendly Error Messages

This test verifies that all failure codes have user-friendly error messages.
"""

import pytest
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from langgraph_langchain.schemas import FailureCode
from langgraph_langchain.error_messages import (
    ERROR_MESSAGES,
    format_user_friendly_error,
    format_error_for_display,
    get_error_title,
    get_error_suggestions,
)


class TestErrorMessageCoverage:
    """Test that all failure codes have error messages"""

    def test_all_failure_codes_have_messages(self):
        """Verify every failure code has a user-friendly message"""
        # Get all failure codes from the Literal type
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

        missing_messages = []
        for code in failure_codes:
            if code not in ERROR_MESSAGES:
                missing_messages.append(code)

        assert len(missing_messages) == 0, f"Missing error messages for: {missing_messages}"

    def test_all_messages_have_required_fields(self):
        """Verify all error messages have required fields"""
        for code, error_msg in ERROR_MESSAGES.items():
            assert error_msg.title, f"{code}: missing title"
            assert error_msg.message, f"{code}: missing message"
            assert error_msg.suggestions, f"{code}: missing suggestions"
            assert len(error_msg.suggestions) > 0, f"{code}: suggestions list is empty"

    def test_messages_are_user_friendly(self):
        """Verify messages don't contain technical jargon"""
        technical_terms = ["traceback", "exception", "stack", "null pointer", "segfault"]

        for code, error_msg in ERROR_MESSAGES.items():
            message_text = f"{error_msg.title} {error_msg.message}".lower()
            for term in technical_terms:
                assert term not in message_text, f"{code}: contains technical term '{term}'"


class TestFormatUserFriendlyError:
    """Test the format_user_friendly_error function"""

    def test_format_known_error(self):
        """Test formatting a known error code"""
        result = format_user_friendly_error("missing_data_file")

        assert result["title"] == "数据文件未找到"
        assert "无法找到" in result["message"]
        assert len(result["suggestions"]) >= 2
        assert result["recovery_hint"] is not None

    def test_format_unknown_error(self):
        """Test formatting an unknown error code"""
        result = format_user_friendly_error("unknown_error_code")

        assert result["title"] == "未知错误"
        assert "未知错误" in result["message"]
        assert len(result["suggestions"]) > 0

    def test_format_with_technical_message(self):
        """Test including technical details"""
        result = format_user_friendly_error(
            "python_execution_error",
            technical_message="KeyError: 'column_name'"
        )

        assert "technical_details" in result
        assert result["technical_details"] == "KeyError: 'column_name'"

    def test_format_with_context(self):
        """Test including context information"""
        result = format_user_friendly_error(
            "schema_understanding_failed",
            context={"file_name": "data.csv", "row_count": 1000}
        )

        assert "context" in result
        assert result["context"]["file_name"] == "data.csv"


class TestFormatErrorForDisplay:
    """Test the format_error_for_display function"""

    def test_basic_display_format(self):
        """Test basic markdown formatting"""
        display = format_error_for_display("missing_data_file")

        assert "## ❌" in display
        assert "数据文件未找到" in display
        assert "### 建议操作" in display
        assert "1." in display  # Numbered suggestions

    def test_display_with_technical_details(self):
        """Test including technical details in display"""
        display = format_error_for_display(
            "python_execution_error",
            technical_message="ValueError: invalid literal",
            include_technical=True
        )

        assert "<details>" in display
        assert "技术细节" in display
        assert "ValueError: invalid literal" in display

    def test_display_without_technical_details(self):
        """Test excluding technical details"""
        display = format_error_for_display(
            "python_execution_error",
            technical_message="ValueError: invalid literal",
            include_technical=False
        )

        assert "<details>" not in display
        assert "ValueError" not in display

    def test_display_includes_recovery_hint(self):
        """Test that recovery hint is included"""
        display = format_error_for_display("max_steps_exceeded")

        assert "**下一步**" in display
        assert "缩小分析范围" in display or "重试" in display


class TestHelperFunctions:
    """Test helper functions"""

    def test_get_error_title(self):
        """Test getting error title"""
        title = get_error_title("missing_data_file")
        assert title == "数据文件未找到"

        unknown_title = get_error_title("unknown_code")
        assert unknown_title == "未知错误"

    def test_get_error_suggestions(self):
        """Test getting error suggestions"""
        suggestions = get_error_suggestions("schema_understanding_failed")
        assert len(suggestions) >= 3
        assert any("格式" in s for s in suggestions)

        unknown_suggestions = get_error_suggestions("unknown_code")
        assert len(unknown_suggestions) > 0


class TestSpecificErrorMessages:
    """Test specific error messages for quality"""

    def test_schema_understanding_failed_message(self):
        """Test schema understanding error has good suggestions"""
        result = format_user_friendly_error("schema_understanding_failed")

        suggestions_text = " ".join(result["suggestions"])
        assert "CSV" in suggestions_text or "Excel" in suggestions_text
        assert "表头" in suggestions_text or "格式" in suggestions_text

    def test_field_semantic_unclear_message(self):
        """Test field semantic error has actionable suggestions"""
        result = format_user_friendly_error("field_semantic_unclear")

        suggestions_text = " ".join(result["suggestions"])
        assert "字段" in suggestions_text
        assert "说明" in suggestions_text or "描述" in suggestions_text

    def test_max_steps_exceeded_message(self):
        """Test max steps error suggests simplification"""
        result = format_user_friendly_error("max_steps_exceeded")

        suggestions_text = " ".join(result["suggestions"])
        assert "简化" in suggestions_text or "拆分" in suggestions_text

    def test_timeout_message(self):
        """Test timeout error suggests data reduction"""
        result = format_user_friendly_error("timeout")

        suggestions_text = " ".join(result["suggestions"])
        assert "数据量" in suggestions_text or "采样" in suggestions_text


class TestErrorMessageQuality:
    """Test the quality of error messages"""

    def test_suggestions_are_actionable(self):
        """Verify suggestions contain action verbs"""
        action_verbs = ["检查", "确认", "尝试", "提供", "重新", "简化", "联系", "创建"]

        for code, error_msg in ERROR_MESSAGES.items():
            suggestions_text = " ".join(error_msg.suggestions)
            has_action = any(verb in suggestions_text for verb in action_verbs)
            assert has_action, f"{code}: suggestions lack action verbs"

    def test_messages_are_concise(self):
        """Verify messages are not too long"""
        for code, error_msg in ERROR_MESSAGES.items():
            assert len(error_msg.title) < 50, f"{code}: title too long"
            assert len(error_msg.message) < 200, f"{code}: message too long"
            for suggestion in error_msg.suggestions:
                assert len(suggestion) < 150, f"{code}: suggestion too long"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
