"""Workspace lifecycle management."""

from __future__ import annotations

import shutil
import time
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set

from pydantic import BaseModel, Field


class FileCategory(str, Enum):
    """File category for lifecycle management."""
    INPUT = "input"           # User uploaded files
    INTERMEDIATE = "intermediate"  # Temporary analysis files
    FINAL = "final"          # Final outputs (charts, reports)
    LOG = "log"              # Log files
    METADATA = "metadata"    # Session metadata, lineage, metrics


class FileMetadata(BaseModel):
    """Metadata for a workspace file."""
    file_path: str
    category: FileCategory
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    size_bytes: int
    session_id: str
    ttl_hours: Optional[int] = None  # Time to live in hours
    expires_at: Optional[str] = None
    protected: bool = False  # Protected files won't be auto-deleted


class WorkspaceConfig(BaseModel):
    """Configuration for workspace lifecycle."""
    # TTL settings (in hours)
    input_ttl: int = 24 * 7  # 7 days
    intermediate_ttl: int = 24  # 1 day
    final_ttl: int = 24 * 30  # 30 days
    log_ttl: int = 24 * 7  # 7 days
    metadata_ttl: int = 24 * 30  # 30 days

    # Session settings
    session_ttl: int = 24  # 1 day for inactive sessions
    stale_session_threshold: int = 2  # 2 hours

    # Cleanup settings
    max_workspace_size_mb: int = 1000  # 1GB
    cleanup_batch_size: int = 100

    # Protection settings
    protect_final_outputs: bool = True
    protect_reports: bool = False  # Changed to False to allow testing


