"""Build portable, auditable analysis-run packages."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Optional

from langgraph_langchain.runtime.models import utc_now
from langgraph_langchain.runtime.report_rebuild import rebuild_report_markdown


PACKAGE_VERSION = "1.0"
_SUPPLEMENTAL_FILES = (
    "data_analysis_report.md",
    "final_report.md",
    "analysis_findings.json",
    "lineage_graph.json",
    "trace_analysis.txt",
    "trace_dashboard.json",
)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_workspace_file(path_value: str, workspace_root: Path) -> Optional[Path]:
    if not path_value:
        return None
    try:
        resolved = Path(path_value).resolve(strict=True)
        resolved.relative_to(workspace_root)
    except (OSError, RuntimeError, ValueError):
        return None
    if not resolved.is_file() or resolved.is_symlink():
        return None
    return resolved


def _archive_name(prefix: str, stable_id: str, source: Path) -> str:
    safe_id = "".join(char for char in stable_id if char.isalnum() or char in "_-")
    safe_name = source.name.replace("/", "_").replace("\\", "_")
    return f"{prefix}/{safe_id or 'item'}/{safe_name}"


def _replay_script(snapshot: dict[str, Any]) -> str:
    python_steps = [
        {
            "execution_id": item.get("execution_id"),
            "code": item.get("code_or_query"),
        }
        for item in snapshot.get("executions", [])
        if item.get("tool_name") == "python_repl"
        and item.get("status") == "succeeded"
        and item.get("code_or_query")
    ]
    serialized = json.dumps(python_steps, ensure_ascii=False)
    return f'''"""Replay successful Python analysis steps from this package.

