"""Tests for workspace lifecycle management."""

import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from langgraph_langchain.workspace_manager import (
    FileCategory,
    FileMetadata,
    WorkspaceConfig,
    WorkspaceManager,
)


class TestWorkspaceManager:
    """Test workspace lifecycle management."""

    def test_manager_initialization(self):
        """Test manager initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            manager = WorkspaceManager(workspace)

            assert manager.workspace_root == workspace
            assert manager.config is not None
            assert len(manager.file_registry) == 0

    def test_register_file(self):
        """Test file registration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            manager = WorkspaceManager(workspace)

            # Create a test file
            test_file = workspace / "test.csv"
            test_file.write_text("data")

            # Register file
            metadata = manager.register_file(
                test_file,
                FileCategory.INPUT,
                "session_123"
            )

            assert metadata.file_path == str(test_file)
            assert metadata.category == FileCategory.INPUT
            assert metadata.session_id == "session_123"
            assert metadata.size_bytes == 4
            assert metadata.ttl_hours == manager.config.input_ttl
            assert metadata.expires_at is not None

    def test_protected_files(self):
        """Test protected files don't expire."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            manager = WorkspaceManager(workspace)

            test_file = workspace / "test.png"
            test_file.write_text("image")

            # Register protected file
            metadata = manager.register_file(
                test_file,
                FileCategory.FINAL,
                "session_123",
                protected=True
            )

            assert metadata.protected is True
            assert metadata.expires_at is None  # Protected files don't expire

    def test_auto_protect_final_outputs(self):
        """Test auto-protection of final outputs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = WorkspaceConfig(protect_final_outputs=True)
            manager = WorkspaceManager(workspace, config)

            test_file = workspace / "chart.png"
            test_file.write_text("chart")

            metadata = manager.register_file(
                test_file,
                FileCategory.FINAL,
                "session_123"
            )

            assert metadata.protected is True

    def test_session_activity_tracking(self):
        """Test session activity tracking."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            manager = WorkspaceManager(workspace)

            manager.update_session_activity("session_123")
            assert "session_123" in manager.session_last_activity

            # Check timestamp is recent
            last_activity = manager.session_last_activity["session_123"]
            assert time.time() - last_activity < 1.0

    def test_get_stale_sessions(self):
        """Test stale session detection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = WorkspaceConfig(stale_session_threshold=1)  # 1 hour
            manager = WorkspaceManager(workspace, config)

            # Add recent session
            manager.session_last_activity["session_recent"] = time.time()

            # Add stale session (2 hours ago)
            manager.session_last_activity["session_stale"] = time.time() - (2 * 3600)

            stale = manager.get_stale_sessions()
            assert "session_stale" in stale
            assert "session_recent" not in stale

    def test_get_expired_files(self):
        """Test expired file detection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            manager = WorkspaceManager(workspace)

            # Create files
            file1 = workspace / "file1.txt"
            file2 = workspace / "file2.txt"
            file1.write_text("data1")
            file2.write_text("data2")

            # Register files
            manager.register_file(file1, FileCategory.INTERMEDIATE, "session_123")
            manager.register_file(file2, FileCategory.INTERMEDIATE, "session_123")

            # Manually set expiration times
            manager.file_registry[str(file1)].expires_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            manager.file_registry[str(file2)].expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

            expired = manager.get_expired_files()
            assert len(expired) == 1
            assert expired[0].file_path == str(file1)

    def test_cleanup_expired_files(self):
        """Test expired file cleanup."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            manager = WorkspaceManager(workspace)

            # Create expired file
            expired_file = workspace / "expired.txt"
            expired_file.write_text("data")

            manager.register_file(expired_file, FileCategory.INTERMEDIATE, "session_123")

            # Set expiration to past
            manager.file_registry[str(expired_file)].expires_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

            # Cleanup
            stats = manager.cleanup_expired_files()

            assert stats["total_expired"] == 1
            assert stats["deleted"] == 1
            assert not expired_file.exists()
            assert str(expired_file) not in manager.file_registry

    def test_cleanup_expired_files_dry_run(self):
        """Test dry run mode for cleanup."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            manager = WorkspaceManager(workspace)

            expired_file = workspace / "expired.txt"
            expired_file.write_text("data")

            manager.register_file(expired_file, FileCategory.INTERMEDIATE, "session_123")

            # Set expiration to past
            manager.file_registry[str(expired_file)].expires_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

            # Dry run
            stats = manager.cleanup_expired_files(dry_run=True)

            assert stats["deleted"] == 1
            assert expired_file.exists()  # File still exists
            assert str(expired_file) in manager.file_registry  # Still in registry

    def test_cleanup_session(self):
        """Test session cleanup."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            manager = WorkspaceManager(workspace)

            # Create files for session
            input_file = workspace / "input.csv"
            intermediate_file = workspace / "temp.txt"
            final_file = workspace / "chart.png"

            for f in [input_file, intermediate_file, final_file]:
                f.write_text("data")

            manager.register_file(input_file, FileCategory.INPUT, "session_123")
            manager.register_file(intermediate_file, FileCategory.INTERMEDIATE, "session_123")
            manager.register_file(final_file, FileCategory.FINAL, "session_123")

            # Cleanup session (keep final)
            stats = manager.cleanup_session("session_123", keep_final=True)

            assert stats["total_files"] == 3
            assert stats["deleted"] == 2  # input + intermediate
            assert stats["kept"] == 1  # final
            assert not input_file.exists()
            assert not intermediate_file.exists()
            assert final_file.exists()

    def test_cleanup_workspace_directory(self):
        """Test complete workspace directory cleanup."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            manager = WorkspaceManager(workspace)

            # Create session directory
            session_dir = workspace / "session_123"
            session_dir.mkdir()
            (session_dir / "file1.txt").write_text("data1")
            (session_dir / "file2.txt").write_text("data2")

            # Register files
            manager.register_file(session_dir / "file1.txt", FileCategory.INPUT, "session_123")
            manager.register_file(session_dir / "file2.txt", FileCategory.INTERMEDIATE, "session_123")

            # Cleanup
            result = manager.cleanup_workspace_directory("session_123")

            assert result is True
            assert not session_dir.exists()
            assert len([f for f in manager.file_registry.values() if f.session_id == "session_123"]) == 0

    def test_get_workspace_size(self):
        """Test workspace size calculation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            manager = WorkspaceManager(workspace)

            # Create files
            file1 = workspace / "file1.txt"
            file2 = workspace / "file2.txt"
            file1.write_text("a" * 100)
            file2.write_text("b" * 200)

            manager.register_file(file1, FileCategory.INPUT, "session_123")
            manager.register_file(file2, FileCategory.INPUT, "session_456")

            # Total size
            total_size = manager.get_workspace_size()
            assert total_size == 300

            # Session-specific size
            session_size = manager.get_workspace_size("session_123")
            assert session_size == 100

    def test_get_workspace_stats(self):
        """Test workspace statistics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            manager = WorkspaceManager(workspace)

            # Create files
            for i in range(3):
                f = workspace / f"chart_{i}.png"
                f.write_text("x" * 100)
                manager.register_file(f, FileCategory.FINAL, "session_123")

            for i in range(2):
                f = workspace / f"temp_{i}.txt"
                f.write_text("y" * 50)
                manager.register_file(f, FileCategory.INTERMEDIATE, "session_123")

            stats = manager.get_workspace_stats("session_123")

            assert stats["total_files"] == 5
            assert stats["total_size_bytes"] == 400
            assert stats["by_category"][FileCategory.FINAL.value]["count"] == 3
            assert stats["by_category"][FileCategory.INTERMEDIATE.value]["count"] == 2

    def test_scan_workspace(self):
        """Test workspace scanning for untracked files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            manager = WorkspaceManager(workspace)

            # Create session directory with files
            session_dir = workspace / "session_123"
            session_dir.mkdir()
            (session_dir / "data.csv").write_text("data")
            (session_dir / "chart.png").write_text("chart")
            (session_dir / "temp.txt").write_text("temp")

            # Scan
            registered = manager.scan_workspace("session_123")

            assert len(registered) == 3
            assert len(manager.file_registry) == 3

    def test_infer_category(self):
        """Test file category inference."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            manager = WorkspaceManager(workspace)

            # Test various file types
            assert manager._infer_category(Path("data.csv")) == FileCategory.INPUT
            assert manager._infer_category(Path("chart.png")) == FileCategory.FINAL
            assert manager._infer_category(Path("report.md")) == FileCategory.FINAL
            assert manager._infer_category(Path("agent.log")) == FileCategory.LOG
            assert manager._infer_category(Path("lineage.json")) == FileCategory.METADATA
            assert manager._infer_category(Path("temp.txt")) == FileCategory.INTERMEDIATE

    def test_save_and_load_registry(self):
        """Test registry persistence."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            manager = WorkspaceManager(workspace)

            # Register some files
            file1 = workspace / "file1.txt"
            file1.write_text("data")
            manager.register_file(file1, FileCategory.INPUT, "session_123")
            manager.update_session_activity("session_123")

            # Save
            registry_path = manager.save_registry()
            assert registry_path.exists()

            # Create new manager and load
            manager2 = WorkspaceManager(workspace)
            loaded = manager2.load_registry()

            assert loaded is True
            assert len(manager2.file_registry) == 1
            assert "session_123" in manager2.session_last_activity

    def test_custom_config(self):
        """Test custom workspace configuration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = WorkspaceConfig(
                input_ttl=48,
                intermediate_ttl=12,
                protect_final_outputs=False
            )
            manager = WorkspaceManager(workspace, config)

            assert manager.config.input_ttl == 48
            assert manager.config.intermediate_ttl == 12
            assert manager.config.protect_final_outputs is False
