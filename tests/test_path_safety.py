"""Unit tests for the unified safe path validation module (WP5)."""

import pytest

from langgraph_langchain.execution.path_safety import (
    PathSafetyError,
    is_safe_published_filename,
    safe_workspace_join,
    validate_relative_path,
)


class TestValidateRelativePath:
    def test_accepts_simple_paths(self):
        assert validate_relative_path("out.csv") == "out.csv"
        assert validate_relative_path("nested/dir/out.csv") == "nested/dir/out.csv"

    def test_rejects_absolute(self):
        with pytest.raises(PathSafetyError):
            validate_relative_path("/etc/passwd")

    def test_rejects_traversal(self):
        for candidate in ("../x", "a/../b", "..", "a/..", "../../etc/passwd"):
            with pytest.raises(PathSafetyError):
                validate_relative_path(candidate)

    def test_rejects_empty_and_dot_segments(self):
        for candidate in ("", "a//b", "./a", "a/"):
            with pytest.raises(PathSafetyError):
                validate_relative_path(candidate)

    def test_rejects_null_byte(self):
        with pytest.raises(PathSafetyError):
            validate_relative_path("a\x00b")

    def test_rejects_control_characters(self):
        with pytest.raises(PathSafetyError):
            validate_relative_path("a\nb")

    def test_rejects_non_nfc_unicode(self):
        with pytest.raises(PathSafetyError):
            validate_relative_path("café".replace("é", "e\u0301"))

    def test_rejects_windows_absolute(self):
        with pytest.raises(PathSafetyError):
            validate_relative_path("C:\\data\\x.csv")

    def test_rejects_overlong(self):
        with pytest.raises(PathSafetyError):
            validate_relative_path("a" * 600)


class TestSafeWorkspaceJoin:
    def test_join_within_root(self, tmp_path):
        (tmp_path / "sub").mkdir()
        result = safe_workspace_join(tmp_path, "sub/file.txt")
        assert result == tmp_path / "sub" / "file.txt"

    def test_rejects_existing_symlink_component(self, tmp_path):
        outside = tmp_path.parent / "outside-target.txt"
        outside.write_text("secret")
        link = tmp_path / "link"
        link.symlink_to(outside)
        with pytest.raises(PathSafetyError):
            safe_workspace_join(tmp_path, "link/file.txt")

    def test_rejects_root_symlink(self, tmp_path):
        link_root = tmp_path.parent / "link-root"
        if link_root.exists():
            link_root.unlink()
        link_root.symlink_to(tmp_path)
        try:
            with pytest.raises(PathSafetyError):
                safe_workspace_join(link_root, "x.txt")
        finally:
            link_root.unlink()


class TestPublishedFilename:
    def test_simple_ok(self):
        assert is_safe_published_filename("result.csv")

    def test_nested_rejected_for_publish(self):
        # publish target must be a flat, job-scoped filename
        assert not is_safe_published_filename("nested/result.csv")

    def test_traversal_rejected(self):
        assert not is_safe_published_filename("../evil")
