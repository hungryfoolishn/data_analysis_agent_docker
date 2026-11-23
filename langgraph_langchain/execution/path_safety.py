"""Unified safe path validation for the Docker worker filesystem boundary.

All host-side and worker-side path decisions must go through this module.
Rules (fail closed):

- Reject absolute paths, traversal segments, empty/dot segments, null bytes,
  control characters, mixed separators, over-long paths/segments, and
  non-NFC unicode tricks.
- `safe_workspace_join` resolves against a real directory root and verifies
  containment using resolved filesystem semantics (not string prefixes), and
  refuses to traverse through symlinks for existing components.
"""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path

MAX_RELATIVE_PATH_LENGTH = 512
MAX_SEGMENT_LENGTH = 255
RESERVED_SEGMENT_NAMES = {".", ".."}


class PathSafetyError(ValueError):
    """Raised when a path fails the filesystem safety policy."""


def validate_relative_path(value: str, *, field_name: str = "path") -> str:
    """Validate an untrusted relative path; returns the normalized value."""
    if not isinstance(value, str) or not value:
        raise PathSafetyError(f"{field_name} must be a non-empty string")
    if "\x00" in value:
        raise PathSafetyError(f"{field_name} contains a null byte")
    if len(value) > MAX_RELATIVE_PATH_LENGTH:
        raise PathSafetyError(f"{field_name} exceeds {MAX_RELATIVE_PATH_LENGTH} characters")
    if value.startswith("/") or value.startswith("\\"):
        raise PathSafetyError(f"{field_name} must be relative")
    if len(value) >= 2 and value[1] == ":":
        raise PathSafetyError(f"{field_name} must not be a Windows absolute path")
    normalized = unicodedata.normalize("NFC", value)
    if normalized != value:
        raise PathSafetyError(f"{field_name} must be NFC-normalized unicode")
    parts = normalized.replace("\\", "/").split("/")
    for part in parts:
        if part in RESERVED_SEGMENT_NAMES or part == "":
            raise PathSafetyError(f"{field_name} must be a normalized relative path")
        if len(part) > MAX_SEGMENT_LENGTH:
            raise PathSafetyError(f"{field_name} segment exceeds {MAX_SEGMENT_LENGTH} characters")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in part):
            raise PathSafetyError(f"{field_name} contains control characters")
    return normalized


def safe_workspace_join(root: Path, relative: str) -> Path:
    """Join and resolve `relative` under `root`, verifying real containment.

    Refuses to follow symlinks in any existing path component between root and
    the final target (the final component may not exist for output writes).
    """
    normalized = validate_relative_path(relative)
    if root.is_symlink():  # lstat: refuses symlinked roots BEFORE any resolution
        raise PathSafetyError("workspace root must not be a symlink")
    root_resolved = root.resolve(strict=True)
    current = root_resolved
    for part in normalized.split("/"):
        candidate = current / part
        if candidate.exists() or candidate.is_symlink():
            if candidate.is_symlink():
                raise PathSafetyError(f"path traverses a symlink component: {part}")
            if not candidate.is_dir():
                candidate_resolved = candidate.resolve(strict=True)
            else:
                candidate_resolved = candidate.resolve(strict=True)
            try:
                candidate_resolved.relative_to(root_resolved)
            except ValueError as exc:
                raise PathSafetyError("resolved path escapes the workspace root") from exc
        current = candidate
    return current


def is_safe_published_filename(name: str) -> bool:
    try:
        validate_relative_path(name, field_name="artifact filename")
    except PathSafetyError:
        return False
    return "/" not in name


def ensure_directory_owner(path: Path, uid: int, gid: int, mode: int) -> None:
    """Create (if missing) and enforce ownership + mode on a manager directory."""
    path.mkdir(parents=True, exist_ok=True)
    os.chown(path, uid, gid)
    os.chmod(path, mode)
