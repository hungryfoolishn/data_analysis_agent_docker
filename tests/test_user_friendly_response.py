"""
Tests for User-Friendly Response Transformer
"""

import pytest
from langgraph_langchain.user_friendly_response import (
    UserFriendlyResponseTransformer,
    create_transformer,
)


class TestUserFriendlyResponseTransformer:
    """Test suite for response transformer."""

    def test_transformer_initialization(self):
        """Test transformer can be initialized."""
        transformer = UserFriendlyResponseTransformer()
        assert transformer.technical_mode is False

        tech_transformer = UserFriendlyResponseTransformer(technical_mode=True)
        assert tech_transformer.technical_mode is True

    def test_factory_function(self):
        """Test factory function creates transformer."""
        transformer = create_transformer()
        assert isinstance(transformer, UserFriendlyResponseTransformer)
        assert transformer.technical_mode is False

        tech_transformer = create_transformer(technical_mode=True)
        assert tech_transformer.technical_mode is True

    def test_transform_analysis_response_basic(self):
        """Test basic analysis response transformation."""
        transformer = UserFriendlyResponseTransformer()

        raw_output = """
## Summary
Sales increased by 25% in Q4 compared to Q3.

## Key Findings
- Revenue grew from $100K to $125K
- Customer count increased by 15%
- Average order value remained stable

## Recommendations
- Focus on customer retention
- Expand marketing in high-growth regions
"""

        response = transformer.transform_analysis_response(
            raw_output=raw_output,
            generated_files=["sales_chart.png", "revenue_data.csv"],
            session_id="test-session-123",
            execution_time_seconds=45.2,
        )

        assert response["status"] == "success"
        assert "Sales increased by 25%" in response["summary"]
        assert len(response["findings"]) == 3
        assert len(response["recommendations"]) == 2
        assert len(response["artifacts"]) == 2
        assert response["execution_time"] == "45.2s"
        assert "_technical" not in response

    def test_transform_analysis_response_with_technical_mode(self):
        """Test analysis response includes technical details in technical mode."""
        transformer = UserFriendlyResponseTransformer(technical_mode=True)

        raw_output = "## Summary\nTest analysis completed."

        response = transformer.transform_analysis_response(
            raw_output=raw_output,
            generated_files=["chart.png"],
            session_id="test-session-456",
        )

        assert response["status"] == "success"
        assert "_technical" in response
        assert response["_technical"]["session_id"] == "test-session-456"
        assert response["_technical"]["raw_output"] == raw_output

    def test_extract_findings(self):
        """Test finding extraction from output."""
        transformer = UserFriendlyResponseTransformer()

        output = """
## Key Findings
- Revenue increased by 20%
- Customer churn decreased to 5%
- New product line contributed 30% of sales

## Other Section
Some other content
"""

        findings = transformer._extract_findings(output)

        assert len(findings) == 3
        assert findings[0]["statement"] == "Revenue increased by 20%"
        assert findings[1]["statement"] == "Customer churn decreased to 5%"
        assert findings[2]["statement"] == "New product line contributed 30% of sales"
        assert all(f["confidence"] == "high" for f in findings)

    def test_extract_recommendations(self):
        """Test recommendation extraction."""
        transformer = UserFriendlyResponseTransformer()

        output = """
## Recommendations
- Increase marketing budget
- Hire additional sales staff
- Expand to new markets

## Summary
Some summary text
"""

        recommendations = transformer._extract_recommendations(output)

        assert len(recommendations) == 3
        assert "Increase marketing budget" in recommendations
        assert "Hire additional sales staff" in recommendations

    def test_extract_risks(self):
        """Test risk extraction."""
        transformer = UserFriendlyResponseTransformer()

        output = """
## Risks
- Data quality issues in Q1
- Small sample size for regional analysis
- Seasonal effects not accounted for

## Findings
Some findings
"""

        risks = transformer._extract_risks(output)

        assert len(risks) == 3
        assert risks[0]["description"] == "Data quality issues in Q1"
        assert risks[1]["description"] == "Small sample size for regional analysis"
        assert all(r["severity"] == "medium" for r in risks)

    def test_extract_summary(self):
        """Test summary extraction."""
        transformer = UserFriendlyResponseTransformer()

        output = """
## Summary
This analysis shows strong growth in Q4 with revenue increasing by 25%.

## Key Findings
- Finding 1
"""

        summary = transformer._extract_summary(output)

        assert summary is not None
        assert "strong growth in Q4" in summary
        assert "25%" in summary

    def test_extract_summary_fallback(self):
        """Test summary extraction falls back to first paragraph."""
        transformer = UserFriendlyResponseTransformer()

        output = """
The analysis reveals significant trends in customer behavior.

## Key Findings
- Finding 1
"""

        summary = transformer._extract_summary(output)

        assert summary is not None
        assert "customer behavior" in summary

    def test_format_artifacts(self):
        """Test artifact formatting."""
        transformer = UserFriendlyResponseTransformer()

        files = [
            "sales_trend_chart.png",
            "customer_data.csv",
            "full_report.html",
            "notes.txt",
        ]

        artifacts = transformer._format_artifacts(files)

        assert len(artifacts) == 4
        assert artifacts[0]["type"] == "visualization"
        assert artifacts[1]["type"] == "data"
        assert artifacts[2]["type"] == "report"
        assert artifacts[3]["type"] == "file"
        assert all("description" in a for a in artifacts)

    def test_transform_error_response_basic(self):
        """Test basic error response transformation."""
        transformer = UserFriendlyResponseTransformer()

        error_detail = {
            "code": "no_data_file",
            "message": "No data file found in session",
            "retryable": True,
        }

        response = transformer.transform_error_response(
            error_detail=error_detail,
            session_id="test-session-789",
        )

        assert response["status"] == "error"
        assert "No data file found" in response["message"]
        assert response["can_retry"] is True
        assert "suggestion" in response
        assert "Upload" in response["suggestion"]
        assert "_technical" not in response

    def test_transform_error_response_with_technical_mode(self):
        """Test error response includes technical details in technical mode."""
        transformer = UserFriendlyResponseTransformer(technical_mode=True)

        error_detail = {
            "code": "analysis_failed",
            "message": "Tool execution failed: division by zero",
            "error_type": "analysis_error",
            "retryable": False,
        }

        response = transformer.transform_error_response(
            error_detail=error_detail,
            session_id="test-session-999",
        )

        assert response["status"] == "error"
        assert "_technical" in response
        assert response["_technical"]["error_code"] == "analysis_failed"
        assert response["_technical"]["error_type"] == "analysis_error"
        assert response["_technical"]["session_id"] == "test-session-999"

    def test_get_user_friendly_error_message(self):
        """Test error message mapping."""
        transformer = UserFriendlyResponseTransformer()

        # Test known error codes
        msg1 = transformer._get_user_friendly_error_message(
            "no_data_file", "Technical message"
        )
        assert "No data file found" in msg1

        msg2 = transformer._get_user_friendly_error_message(
            "timeout", "Request timeout"
        )
        assert "taking longer than expected" in msg2

        # Test unknown error code
        msg3 = transformer._get_user_friendly_error_message(
            "unknown_error_xyz", "Some technical error"
        )
        assert "unexpected error" in msg3

    def test_get_error_suggestion(self):
        """Test error suggestion generation."""
        transformer = UserFriendlyResponseTransformer()

        # Test known error with suggestion
        suggestion1 = transformer._get_error_suggestion(
            "no_data_file", {"code": "no_data_file"}
        )
        assert suggestion1 is not None
        assert "Upload" in suggestion1

        # Test error with hint
        suggestion2 = transformer._get_error_suggestion(
            "unknown_error",
            {"code": "unknown_error", "hint": "Check your input format"}
        )
        assert suggestion2 == "Check your input format"

        # Test error without suggestion
        suggestion3 = transformer._get_error_suggestion(
            "some_random_error", {"code": "some_random_error"}
        )
        assert suggestion3 is None

    def test_transform_progress_update(self):
        """Test progress update transformation."""
        transformer = UserFriendlyResponseTransformer()

        response = transformer.transform_progress_update(
            stage="data_exploration",
            step_description="Executing eda_profile on dataset",
            progress_percent=25,
        )

        assert response["status"] == "in_progress"
        assert response["current_stage"] == "Understanding your data"
        assert "exploring data" in response["activity"]
        assert response["progress"] == "25%"
        assert "_technical" not in response

    def test_transform_progress_update_with_technical_mode(self):
        """Test progress update includes technical details in technical mode."""
        transformer = UserFriendlyResponseTransformer(technical_mode=True)

        response = transformer.transform_progress_update(
            stage="analysis",
            step_description="Running python_repl_tool",
        )

        assert response["status"] == "in_progress"
        assert "_technical" in response
        assert response["_technical"]["stage"] == "analysis"

    def test_simplify_step_description(self):
        """Test step description simplification."""
        transformer = UserFriendlyResponseTransformer()

        # Test technical term replacement
        simplified1 = transformer._simplify_step_description(
            "executing python_repl_tool for calculations"
        )
        assert "running calculations" in simplified1
        assert "python_repl_tool" not in simplified1

        simplified2 = transformer._simplify_step_description(
            "validating eda_profile results"
        )
        assert "exploring data" in simplified2
        assert "Checking" in simplified2 or "checking" in simplified2.lower()

    def test_get_artifact_description(self):
        """Test artifact description generation."""
        transformer = UserFriendlyResponseTransformer()

        desc1 = transformer._get_artifact_description(
            "sales_trend_chart.png", "visualization"
        )
        assert "Chart or graph" in desc1
        assert "sales trend chart" in desc1

        desc2 = transformer._get_artifact_description(
            "data.csv", "data"
        )
        assert "Processed data file" in desc2

    def test_empty_output_handling(self):
        """Test handling of empty or minimal output."""
        transformer = UserFriendlyResponseTransformer()

        response = transformer.transform_analysis_response(
            raw_output="",
            generated_files=[],
            session_id="test-session",
        )

        assert response["status"] == "success"
        assert response["summary"] == "Analysis completed successfully."
        assert response["findings"] == []
        assert response["recommendations"] == []
        assert response["risks"] == []
        assert response["artifacts"] == []

    def test_multiple_sections_extraction(self):
        """Test extraction when multiple similar sections exist."""
        transformer = UserFriendlyResponseTransformer()

        output = """
## Key Findings
- Finding 1
- Finding 2

## Analysis Details
Some details here

## Additional Findings
- Finding 3

## Recommendations
- Recommendation 1
- Recommendation 2
"""

        findings = transformer._extract_findings(output)
        recommendations = transformer._extract_recommendations(output)

        # Should extract from first matching section
        assert len(findings) == 2
        assert len(recommendations) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
