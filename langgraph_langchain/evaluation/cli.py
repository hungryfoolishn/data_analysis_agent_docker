"""Command-line release gate for persisted evaluation summaries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .gate import ReleaseGate, ReleaseGatePolicy
from .models import EvaluationSummary


def _read_summary(path: Path) -> EvaluationSummary:
    return EvaluationSummary.model_validate_json(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply the DeepAnalyze deterministic release gate")
    parser.add_argument("current", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--policy", type=Path)
    args = parser.parse_args(argv)
    policy = (
        ReleaseGatePolicy.model_validate_json(args.policy.read_text(encoding="utf-8"))
        if args.policy else ReleaseGatePolicy()
    )
    decision = ReleaseGate(policy).decide(
        _read_summary(args.current),
        _read_summary(args.baseline) if args.baseline else None,
    )
    print(json.dumps(decision.model_dump(), ensure_ascii=False, indent=2))
    return 0 if decision.allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
