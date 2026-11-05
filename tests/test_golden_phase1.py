from __future__ import annotations

import json
from pathlib import Path

import pytest

from langgraph_langchain.evaluation.golden_phase1 import (
    DATASETS,
    QUESTIONS,
    run_golden_suite,
)


def test_catalog_meets_phase1_minimum():
    assert len(DATASETS) >= 10
    assert len(QUESTIONS) >= 30
    assert len({item.dataset_id for item in QUESTIONS}) >= 8
    assert any(item.kind == "sqlite" for item in DATASETS)
    assert any(item.kind == "file" for item in DATASETS)


def test_golden_suite_runs_and_freezes_results(tmp_path: Path):
    summary = run_golden_suite(output_dir=tmp_path)

    assert summary["dataset_count"] >= 10
    assert summary["executed_count"] >= 30
    assert summary["passed_count"] >= 28
    assert summary["numeric_correct_rate"] >= 0.9
    assert summary["evidence_complete_rate"] == 1.0
    assert summary["report_valid_rate"] >= 0.9
    assert (tmp_path / "golden_results.json").is_file()
    assert (tmp_path / "golden_report.md").is_file()

    payload = json.loads((tmp_path / "golden_results.json").read_text(encoding="utf-8"))
    assert payload["suite_version"] == "phase1-mvp-v1"