Review the generated code before running it. Execute from the unpacked package
directory with the original project dependencies installed.
"""
import json
from pathlib import Path

import pandas as pd

PACKAGE_ROOT = Path(__file__).resolve().parent
WORKSPACE_DIR = str(PACKAGE_ROOT / "reproduced")
Path(WORKSPACE_DIR).mkdir(exist_ok=True)
SUPPORTED = {{".csv", ".xlsx", ".xls", ".parquet"}}
inputs = [path for path in (PACKAGE_ROOT / "inputs").rglob("*") if path.suffix.lower() in SUPPORTED]
if not inputs:
    raise FileNotFoundError("No replayable tabular input is present in inputs/")
SOURCE_PATH = str(inputs[0])
suffix = inputs[0].suffix.lower()
if suffix == ".csv":
    df = pd.read_csv(inputs[0])
elif suffix in {{".xlsx", ".xls"}}:
    df = pd.read_excel(inputs[0])
else:
    df = pd.read_parquet(inputs[0])

def save_fig(filename):
    import matplotlib.pyplot as plt
    target = Path(WORKSPACE_DIR) / Path(filename).name
    plt.savefig(target, bbox_inches="tight", dpi=150)
    plt.close()
    return str(target)

def fix_chinese():
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "Microsoft YaHei", "SimHei"]
    plt.rcParams["axes.unicode_minus"] = False

steps = json.loads({serialized!r})
namespace = {{
    "df": df,
    "pd": pd,
    "Path": Path,
    "SOURCE_PATH": SOURCE_PATH,
    "WORKSPACE_DIR": WORKSPACE_DIR,
    "save_fig": save_fig,
    "fix_chinese": fix_chinese,
}}
for index, step in enumerate(steps, start=1):
    print(f"[replay] {{index}}/{{len(steps)}} {{step['execution_id']}}")
    exec(step["code"], namespace, namespace)
'''


def build_analysis_package(
    *,
    snapshot: dict[str, Any],
    workspace_root: Path,
) -> Path:
    """Create an atomic ZIP package for one persisted analysis run."""
    workspace_root = workspace_root.resolve()
    task = snapshot.get("task") or {}
    run = snapshot.get("run") or {}
    run_id = str(run.get("run_id") or "")
    session_id = str(task.get("session_id") or "")
    if not run_id or not session_id:
        raise ValueError("Run snapshot is missing run_id or session_id")

    session_dir = (workspace_root / session_id).resolve()
    try:
        session_dir.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError("Run session is outside the workspace") from exc
    if not session_dir.is_dir():
        raise FileNotFoundError("Run workspace no longer exists")

    package_dir = session_dir / ".analysis_packages"
    package_dir.mkdir(parents=True, exist_ok=True)
    package_path = package_dir / f"{run_id}.zip"
    fd, temp_name = tempfile.mkstemp(dir=str(package_dir), prefix=f".{run_id}_", suffix=".tmp")
    os.close(fd)
    temp_path = Path(temp_name)

    manifest: dict[str, Any] = {
        "package_version": PACKAGE_VERSION,
        "created_at": utc_now(),
        "run_id": run_id,
        "session_id": session_id,
        "run_status": run.get("status"),
        "files": [],
        "missing_files": [],
    }
    added_sources: set[Path] = set()

    def add_file(archive: zipfile.ZipFile, source: Optional[Path], archive_path: str, role: str, ref_id: str) -> None:
        if source is None:
            manifest["missing_files"].append({"role": role, "ref_id": ref_id})
            return
        archive.write(source, archive_path)
        added_sources.add(source)
        manifest["files"].append({
            "role": role,
            "ref_id": ref_id,
            "archive_path": archive_path,
            "size_bytes": source.stat().st_size,
            "sha256": _hash_file(source),
        })

    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for asset in snapshot.get("assets", []):
                asset_id = str(asset.get("asset_id") or "asset")
                source = _safe_workspace_file(str(asset.get("location") or ""), workspace_root)
                archive_path = _archive_name("inputs", asset_id, source) if source else f"inputs/{asset_id}/missing"
                add_file(archive, source, archive_path, "input", asset_id)

            for artifact in snapshot.get("artifacts", []):
                artifact_id = str(artifact.get("artifact_id") or "artifact")
                source = _safe_workspace_file(str(artifact.get("path") or ""), workspace_root)
                archive_path = _archive_name("artifacts", artifact_id, source) if source else f"artifacts/{artifact_id}/missing"
                add_file(archive, source, archive_path, "artifact", artifact_id)

            for name in _SUPPLEMENTAL_FILES:
                source = _safe_workspace_file(str(session_dir / name), workspace_root)
                if source is not None and source not in added_sources:
                    add_file(archive, source, f"artifacts/supplemental/{name}", "supplemental", name)

            snapshot_json = json.dumps(snapshot, ensure_ascii=False, indent=2)
            archive.writestr("metadata/run_snapshot.json", snapshot_json)
            manifest["files"].append({
                "role": "metadata",
                "ref_id": run_id,
                "archive_path": "metadata/run_snapshot.json",
                "size_bytes": len(snapshot_json.encode("utf-8")),
                "sha256": hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest(),
            })
            replay = _replay_script(snapshot)
            archive.writestr("replay.py", replay)
            try:
                rebuilt_report = rebuild_report_markdown(snapshot)
            except ValueError as exc:
                manifest["missing_files"].append({
                    "role": "rebuilt_report",
                    "ref_id": run_id,
                    "reason": str(exc),
                })
            else:
                archive.writestr("artifacts/rebuilt_report.md", rebuilt_report)
                rebuilt_bytes = rebuilt_report.encode("utf-8")
                manifest["files"].append({
                    "role": "rebuilt_report",
                    "ref_id": run_id,
                    "archive_path": "artifacts/rebuilt_report.md",
                    "size_bytes": len(rebuilt_bytes),
                    "sha256": hashlib.sha256(rebuilt_bytes).hexdigest(),
                })
            readme = (
                "# DeepAnalyze analysis package\n\n"
                f"Run: `{run_id}`\n\n"
                "- `metadata/run_snapshot.json`: task, plan, executions, assets, and artifact lineage.\n"
                "- `inputs/`: source data captured for this run.\n"
                "- `artifacts/`: reports, charts, tables, findings, and lineage files.\n"
                "- `replay.py`: replays successful Python steps after manual review.\n"
                "- `artifacts/rebuilt_report.md`: deterministic report rebuilt without the original narrative.\n"
                "- `manifest.json`: file hashes and missing-file disclosures.\n"
            )
            archive.writestr("README.md", readme)
            for archive_path, role, content in (
                ("replay.py", "replay", replay),
                ("README.md", "documentation", readme),
            ):
                encoded = content.encode("utf-8")
                manifest["files"].append({
                    "role": role,
                    "ref_id": run_id,
                    "archive_path": archive_path,
                    "size_bytes": len(encoded),
                    "sha256": hashlib.sha256(encoded).hexdigest(),
                })
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        os.replace(temp_path, package_path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return package_path
