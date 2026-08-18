"""Load versioned evaluation cases from JSON without importing agent runtime."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

from .models import EvaluationCase


def load_cases(path: Path) -> list[EvaluationCase]:
    """Load either ``[{case}]`` or ``{"cases": [{case}]}`` JSON documents."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw.get("cases", []) if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise ValueError("Evaluation case document must contain a list of cases")
    return [EvaluationCase.model_validate(entry) for entry in entries]


def legacy_benchmark_cases() -> list[EvaluationCase]:
    """Provide a non-breaking migration path for the historical benchmark list."""
    from langgraph_langchain.benchmark import get_benchmark_cases

    return [
        EvaluationCase(
            case_id=legacy.case_id,
            name=legacy.name,
            question=legacy.instruction,
            data_files=[legacy.data_file],
            tags=legacy.tags,
        )
        for legacy in get_benchmark_cases()
    ]


def verify_case_data_hashes(case: EvaluationCase, base_dir: Path) -> list[str]:
    """Return deterministic data-version errors before an evaluation is run."""
    errors: list[str] = []
    for filename, expected in case.data_hashes.items():
        path = (base_dir / filename).resolve()
        if not path.is_file():
            errors.append(f"Missing evaluation data file: {filename}")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected:
            errors.append(f"Data hash mismatch for {filename}: expected {expected}, got {digest}")
    return errors