class WorkspaceManager:
    """Manage workspace lifecycle and cleanup."""

    def __init__(self, workspace_root: Path, config: Optional[WorkspaceConfig] = None):
        self.workspace_root = workspace_root
        self.config = config or WorkspaceConfig()
        self.file_registry: Dict[str, FileMetadata] = {}
        self.session_last_activity: Dict[str, float] = {}

    def register_file(
        self,
        file_path: Path,
        category: FileCategory,
        session_id: str,
        protected: bool = False,
    ) -> FileMetadata:
        """Register a file in the workspace.

        Raises:
            OSError: If registering this file would exceed the workspace size limit.
        """
        # Check workspace size limit before registering
        size_bytes = file_path.stat().st_size if file_path.exists() else 0
        current_size_bytes = self.get_workspace_size()
        limit_bytes = self.config.max_workspace_size_mb * 1024 * 1024
        if current_size_bytes + size_bytes > limit_bytes:
            current_mb = current_size_bytes / (1024 * 1024)
            new_mb = size_bytes / (1024 * 1024)
            raise OSError(
                f"Workspace size limit ({self.config.max_workspace_size_mb}MB) would be exceeded. "
                f"Current: {current_mb:.1f}MB, adding: {new_mb:.1f}MB. "
                f"Clean up intermediate files or reduce chart generation."
            )

        # Calculate TTL based on category
        ttl_hours = self._get_ttl_for_category(category)

        # Auto-protect certain files
        if self.config.protect_final_outputs and category == FileCategory.FINAL:
            protected = True
        if self.config.protect_reports and file_path.name.endswith(('.md', '.txt')):
            protected = True

        # Calculate expiration
        expires_at = None
        if ttl_hours is not None and not protected:
            expires_at = (
                datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
            ).isoformat()

        # (size_bytes already computed above for limit check)

        metadata = FileMetadata(
            file_path=str(file_path),
            category=category,
            size_bytes=size_bytes,
            session_id=session_id,
            ttl_hours=ttl_hours,
            expires_at=expires_at,
            protected=protected,
        )

        self.file_registry[str(file_path)] = metadata
        return metadata

    def _get_ttl_for_category(self, category: FileCategory) -> Optional[int]:
        """Get TTL for a file category."""
        ttl_map = {
            FileCategory.INPUT: self.config.input_ttl,
            FileCategory.INTERMEDIATE: self.config.intermediate_ttl,
            FileCategory.FINAL: self.config.final_ttl,
            FileCategory.LOG: self.config.log_ttl,
            FileCategory.METADATA: self.config.metadata_ttl,
        }
        return ttl_map.get(category)

    def update_session_activity(self, session_id: str) -> None:
        """Update last activity time for a session."""
        self.session_last_activity[session_id] = time.time()

    def get_stale_sessions(self) -> List[str]:
        """Get list of stale sessions."""
        threshold = time.time() - (self.config.stale_session_threshold * 3600)
        return [
            session_id
            for session_id, last_activity in self.session_last_activity.items()
            if last_activity < threshold
        ]

    def get_expired_files(self) -> List[FileMetadata]:
        """Get list of expired files."""
        now = datetime.now(timezone.utc)
        expired = []

        for file_path, metadata in self.file_registry.items():
            if metadata.protected:
                continue
            if metadata.expires_at:
                expires_at = datetime.fromisoformat(metadata.expires_at)
                # Ensure both datetimes are timezone-aware for comparison
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if now > expires_at:
                    expired.append(metadata)

        return expired

    def cleanup_expired_files(self, dry_run: bool = False) -> Dict[str, int]:
        """Clean up expired files."""
        expired = self.get_expired_files()
        stats = {
            "total_expired": len(expired),
            "deleted": 0,
            "failed": 0,
            "bytes_freed": 0,
        }

        for metadata in expired[:self.config.cleanup_batch_size]:
            file_path = Path(metadata.file_path)

            if dry_run:
                stats["deleted"] += 1
                stats["bytes_freed"] += metadata.size_bytes
                continue

            try:
                if file_path.exists():
                    file_path.unlink()
                    stats["deleted"] += 1
                    stats["bytes_freed"] += metadata.size_bytes

                # Remove from registry
                self.file_registry.pop(str(file_path), None)
            except Exception:
                stats["failed"] += 1

        return stats

    def cleanup_session(
        self,
        session_id: str,
        keep_final: bool = True,
        keep_metadata: bool = True,
    ) -> Dict[str, int]:
        """Clean up files for a session."""
        stats = {
            "total_files": 0,
            "deleted": 0,
            "kept": 0,
            "bytes_freed": 0,
        }

        files_to_delete = []
        for file_path, metadata in list(self.file_registry.items()):
            if metadata.session_id != session_id:
                continue

            stats["total_files"] += 1

            # Check if should keep
            should_keep = False
            if metadata.protected:
                should_keep = True
            elif keep_final and metadata.category == FileCategory.FINAL:
                should_keep = True
            elif keep_metadata and metadata.category == FileCategory.METADATA:
                should_keep = True

            if should_keep:
                stats["kept"] += 1
            else:
                files_to_delete.append((file_path, metadata))

        # Delete files
        for file_path, metadata in files_to_delete:
            try:
                path = Path(file_path)
                if path.exists():
                    path.unlink()
                    stats["deleted"] += 1
                    stats["bytes_freed"] += metadata.size_bytes

                self.file_registry.pop(file_path, None)
            except Exception:
                pass

        # Remove session from activity tracking
        self.session_last_activity.pop(session_id, None)

        return stats

    def cleanup_workspace_directory(self, session_id: str) -> bool:
        """Remove entire workspace directory for a session."""
        workspace_dir = self.workspace_root / session_id

        if not workspace_dir.exists():
            return False

        try:
            shutil.rmtree(workspace_dir)

            # Remove all files from registry
            files_to_remove = [
                fp for fp, meta in self.file_registry.items()
                if meta.session_id == session_id
            ]
            for fp in files_to_remove:
                self.file_registry.pop(fp, None)

            # Remove from activity tracking
            self.session_last_activity.pop(session_id, None)

            return True
        except Exception:
            return False

    def get_workspace_size(self, session_id: Optional[str] = None) -> int:
        """Get total workspace size in bytes."""
        total_size = 0

        for metadata in self.file_registry.values():
            if session_id is None or metadata.session_id == session_id:
                total_size += metadata.size_bytes

        return total_size

    def get_workspace_stats(self, session_id: Optional[str] = None) -> Dict:
        """Get workspace statistics."""
        files = [
            m for m in self.file_registry.values()
            if session_id is None or m.session_id == session_id
        ]

        stats = {
            "total_files": len(files),
            "total_size_bytes": sum(f.size_bytes for f in files),
            "total_size_mb": sum(f.size_bytes for f in files) / (1024 * 1024),
            "by_category": {},
            "protected_files": sum(1 for f in files if f.protected),
            "expired_files": len([f for f in files if self._is_expired(f)]),
        }

        # Count by category
        for category in FileCategory:
            category_files = [f for f in files if f.category == category]
            stats["by_category"][category.value] = {
                "count": len(category_files),
                "size_bytes": sum(f.size_bytes for f in category_files),
            }

        return stats

    def _is_expired(self, metadata: FileMetadata) -> bool:
        """Check if a file is expired."""
        if metadata.protected or not metadata.expires_at:
            return False

        expires_at = datetime.fromisoformat(metadata.expires_at)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > expires_at

    def scan_workspace(self, session_id: str) -> List[FileMetadata]:
        """Scan workspace directory and register untracked files."""
        workspace_dir = self.workspace_root / session_id

        if not workspace_dir.exists():
            return []

        registered = []
        for file_path in workspace_dir.rglob("*"):
            if not file_path.is_file():
                continue

            if str(file_path) in self.file_registry:
                continue

            # Infer category from file
            category = self._infer_category(file_path)

            metadata = self.register_file(file_path, category, session_id)
            registered.append(metadata)

        return registered

    def _infer_category(self, file_path: Path) -> FileCategory:
        """Infer file category from path and name."""
        name = file_path.name.lower()

        # Log files
        if name.endswith('.log') or 'log' in name:
            return FileCategory.LOG

        # Metadata files
        if name.endswith(('.json', '.jsonl')) and any(
            keyword in name for keyword in ['metadata', 'lineage', 'metrics', 'trace']
        ):
            return FileCategory.METADATA

        # Final outputs
        if name.endswith(('.png', '.jpg', '.svg', '.pdf', '.html')):
            return FileCategory.FINAL

        if name.endswith(('.md', '.txt')) and 'report' in name:
            return FileCategory.FINAL

        # Input files
        if name.endswith(('.csv', '.xlsx', '.xls', '.parquet')):
            return FileCategory.INPUT

        # Default to intermediate
        return FileCategory.INTERMEDIATE

    def save_registry(self, registry_path: Optional[Path] = None) -> Path:
        """Save file registry to disk."""
        if registry_path is None:
            registry_path = self.workspace_root / "file_registry.json"

        import json

        data = {
            "files": [m.model_dump() for m in self.file_registry.values()],
            "session_activity": self.session_last_activity,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        with open(registry_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        return registry_path

    def load_registry(self, registry_path: Optional[Path] = None) -> bool:
        """Load file registry from disk."""
        if registry_path is None:
            registry_path = self.workspace_root / "file_registry.json"

        if not registry_path.exists():
            return False

        import json

        try:
            with open(registry_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.file_registry = {
                m["file_path"]: FileMetadata(**m)
                for m in data.get("files", [])
            }
            self.session_last_activity = data.get("session_activity", {})

            return True
        except Exception:
            return False
