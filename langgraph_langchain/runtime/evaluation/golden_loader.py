"""Load Runtime V8.5 golden cases and their versioned datasets."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from langgraph_langchain.data.assets import canonical_text_sha256

from .models import GoldenCase


def default_golden_cases_dir() -> Path:
    """Return the checked-in golden dataset directory when it exists."""
    package_root = Path(__file__).resolve().parents[3]
    return package_root / "tests" / "evaluation" / "golden_cases"


class GoldenCaseLoader:
    """Load and validate a directory of versioned golden cases."""

    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(directory) if directory else default_golden_cases_dir()

    def load(self) -> list[GoldenCase]:
        if not self.directory.is_dir():
            raise FileNotFoundError(f"Golden cases directory not found: {self.directory}")

        cases: list[GoldenCase] = []
        for path in sorted(self.directory.glob("*.json")):
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
            entries = raw.get("cases", []) if isinstance(raw, dict) else raw
            if not isinstance(entries, list):
                raise ValueError(f"Golden case file must contain a list: {path}")
            cases.extend(GoldenCase.model_validate(item) for item in entries)

        duplicates = [
            case_id
            for case_id, count in Counter(case.case_id for case in cases).items()
            if count > 1
        ]
        if duplicates:
            raise ValueError(f"Duplicate golden case IDs: {', '.join(sorted(duplicates))}")
        return cases

    def dataset_path(self, case: GoldenCase) -> Path:
        # Datasets live beside golden_cases (for example tests/evaluation/data),
        # so the security boundary is the evaluation root, not the JSON directory.
        evaluation_root = self.directory.parent.resolve()
        path = (self.directory / case.dataset).resolve()
        if evaluation_root not in path.parents:
            raise ValueError(f"Dataset path escapes evaluation root: {case.dataset}")
        return path

    def validate(self) -> list[str]:
        errors: list[str] = []
        try:
            cases = self.load()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return [str(exc)]

        seen = Counter(case.case_id for case in cases)
        errors.extend(
            f"Duplicate golden case ID: {case_id}"
            for case_id, count in seen.items()
            if count > 1
        )
        for case in cases:
            try:
                path = self.dataset_path(case)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if not path.is_file():
                errors.append(f"Missing dataset for {case.case_id}: {path}")
                continue
            digest = canonical_text_sha256(path)
            if case.dataset_sha256 and case.dataset_sha256 != digest:
                errors.append(
                    f"Dataset hash mismatch for {case.case_id}: expected {case.dataset_sha256}, got {digest}"
                )
        return errors

    @staticmethod
    def canonical_sha256_file(path: str | Path) -> str:
        return canonical_text_sha256(Path(path))
