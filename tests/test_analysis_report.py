from __future__ import annotations

import pytest

from langgraph_langchain.runtime.analysis_report import (
    AnalysisReport,
    ReportFinding,
    ReportSection,
    ReportValidationError,
    build_analysis_report,
    render_analysis_report,
    validate_analysis_report,
)


def _evidence(
    evidence_id: str = "evid_1",
    *,
    numeric_values: dict | None = None,
    truncated: bool = False,
    validation_status: str = "verified",
) -> dict:
    return {
        "validation_status": validation_status,
        "truncated": truncated,
        "numeric_values": numeric_values or {"total": 120.5},
        "evidence_text": "verified SQL execution",
    }


def _report(**overrides) -> AnalysisReport:
    finding = ReportFinding(
        finding_id="F001",
        claim="North revenue was 120.5.",
        supported_by=["evid_1"],
        referenced_asset_ids=["asset_orders"],
        calculation_ref="sqlite_query:evid_1",
        confidence="high",
    )
    fields = {
        "task_id": "task_1",
        "user_goal": "Summarize revenue by region",
        "status": "success",
        "executive_summary": "North revenue was 120.5.",
        "sections": [
            ReportSection(
                title="Revenue",
                body="Aggregated registered SQLite orders.",
                finding_ids=["F001"],
            )
        ],
        "findings": [finding],
        "artifact_ids": [],
        "limitations": [],
    }
    fields.update(overrides)
    return AnalysisReport(**fields)


def test_builds_and_validates_evidence_backed_report():
    report = _report()
    result = validate_analysis_report(
        report,
        evidence_records={"evid_1": _evidence()},
        asset_ids={"asset_orders"},
        artifact_ids=set(),
    )

    assert result.is_valid is True
    assert result.status == "valid"
    assert result.errors == []
    assert "F001" in render_analysis_report(report)
    assert "evid_1" in render_analysis_report(report)


def test_missing_or_unverified_evidence_is_invalid():
    report = _report()
    result = validate_analysis_report(
        report,
        evidence_records={
            "evid_1": _evidence(validation_status="unverified"),
        },
        asset_ids={"asset_orders"},
    )

    assert result.status == "invalid"
    assert any("unverified evidence" in error for error in result.errors)

    result = validate_analysis_report(
        report,
        evidence_records={},
        asset_ids={"asset_orders"},
    )
    assert any("Unknown evidence reference" in error for error in result.errors)


def test_unknown_asset_and_artifact_are_invalid():
    report = _report(
        artifact_ids=["artifact_missing"],
    )
    report.findings[0].referenced_asset_ids.append("asset_missing")

    result = validate_analysis_report(
        report,
        evidence_records={"evid_1": _evidence()},
        asset_ids=set(),
        artifact_ids=set(),
    )

    assert any("unknown asset asset_missing" in error for error in result.errors)
    assert any("Unknown artifact reference" in error for error in result.errors)


def test_report_number_must_match_evidence():
    report = _report()
    report.findings[0].claim = "North revenue was 999.9."

    result = validate_analysis_report(
        report,
        evidence_records={"evid_1": _evidence()},
        asset_ids={"asset_orders"},
    )

    assert result.status == "invalid"
    assert any("untraceable numbers: 999.9" in error for error in result.errors)


def test_truncated_evidence_requires_limitation_warning():
    report = _report()
    report.findings[0].limitation_notes = []
    report.limitations = []

    result = validate_analysis_report(
        report,
        evidence_records={"evid_1": _evidence(truncated=True)},
        asset_ids={"asset_orders"},
    )

    assert result.status == "warning"
    assert any("truncated data" in warning for warning in result.warnings)

    report.limitations = ["The input query result was truncated."]
    result = validate_analysis_report(
        report,
        evidence_records={"evid_1": _evidence(truncated=True)},
        asset_ids={"asset_orders"},
    )
    assert result.status == "valid"


def test_partial_failure_report_can_be_rendered_without_success_claim():
    report = build_analysis_report(
        task_id="task_1",
        user_goal="Analyze mixed results",
        status="partial_success",
        executive_summary="Only the verified SQLite step completed.",
        findings=[
            ReportFinding(
                finding_id="F001",
                claim="The verified query returned 6 rows.",
                supported_by=["evid_rows"],
                referenced_asset_ids=["asset_query"],
            )
        ],
        limitations=["Other requested analysis steps did not complete."],
    )

    result = validate_analysis_report(
        report,
        evidence_records={
            "evid_rows": _evidence(numeric_values={"rows": 6}),
        },
        asset_ids={"asset_query"},
    )

    assert result.is_valid is True
    assert report.status == "partial_success"
    assert "partial_success" in render_analysis_report(report)


def test_successful_report_without_known_evidence_is_invalid():
    report = build_analysis_report(
        task_id="task_1",
        user_goal="No evidence",
        status="success",
        executive_summary="Unsupported",
        findings=[
            ReportFinding(
                finding_id="F001",
                claim="Unsupported claim",
                supported_by=["evid_missing"],
            )
        ],
    )

    result = validate_analysis_report(
        report,
        evidence_records={},
        asset_ids=set(),
    )

    assert result.status == "invalid"
    assert any("Unknown evidence reference" in error for error in result.errors)
