"""Product worker job construction and safe state transport (R0a-1B).

The portable ``WorkerJob`` contract keeps the namespace JSON-safe so a
container backend can transport it. Product analysis state (pandas
DataFrames, numpy arrays) cannot cross that contract as runtime objects, so
this module implements the explicit artifact-based state mechanism that
``ProcessWorkerJob`` documentation requires:

- DataFrames are serialized to Parquet (data-only format; no code execution
  on restore).
- numpy arrays are serialized to ``.npy`` (``allow_pickle=False``).
- JSON-safe scalars travel in ``WorkerJob.namespace`` (input) and
  ``state__json.json`` (output).
- Everything else (functions, modules, arbitrary objects) is not transported.

Security notes:
- Restoring state uses only data-only decoders (Parquet / ``np.load`` with
  ``allow_pickle=False`` / ``json.loads``).  Worker-provided pickle is never
  unpickled on the host: untrusted container code could otherwise achieve
  host-side code execution through a crafted state file.
- State artifacts use the reserved ``state__`` name prefix; they are
  excluded from user-facing artifacts and from session workspace copies.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

from langgraph_langchain.execution.job_contract import (
    ArtifactReference,
    WorkerExecutionOptions,
    WorkerJob,
)

STATE_PREFIX = "state__"
STATE_DIR_NAME = ".analysis_state"
DF_STATE_PATTERN = re.compile(r"^state__df__([A-Za-z0-9_]{1,64})\.parquet$")
NP_STATE_PATTERN = re.compile(r"^state__np__([A-Za-z0-9_]{1,64})\.npy$")
JSON_STATE_NAME = "state__json.json"
VAR_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

# Worker-provided helpers / modules that must never be treated as analysis
# state.  ``df`` is intentionally NOT in this set: it is state.
_RESERVED_WORKER_NAMES = frozenset(
    {
        "pd",
        "np",
        "Path",
        "save_fig",
        "load_csv",
        "safe_workspace_path",
        "fix_chinese",
        "WORKSPACE_DIR",
        "SOURCE_PATH",
    }
)

_MISSING = object()


class ProductStateError(RuntimeError):
    """State preparation or restoration failed; execution must fail closed."""


@dataclass
class PreparedProductJob:
    """A portable WorkerJob plus its host-side staging bookkeeping."""

    job: WorkerJob
    staged_paths: list[Path] = field(default_factory=list)
    staged_names: list[str] = field(default_factory=list)

    def cleanup_staging(self) -> None:
        """Remove staged state files from the session workspace (idempotent)."""
        for path in self.staged_paths:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass


def _is_transportable_json(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, list):
        return all(_is_transportable_json(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_transportable_json(item)
            for key, item in value.items()
        )
    return False


def _json_safe(value: Any) -> Any:
    """Convert numpy scalars where safe; return _MISSING when unsupported."""
    module = type(value).__module__
    if module.startswith("numpy") and hasattr(value, "item"):
        try:
            converted = value.item()
        except (ValueError, TypeError):
            return _MISSING
        return converted if _is_transportable_json(converted) else _MISSING
    return value if _is_transportable_json(value) else _MISSING


def _is_state_candidate_name(name: str) -> bool:
    return bool(VAR_NAME_PATTERN.match(name)) and name not in _RESERVED_WORKER_NAMES


def _sha256_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _stage_dataframe(value: Any, path: Path) -> None:
    import pandas as pd

    if not isinstance(value, pd.DataFrame):
        raise ProductStateError("expected a pandas DataFrame for parquet state")
    value.to_parquet(path)


def _stage_ndarray(value: Any, path: Path) -> None:
    import numpy as np

    if not isinstance(value, np.ndarray):
        raise ProductStateError("expected a numpy ndarray for npy state")
    with path.open("wb") as handle:
        np.save(handle, value, allow_pickle=False)


def build_product_worker_job(
    *,
    code: str,
    workspace_dir: str,
    source_path: str = "",
    namespace: Optional[dict[str, Any]] = None,
    timeout_seconds: float = 60.0,
    max_output_chars: int = 3000,
    max_artifact_bytes: int = 100 * 1024 * 1024,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
    execution_id: Optional[str] = None,
) -> PreparedProductJob:
    """Build a portable WorkerJob carrying product analysis state safely.

    Raises ``ProductStateError`` when the analysis state cannot be prepared;
    callers must treat that as a hard failure (fail closed), never as a
    reason to fall back to another backend.
    """
    import pandas as pd

    workspace = Path(workspace_dir).resolve()
    if not workspace.is_dir():
        raise ProductStateError(f"session workspace does not exist: {workspace}")
    state_dir = workspace / STATE_DIR_NAME
    state_dir.mkdir(exist_ok=True)

    json_namespace: dict[str, Any] = {}
    staged: list[tuple[str, Path]] = []
    for name, value in dict(namespace or {}).items():
        if name.startswith("__") or name in _RESERVED_WORKER_NAMES:
            continue
        if isinstance(value, ModuleType):
            continue
        if callable(value) or isinstance(value, type):
            continue
        if not _is_state_candidate_name(name):
            continue
        if isinstance(value, pd.DataFrame):
            filename = f"{STATE_PREFIX}df__{name}.parquet"
            target = state_dir / filename
            try:
                _stage_dataframe(value, target)
            except Exception as exc:
                raise ProductStateError(
                    f"cannot stage DataFrame state for {name!r}: {exc}"
                ) from exc
            staged.append((filename, target))
        else:
            module = type(value).__module__
            if module.startswith("numpy") and hasattr(value, "item"):
                pass  # handled below via ndarray/scalar split
            try:
                import numpy as np

                is_ndarray = isinstance(value, np.ndarray)
            except Exception:
                is_ndarray = False
            if is_ndarray:
                filename = f"{STATE_PREFIX}np__{name}.npy"
                target = state_dir / filename
                try:
                    _stage_ndarray(value, target)
                except Exception as exc:
                    raise ProductStateError(
                        f"cannot stage ndarray state for {name!r}: {exc}"
                    ) from exc
                staged.append((filename, target))
                continue
            converted = _json_safe(value)
            if converted is not _MISSING:
                json_namespace[name] = converted

    input_artifacts: list[ArtifactReference] = []
    if source_path:
        source = Path(source_path).resolve()
        try:
            relative = os.path.relpath(source, workspace)
        except ValueError as exc:
            raise ProductStateError(
                f"source path is outside the session workspace: {source}"
            ) from exc
        if re.fullmatch(r"[A-Za-z0-9_.\-]{1,255}", relative) and not relative.startswith(
            "."
        ) and source.is_file():
            size, digest = _sha256_file(source)
            input_artifacts.append(
                ArtifactReference(
                    artifact_id=f"input:{relative}",
                    artifact_type=source.suffix.lstrip(".").lower() or "file",
                    relative_path=relative,
                    size_bytes=size,
                    sha256=digest,
                )
            )

    for filename, path in staged:
        size, digest = _sha256_file(path)
        input_artifacts.append(
            ArtifactReference(
                artifact_id=f"state:{filename}",
                artifact_type="state",
                relative_path=filename,
                size_bytes=size,
                sha256=digest,
            )
        )

    job = WorkerJob(
        session_id=session_id,
        task_id=task_id,
        execution_id=execution_id,
        correlation_id=f"prod_{uuid.uuid4().hex[:12]}",
        code=code,
        workspace_dir=str(workspace),
        source_path=str(Path(source_path).resolve()) if source_path else "",
        namespace=json_namespace,
        input_artifacts=tuple(input_artifacts),
        execution_options=WorkerExecutionOptions(
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
            max_artifact_bytes=max_artifact_bytes,
            max_artifacts=100,
        ),
    )
    return PreparedProductJob(
        job=job,
        staged_paths=[path for _, path in staged],
        staged_names=[filename for filename, _ in staged],
    )


def restore_product_namespace_state(
    namespace: dict[str, Any],
    state_files: dict[str, bytes],
) -> list[str]:
    """Restore worker state into a host namespace using data-only decoders.

    Raises ``ProductStateError`` on any unexpected state artifact or decode
    failure.  Callers must fail the execution result instead of continuing
    with a partially restored namespace.
    """
    import pandas as pd

    restored: list[str] = []
    json_updates: Optional[dict[str, Any]] = None
    for filename in sorted(state_files):
        content = state_files[filename]
        if filename == JSON_STATE_NAME:
            try:
                payload = json.loads(content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProductStateError(
                    f"cannot decode JSON state artifact: {exc}"
                ) from exc
            if not isinstance(payload, dict):
                raise ProductStateError("JSON state artifact must be an object")
            json_updates = payload
            continue
        df_match = DF_STATE_PATTERN.match(filename)
        if df_match is not None:
            try:
                namespace[df_match.group(1)] = pd.read_parquet(io.BytesIO(content))
            except Exception as exc:
                raise ProductStateError(
                    f"cannot restore DataFrame state {filename!r}: {exc}"
                ) from exc
            restored.append(df_match.group(1))
            continue
        np_match = NP_STATE_PATTERN.match(filename)
        if np_match is not None:
            import numpy as np

            try:
                namespace[np_match.group(1)] = np.load(
                    io.BytesIO(content), allow_pickle=False
                )
            except Exception as exc:
                raise ProductStateError(
                    f"cannot restore ndarray state {filename!r}: {exc}"
                ) from exc
            restored.append(np_match.group(1))
            continue
        raise ProductStateError(f"unrecognized state artifact: {filename!r}")
    if json_updates is not None:
        for name, value in json_updates.items():
            if not isinstance(name, str) or not _is_state_candidate_name(name):
                raise ProductStateError(f"invalid JSON state variable name: {name!r}")
            if not _is_transportable_json(value):
                raise ProductStateError(
                    f"JSON state variable {name!r} is not JSON-safe"
                )
            namespace[name] = value
            restored.append(name)
    return restored


def split_result_artifacts(artifact_paths: list[str]) -> tuple[list[str], list[str]]:
    """Split worker-declared artifact names into user vs internal state."""
    user: list[str] = []
    state: list[str] = []
    for name in artifact_paths:
        (state if name.startswith(STATE_PREFIX) else user).append(name)
    return user, state
