"""Dual-layer MemoryStore for the data analysis agent.

Adapted from hermes-agent's tools/memory_tool.py MemoryStore class.

Two memory layers:
  1. **Short-term (session)**: Per-session workspace memory.
     - analysis_notes.md — agent observations about data patterns, anomalies
     - session_context.md — user preferences, analysis scope, key decisions
     - Stored in: workspace/<session_id>/.agent_memory/
     - Lifecycle: created on session start, cleaned up with session

  2. **Long-term (global)**: Cross-session persistent memory.
     - USER.md — user profile: preferences, communication style, domain expertise
     - MEMORY.md — agent notes: common data patterns, analysis lessons, tool quirks
     - Stored in: <project_root>/.agent_memory/
     - Lifecycle: persists across sessions, accumulates knowledge

Key features (from hermes):
  - Entry delimiter: § (section sign)
  - Frozen snapshot at load time → injected into system prompt → never changes mid-session
  - fcntl.flock for concurrent access safety
  - Atomic writes via tempfile.mkstemp + os.replace
  - Content scanning for injection/exfiltration prevention
  - Actions: add, replace, remove
  - Character limits per store (configurable)
"""

from __future__ import annotations

import fcntl
import logging
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ENTRY_DELIMITER = "\n§\n"

# ---------------------------------------------------------------------------
# Memory content scanning — lightweight check for injection/exfiltration
# ---------------------------------------------------------------------------

