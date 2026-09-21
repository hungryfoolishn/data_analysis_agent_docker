from __future__ import annotations

from datetime import datetime
from typing import get_args
import unittest

from pydantic import ValidationError

from langgraph_langchain.schemas import (
    AnalysisTask,
    EvidenceItem,
    ExecutorType,
    Finding,
    TaskType,
    VerificationResult,
)


class RuntimeSchemasCommit1Test(unittest.TestCase):
    def test_task_type_and_executor_type_contracts(self) -> None:
        self.assertIn("schema", get_args(TaskType))
        self.assertIn("contribution", get_args(TaskType))
        self.assertLessEqual({"structured", "react", "python"}, set(get_args(ExecutorType)))

    def test_analysis_task_defaults_and_round_trip(self) -> None:
        task = AnalysisTask(
            session_id="session_1",
            question="Calculate monthly revenue",
            task_type="metric",
            executor_type="structured",
            method="calculate_metric",
            depends_on=["task_previous"],
            expected_outputs=["monthly_revenue"],
            constraints={"output": "table"},
        )

        payload = task.model_dump()
        restored = AnalysisTask.model_validate(payload)

        self.assertEqual(restored, task)
        self.assertTrue(restored.task_id.startswith("task_"))
        self.assertEqual(restored.status, "pending")
        datetime.fromisoformat(restored.created_at)

    def test_analysis_task_rejects_unknown_executor(self) -> None:
        with self.assertRaises(ValidationError):
            AnalysisTask(
                session_id="session_1",
                question="Calculate monthly revenue",
                executor_type="unknown",
            )

    def test_verification_result_pass_and_fail_consistency(self) -> None:
        passed = VerificationResult(
            run_id="run_1",
            execution_id="exec_1",
            artifact_id="artifact_1",
            check_type="aggregation_consistency",
            status="passed",
            passed=True,
            expected=100,
            actual=100,
            tolerance=0.001,
            message="group totals match",
        )
        failed = VerificationResult(
            run_id="run_1",
            execution_id="exec_1",
            artifact_id="artifact_1",
            check_type="aggregation_consistency",
            status="failed",
            passed=False,
            expected=100,
            actual=90,
            message="group totals do not match",
        )

        self.assertEqual(VerificationResult.model_validate(passed.model_dump()), passed)
        self.assertEqual(VerificationResult.model_validate(failed.model_dump()), failed)

    def test_verification_result_rejects_inconsistent_status(self) -> None:
        cases = [
            ("passed", False),
            ("failed", True),
            ("skipped", True),
            ("not_applicable", True),
        ]
        for status, passed in cases:
            with self.subTest(status=status, passed=passed):
                with self.assertRaises(ValidationError):
                    VerificationResult(status=status, passed=passed)

    def test_evidence_item_defaults_and_legacy_payload_compatibility(self) -> None:
        item = EvidenceItem(evidence_text="Revenue decreased by 10%")
        self.assertEqual(item.verification_status, "unverified")
        self.assertIsNone(item.verification_result_id)

        legacy = {
            "evidence_id": "evidence_legacy",
            "evidence_text": "North revenue is 100",
            "source_fields": ["region", "revenue"],
        }
        restored = EvidenceItem.model_validate(legacy)
        self.assertEqual(restored.verification_status, "unverified")
        self.assertEqual(EvidenceItem.model_validate(restored.model_dump()), restored)

    def test_finding_structured_dependencies_and_round_trip(self) -> None:
        evidence = EvidenceItem(evidence_text="North revenue dropped 10% year over year")
        finding = Finding(
            finding_id="F001",
            statement="North revenue decreased most among all departments.",
            evidence=[evidence],
            finding_type="comparison",
            supported_by=[evidence.evidence_id],
            depends_on=["F000"],
            confidence_level="high",
            evidence_level="A",
        )

        payload = finding.model_dump()
        restored = Finding.model_validate(payload)

        self.assertEqual(restored, finding)
        self.assertEqual(restored.finding_type, "comparison")
        self.assertEqual(restored.supported_by, [evidence.evidence_id])
        self.assertEqual(restored.evidence[0].verification_status, "unverified")

    def test_legacy_finding_payload_still_loads(self) -> None:
        legacy = {
            "finding_id": "F_LEGACY",
            "statement": "Revenue is stable.",
            "evidence": [
                {
                    "evidence_id": "evidence_legacy",
                    "evidence_text": "Revenue is stable.",
                }
            ],
        }
        finding = Finding.model_validate(legacy)

        self.assertIsNone(finding.finding_type)
        self.assertEqual(finding.supported_by, [])
        self.assertEqual(finding.depends_on, [])
        self.assertEqual(Finding.model_validate(finding.model_dump()), finding)


if __name__ == "__main__":
    unittest.main()
