"""
User-Friendly Response Transformer

Transforms technical API responses into business-friendly format by:
1. Hiding technical details (session_id, workspace, tool execution details)
2. Emphasizing business outputs (conclusions, evidence, recommendations, risks)
3. Simplifying error messages for non-technical users
4. Providing actionable progress updates
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
import re


class UserFriendlyResponseTransformer:
    """Transform technical responses into user-friendly format."""

    def __init__(self, technical_mode: bool = False):
        """
        Args:
            technical_mode: If True, include technical details for power users
        """
        self.technical_mode = technical_mode

    def transform_analysis_response(
        self,
        raw_output: str,
        generated_files: List[str],
        session_id: str,
        execution_time_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Transform raw analysis output into user-friendly response.

        Args:
            raw_output: Raw agent output text
            generated_files: List of generated artifact filenames
            session_id: Session identifier
            execution_time_seconds: Total execution time

        Returns:
            User-friendly response dict
        """
        # Extract structured components from output
        findings = self._extract_findings(raw_output)
        recommendations = self._extract_recommendations(raw_output)
        risks = self._extract_risks(raw_output)
        summary = self._extract_summary(raw_output)

        # Build user-friendly response
        response = {
            "status": "success",
            "summary": summary or "Analysis completed successfully.",
            "findings": findings,
            "recommendations": recommendations,
            "risks": risks,
            "artifacts": self._format_artifacts(generated_files),
        }

        # Add execution metadata (simplified)
        if execution_time_seconds:
            response["execution_time"] = f"{execution_time_seconds:.1f}s"

        # Technical mode: include additional details
        if self.technical_mode:
            response["_technical"] = {
                "session_id": session_id,
                "raw_output": raw_output,
                "generated_files": generated_files,
            }

        return response

    def transform_error_response(
        self,
        error_detail: Dict[str, Any],
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Transform technical error into user-friendly error message.

        Args:
            error_detail: Technical error detail dict
            session_id: Session identifier

        Returns:
            User-friendly error response
        """
        error_code = error_detail.get("code", "unknown_error")
        technical_message = error_detail.get("message", "An error occurred")

        # Map technical errors to user-friendly messages
        user_message = self._get_user_friendly_error_message(error_code, technical_message)

        response = {
            "status": "error",
            "message": user_message,
            "can_retry": error_detail.get("retryable", False),
        }

        # Add actionable suggestions
        suggestion = self._get_error_suggestion(error_code, error_detail)
        if suggestion:
            response["suggestion"] = suggestion

        # Technical mode: include full error details
        if self.technical_mode:
            response["_technical"] = {
                "error_code": error_code,
                "error_type": error_detail.get("error_type"),
                "technical_message": technical_message,
                "session_id": session_id,
            }

        return response

    def transform_progress_update(
        self,
        stage: str,
        step_description: str,
        progress_percent: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Transform technical progress update into user-friendly format.

        Args:
            stage: Current stage (e.g., "data_exploration", "analysis")
            step_description: Technical step description
            progress_percent: Optional progress percentage

        Returns:
            User-friendly progress update
        """
        # Map technical stages to user-friendly descriptions
        stage_map = {
            "data_exploration": "Understanding your data",
            "schema_understanding": "Analyzing data structure",
            "analysis": "Performing analysis",
            "visualization": "Creating visualizations",
            "report_generation": "Preparing final report",
            "validation": "Validating results",
        }

        user_stage = stage_map.get(stage, "Processing")

        # Simplify step description
        user_step = self._simplify_step_description(step_description)

        response = {
            "status": "in_progress",
            "current_stage": user_stage,
            "activity": user_step,
        }

        if progress_percent is not None:
            response["progress"] = f"{progress_percent}%"

        if self.technical_mode:
            response["_technical"] = {
                "stage": stage,
                "step_description": step_description,
            }

        return response

    # ── Private helper methods ────────────────────────────────────────────────

    def _extract_findings(self, output: str) -> List[Dict[str, Any]]:
        """Extract key findings from output."""
        findings = []

        # Look for findings section
        findings_match = re.search(
            r"(?:## Key Findings|## Findings|### Findings)(.*?)(?=##|\Z)",
            output,
            re.DOTALL | re.IGNORECASE
        )

        if findings_match:
            findings_text = findings_match.group(1)
            # Extract bullet points
            for line in findings_text.split('\n'):
                line = line.strip()
                if line.startswith('-') or line.startswith('*'):
                    finding_text = line.lstrip('-*').strip()
                    if finding_text:
                        findings.append({
                            "statement": finding_text,
                            "confidence": "high"  # Could be extracted if available
                        })

        return findings

    def _extract_recommendations(self, output: str) -> List[str]:
        """Extract recommendations from output."""
        recommendations = []

        # Look for recommendations section
        rec_match = re.search(
            r"(?:## Recommendations|## Actions|### Next Steps)(.*?)(?=##|\Z)",
            output,
            re.DOTALL | re.IGNORECASE
        )

        if rec_match:
            rec_text = rec_match.group(1)
            for line in rec_text.split('\n'):
                line = line.strip()
                if line.startswith('-') or line.startswith('*'):
                    rec = line.lstrip('-*').strip()
                    if rec:
                        recommendations.append(rec)

        return recommendations

    def _extract_risks(self, output: str) -> List[Dict[str, str]]:
        """Extract risks and caveats from output."""
        risks = []

        # Look for risks/caveats section
        risk_match = re.search(
            r"(?:## Risks|## Caveats|## Limitations|### Assumptions)(.*?)(?=##|\Z)",
            output,
            re.DOTALL | re.IGNORECASE
        )

        if risk_match:
            risk_text = risk_match.group(1)
            for line in risk_text.split('\n'):
                line = line.strip()
                if line.startswith('-') or line.startswith('*'):
                    risk = line.lstrip('-*').strip()
                    if risk:
                        risks.append({
                            "description": risk,
                            "severity": "medium"  # Could be extracted if available
                        })

        return risks

    def _extract_summary(self, output: str) -> Optional[str]:
        """Extract executive summary from output."""
        # Look for summary section
        summary_match = re.search(
            r"(?:## Summary|## Executive Summary|### Overview)(.*?)(?=##|\Z)",
            output,
            re.DOTALL | re.IGNORECASE
        )

        if summary_match:
            summary = summary_match.group(1).strip()
            # Take first paragraph
            first_para = summary.split('\n\n')[0].strip()
            return first_para

        # Fallback: take first non-empty paragraph
        paragraphs = [p.strip() for p in output.split('\n\n') if p.strip()]
        if paragraphs:
            return paragraphs[0][:300]  # Limit length

        return None

    def _format_artifacts(self, generated_files: List[str]) -> List[Dict[str, str]]:
        """Format artifact list for user consumption."""
        artifacts = []

        for filename in generated_files:
            # Determine artifact type
            if filename.endswith('.png') or filename.endswith('.jpg'):
                artifact_type = "visualization"
            elif filename.endswith('.csv'):
                artifact_type = "data"
            elif filename.endswith('.html'):
                artifact_type = "report"
            else:
                artifact_type = "file"

            # Create user-friendly description
            description = self._get_artifact_description(filename, artifact_type)

            artifacts.append({
                "filename": filename,
                "type": artifact_type,
                "description": description,
            })

        return artifacts

    def _get_artifact_description(self, filename: str, artifact_type: str) -> str:
        """Generate user-friendly artifact description."""
        descriptions = {
            "visualization": "Chart or graph showing analysis results",
            "data": "Processed data file",
            "report": "Detailed analysis report",
            "file": "Generated file",
        }

        base_desc = descriptions.get(artifact_type, "Generated file")

        # Try to extract meaningful name from filename
        name_part = filename.replace('_', ' ').replace('-', ' ')
        name_part = re.sub(r'\.(png|jpg|csv|html)$', '', name_part, flags=re.IGNORECASE)

        if name_part and name_part != filename:
            return f"{base_desc}: {name_part}"

        return base_desc

    def _get_user_friendly_error_message(self, error_code: str, technical_message: str) -> str:
        """Map technical error to user-friendly message."""
        error_messages = {
            "missing_api_key": "Service configuration error. Please contact support.",
            "missing_user_message": "Please provide a question or instruction for analysis.",
            "no_data_file": "No data file found. Please upload a data file first.",
            "invalid_data_file": "The data file format is not supported or is corrupted.",
            "schema_understanding_failed": "Unable to understand the data structure. The file may be malformed.",
            "analysis_failed": "Analysis could not be completed. Please try rephrasing your question.",
            "tool_execution_failed": "An error occurred during data processing.",
            "timeout": "Analysis is taking longer than expected. Please try with a smaller dataset.",
            "cancelled": "Analysis was cancelled.",
            "session_not_found": "Session expired. Please start a new analysis.",
            "session_workspace_missing": "Session data not found. Please start a new analysis.",
        }

        return error_messages.get(error_code, "An unexpected error occurred. Please try again.")

    def _get_error_suggestion(self, error_code: str, error_detail: Dict[str, Any]) -> Optional[str]:
        """Provide actionable suggestion for error."""
        suggestions = {
            "no_data_file": "Upload a CSV, Excel, or JSON file to begin analysis.",
            "invalid_data_file": "Ensure your file is a valid CSV, Excel, or JSON format.",
            "schema_understanding_failed": "Check that your file has proper column headers and data.",
            "analysis_failed": "Try asking a more specific question about your data.",
            "timeout": "Consider filtering your data or asking a simpler question.",
            "session_not_found": "Start a new session by uploading your data file.",
        }

        suggestion = suggestions.get(error_code)

        # Add hint from error detail if available
        hint = error_detail.get("hint")
        if hint and not suggestion:
            return hint

        return suggestion

    def _simplify_step_description(self, step_description: str) -> str:
        """Simplify technical step description for users."""
        # Remove tool names and technical jargon
        simplified = step_description

        # Map common technical terms to user-friendly terms
        replacements = {
            "python_repl_tool": "running calculations",
            "eda_profile": "exploring data",
            "record_finding": "recording insights",
            "declare_metric": "defining metrics",
            "finish_report": "finalizing report",
            "executing": "processing",
            "validating": "checking",
        }

        for tech_term, user_term in replacements.items():
            simplified = simplified.replace(tech_term, user_term)

        # Capitalize first letter
        simplified = simplified[0].upper() + simplified[1:] if simplified else simplified

        return simplified


def create_transformer(technical_mode: bool = False) -> UserFriendlyResponseTransformer:
    """Factory function to create response transformer."""
    return UserFriendlyResponseTransformer(technical_mode=technical_mode)