_MEMORY_THREAT_PATTERNS = [
    (r'ignore\s+(previous|all|above|prior)\s+instructions', "prompt_injection"),
    (r'you\s+are\s+now\s+', "role_hijack"),
    (r'do\s+not\s+tell\s+the\s+user', "deception_hide"),
    (r'system\s+prompt\s+override', "sys_prompt_override"),
    (r'disregard\s+(your|all|any)\s+(instructions|rules|guidelines)', "disregard_rules"),
    (r'curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_curl"),
    (r'wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_wget"),
    (r'cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)', "read_secrets"),
]

_INVISIBLE_CHARS = {
    '​', '‌', '‍', '⁠', '﻿',
    '‪', '‫', '‬', '‭', '‮',
}


def _scan_memory_content(content: str) -> Optional[str]:
    """Scan memory content for injection/exfil patterns. Returns error string if blocked."""
    for char in _INVISIBLE_CHARS:
        if char in content:
            return f"Blocked: content contains invisible unicode character U+{ord(char):04X} (possible injection)."

    for pattern, pid in _MEMORY_THREAT_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            return f"Blocked: content matches threat pattern '{pid}'. Memory entries are injected into the system prompt and must not contain injection or exfiltration payloads."

    return None


class MemoryStore:
    """File-backed memory store with frozen snapshot for system prompt stability.

    Manages two types of stores:
      - Session stores (analysis_notes, session_context) in workspace
      - Global stores (memory, user) in project root

    The frozen snapshot pattern:
      - At load_from_disk(), current state is captured into _system_prompt_snapshot
      - This snapshot is used for system prompt injection (prefix cache stability)
      - Mid-session writes update files and live state but NOT the snapshot
      - The snapshot refreshes on next session start
    """

    def __init__(
        self,
        session_dir: Path,
        global_dir: Path = None,
        notes_char_limit: int = 3000,
        context_char_limit: int = 1500,
        memory_char_limit: int = 3000,
        user_char_limit: int = 1500,
    ):
        self._session_dir = session_dir / ".agent_memory"
        self._global_dir = global_dir / ".agent_memory" if global_dir else None

        # Session-layer entries
        self.notes_entries: List[str] = []
        self.context_entries: List[str] = []

        # Global-layer entries
        self.memory_entries: List[str] = []
        self.user_entries: List[str] = []

        # Limits
        self._notes_char_limit = notes_char_limit
        self._context_char_limit = context_char_limit
        self._memory_char_limit = memory_char_limit
        self._user_char_limit = user_char_limit

        # Frozen snapshot for system prompt — set once at load_from_disk()
        self._system_prompt_snapshot: Dict[str, str] = {}

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def load_from_disk(self):
        """Load all entries from disk and capture system prompt snapshot."""
        # Session layer
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self.notes_entries = self._read_file(self._session_dir / "analysis_notes.md")
        self.context_entries = self._read_file(self._session_dir / "session_context.md")

        # Global layer
        if self._global_dir:
            self._global_dir.mkdir(parents=True, exist_ok=True)
            self.memory_entries = self._read_file(self._global_dir / "MEMORY.md")
            self.user_entries = self._read_file(self._global_dir / "USER.md")
        else:
            self.memory_entries = []
            self.user_entries = []

        # Deduplicate (preserves order)
        self.notes_entries = list(dict.fromkeys(self.notes_entries))
        self.context_entries = list(dict.fromkeys(self.context_entries))
        self.memory_entries = list(dict.fromkeys(self.memory_entries))
        self.user_entries = list(dict.fromkeys(self.user_entries))

        # Capture frozen snapshot
        self._system_prompt_snapshot = self._build_snapshot()

    def _build_snapshot(self) -> Dict[str, str]:
        """Build the frozen snapshot from all loaded entries."""
        snapshot = {}
        if self.user_entries:
            snapshot["user"] = self._render_block("user", self.user_entries, self._user_char_limit)
        if self.memory_entries:
            snapshot["memory"] = self._render_block("memory", self.memory_entries, self._memory_char_limit)
        if self.notes_entries:
            snapshot["analysis_notes"] = self._render_block("analysis_notes", self.notes_entries, self._notes_char_limit)
        if self.context_entries:
            snapshot["session_context"] = self._render_block("session_context", self.context_entries, self._context_char_limit)
        return snapshot

    # ── File I/O (adapted from hermes) ─────────────────────────────────────────

    @staticmethod
    @contextmanager
    def _file_lock(path: Path):
        """Acquire an exclusive file lock for read-modify-write safety."""
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = open(lock_path, "w")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()

    @staticmethod
    def _read_file(path: Path) -> List[str]:
        """Read a memory file and split into entries."""
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, IOError):
            return []
        if not raw.strip():
            return []
        entries = [e.strip() for e in raw.split(ENTRY_DELIMITER)]
        return [e for e in entries if e]

    @staticmethod
    def _write_file(path: Path, entries: List[str]):
        """Write entries to a memory file using atomic temp-file + rename."""
        content = ENTRY_DELIMITER.join(entries) if entries else ""
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(path.parent), suffix=".tmp", prefix=".mem_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, str(path))  # Atomic on same filesystem
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except (OSError, IOError) as e:
            raise RuntimeError(f"Failed to write memory file {path}: {e}")

    def save_to_disk(self, target: str):
        """Persist entries to the appropriate file."""
        path = self._path_for(target)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        entries = self._entries_for(target)
        self._write_file(path, entries)

    # ── Path resolution ────────────────────────────────────────────────────────

    def _path_for(self, target: str) -> Optional[Path]:
        """Return the file path for a given target."""
        mapping = {
            "analysis_notes": self._session_dir / "analysis_notes.md",
            "session_context": self._session_dir / "session_context.md",
        }
        if self._global_dir:
            mapping["memory"] = self._global_dir / "MEMORY.md"
            mapping["user"] = self._global_dir / "USER.md"
        return mapping.get(target)

    def _entries_for(self, target: str) -> List[str]:
        return {
            "analysis_notes": self.notes_entries,
            "session_context": self.context_entries,
            "memory": self.memory_entries,
            "user": self.user_entries,
        }.get(target, [])

    def _set_entries(self, target: str, entries: List[str]):
        if target == "analysis_notes":
            self.notes_entries = entries
        elif target == "session_context":
            self.context_entries = entries
        elif target == "memory":
            self.memory_entries = entries
        elif target == "user":
            self.user_entries = entries

    def _char_limit(self, target: str) -> int:
        return {
            "analysis_notes": self._notes_char_limit,
            "session_context": self._context_char_limit,
            "memory": self._memory_char_limit,
            "user": self._user_char_limit,
        }.get(target, 3000)

    def _char_count(self, target: str) -> int:
        entries = self._entries_for(target)
        if not entries:
            return 0
        return len(ENTRY_DELIMITER.join(entries))

    def _is_global(self, target: str) -> bool:
        return target in ("memory", "user")

    # ── CRUD operations ────────────────────────────────────────────────────────

    def _reload_target(self, target: str):
        """Re-read entries from disk into in-memory state. Called under file lock."""
        path = self._path_for(target)
        if path is None:
            return
        fresh = self._read_file(path)
        fresh = list(dict.fromkeys(fresh))
        self._set_entries(target, fresh)

    def add(self, target: str, content: str) -> Dict[str, Any]:
        """Append a new entry."""
        content = content.strip()
        if not content:
            return {"success": False, "error": "Content cannot be empty."}

        scan_error = _scan_memory_content(content)
        if scan_error:
            return {"success": False, "error": scan_error}

        path = self._path_for(target)
        if path is None:
            return {"success": False, "error": f"Unknown target '{target}'."}

        with self._file_lock(path):
            self._reload_target(target)
            entries = self._entries_for(target)
            limit = self._char_limit(target)

            if content in entries:
                return self._success_response(target, "Entry already exists (no duplicate added).")

            new_entries = entries + [content]
            new_total = len(ENTRY_DELIMITER.join(new_entries))

            if new_total > limit:
                current = self._char_count(target)
                return {
                    "success": False,
                    "error": (
                        f"Memory at {current:,}/{limit:,} chars. "
                        f"Adding this entry ({len(content)} chars) would exceed the limit. "
                        f"Replace or remove existing entries first."
                    ),
                    "current_entries": entries,
                    "usage": f"{current:,}/{limit:,}",
                }

            entries.append(content)
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "Entry added.")

    def replace(self, target: str, old_text: str, new_content: str) -> Dict[str, Any]:
        """Find entry containing old_text substring, replace it with new_content."""
        old_text = old_text.strip()
        new_content = new_content.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}
        if not new_content:
            return {"success": False, "error": "new_content cannot be empty. Use 'remove' to delete entries."}

        scan_error = _scan_memory_content(new_content)
        if scan_error:
            return {"success": False, "error": scan_error}

        path = self._path_for(target)
        if path is None:
            return {"success": False, "error": f"Unknown target '{target}'."}

        with self._file_lock(path):
            self._reload_target(target)
            entries = self._entries_for(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]

            if not matches:
                return {"success": False, "error": f"No entry matched '{old_text}'."}

            if len(matches) > 1:
                unique_texts = set(e for _, e in matches)
                if len(unique_texts) > 1:
                    previews = [e[:80] + ("..." if len(e) > 80 else "") for _, e in matches]
                    return {
                        "success": False,
                        "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                        "matches": previews,
                    }

            idx = matches[0][0]
            limit = self._char_limit(target)
            test_entries = entries.copy()
            test_entries[idx] = new_content
            new_total = len(ENTRY_DELIMITER.join(test_entries))

            if new_total > limit:
                return {
                    "success": False,
                    "error": (
                        f"Replacement would put memory at {new_total:,}/{limit:,} chars. "
                        f"Shorten the new content or remove other entries first."
                    ),
                }

            entries[idx] = new_content
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "Entry replaced.")

    def remove(self, target: str, old_text: str) -> Dict[str, Any]:
        """Remove the entry containing old_text substring."""
        old_text = old_text.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}

        path = self._path_for(target)
        if path is None:
            return {"success": False, "error": f"Unknown target '{target}'."}

        with self._file_lock(path):
            self._reload_target(target)
            entries = self._entries_for(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]

            if not matches:
                return {"success": False, "error": f"No entry matched '{old_text}'."}

            if len(matches) > 1:
                unique_texts = set(e for _, e in matches)
                if len(unique_texts) > 1:
                    previews = [e[:80] + ("..." if len(e) > 80 else "") for _, e in matches]
                    return {
                        "success": False,
                        "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                        "matches": previews,
                    }

            idx = matches[0][0]
            entries.pop(idx)
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "Entry removed.")

    # ── System prompt integration ──────────────────────────────────────────────

    def format_for_system_prompt(self) -> str:
        """Return the frozen snapshot as a formatted block for system prompt.

        This returns the state captured at load_from_disk() time, NOT the live state.
        Mid-session writes do not affect this — keeps prefix cache stable.
        """
        if not self._system_prompt_snapshot:
            return ""
        blocks = []
        for key in ("user", "memory", "session_context", "analysis_notes"):
            block = self._system_prompt_snapshot.get(key, "")
            if block:
                blocks.append(block)
        return "\n\n".join(blocks)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _success_response(self, target: str, message: str = None) -> Dict[str, Any]:
        entries = self._entries_for(target)
        current = self._char_count(target)
        limit = self._char_limit(target)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        resp = {
            "success": True,
            "target": target,
            "entries": entries,
            "usage": f"{pct}% — {current:,}/{limit:,} chars",
            "entry_count": len(entries),
        }
        if message:
            resp["message"] = message
        return resp

    def _render_block(self, target: str, entries: List[str], limit: int) -> str:
        """Render a system prompt block with header and usage indicator."""
        if not entries:
            return ""

        content = ENTRY_DELIMITER.join(entries)
        current = len(content)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        labels = {
            "user": "USER PROFILE (用户画像 — preferences, style, expertise)",
            "memory": "AGENT MEMORY (智能体笔记 — lessons, patterns, conventions)",
            "analysis_notes": "ANALYSIS NOTES (分析笔记 — data observations from this session)",
            "session_context": "SESSION CONTEXT (会话上下文 — scope, decisions, preferences)",
        }
        header = f"{labels.get(target, target.upper())} [{pct}% — {current:,}/{limit:,} chars]"
        separator = "═" * 46
        return f"{separator}\n{header}\n{separator}\n{content}"
