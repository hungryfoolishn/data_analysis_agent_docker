"""Write machine-readable and human-readable evaluation reports."""

from __future__ import annotations

import html
import json
from pathlib import Path

from .models import EvaluationSummary


def write_evaluation_reports(summary: EvaluationSummary, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{summary.suite_id}.json"
    html_path = output_dir / f"{summary.suite_id}.html"
    json_path.write_text(summary.model_dump_json(indent=2), encoding="utf-8")

    category_rows = "".join(
        f"<tr><td>{html.escape(name)}</td><td>{score:.1%}</td>"
        f"<td>{summary.failure_counts.get(name, 0)}</td></tr>"
        for name, score in sorted(summary.category_scores.items())
    )
    case_rows = "".join(
        f"<tr><td>{html.escape(result.case_id)}</td>"
        f"<td>{'PASS' if result.passed else 'FAIL'}</td>"
        f"<td><code>{html.escape(json.dumps(result.failure_counts, ensure_ascii=False))}</code></td></tr>"
        for result in summary.results
    )
    document = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(summary.suite_id)}</title>
<style>body{{font:14px system-ui;margin:32px;color:#222}}table{{border-collapse:collapse;margin:16px 0;width:100%}}th,td{{border:1px solid #ddd;padding:8px;text-align:left}}th{{background:#f4f4f4}}</style>
</head><body><h1>DeepAnalyze evaluation: {html.escape(summary.suite_id)}</h1>
<p>{summary.passed_cases}/{summary.case_count} cases passed; critical failures: {summary.critical_failures}.</p>
<h2>Category scores</h2><table><tr><th>Category</th><th>Score</th><th>Failures</th></tr>{category_rows}</table>
<h2>Cases</h2><table><tr><th>Case</th><th>Status</th><th>Failures</th></tr>{case_rows}</table>
</body></html>"""
    html_path.write_text(document, encoding="utf-8")
    return json_path, html_path
