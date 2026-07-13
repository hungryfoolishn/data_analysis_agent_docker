"""Dynamic prompt builder — assembles system prompt from modular .md sections.

Adapted from hermes-agent's agent/prompt_builder.py, simplified for data analysis.

Sections are loaded from prompts/sections/ directory. Each section is an
independent .md file, editable without touching Python code. The builder
supports:
  - Ordered section assembly
  - Content scanning for injection prevention (reuses memory_store patterns)
  - Caching for loaded sections
  - Dynamic context injection (skills index, memory snapshot)

Adding a new analysis scenario: just add a .md file and add its name to SECTION_ORDER.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Section loading order — defines the system prompt structure
SECTION_ORDER = [
    "identity",              # Role + domain expertise
    "workflow",               # Required 9-step workflow
    "stage_requirements",     # 6-stage state machine + tool restrictions
    "analysis_framework",     # 7-step analysis methodology
    "analysis_rules",         # Reasoning rules, evidence levels
    "python_repl_rules",      # Step markers, output rules, helpers
    "error_recovery",         # Retry guidance for python_repl
    "rd_domain",              # R&D efficiency domain knowledge
]

# ---------------------------------------------------------------------------
# Content scanning — prevent prompt injection in section files
# ---------------------------------------------------------------------------

_THREAT_PATTERNS = [
    (r'ignore\s+(previous|all|above|prior)\s+instructions', "prompt_injection"),
    (r'do\s+not\s+tell\s+the\s+user', "deception_hide"),
    (r'system\s+prompt\s+override', "sys_prompt_override"),
    (r'disregard\s+(your|all|any)\s+(instructions|rules|guidelines)', "disregard_rules"),
]

_INVISIBLE_CHARS = {
    '​', '‌', '‍', '⁠', '﻿',
    '‪', '‫', '‬', '‭', '‮',
}


def _scan_section(content: str, filename: str) -> str:
    """Scan section content for injection. Returns original or blocked message."""
    for char in _INVISIBLE_CHARS:
        if char in content:
            logger.warning("Section %s blocked: invisible unicode U+%04X", filename, ord(char))
            return f"[BLOCKED: {filename} contained invisible unicode. Content not loaded.]"

    for pattern, pid in _THREAT_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            logger.warning("Section %s blocked: %s", filename, pid)
            return f"[BLOCKED: {filename} contained potential injection ({pid}). Content not loaded.]"

    return content


class PromptBuilder:
    """Assembles system prompt from modular .md section files.

    Usage:
        builder = PromptBuilder(Path(__file__).parent / "prompts" / "sections")
        base_prompt = builder.build()
        full_prompt = builder.build_with_context(
            skills_index="## Skills\\n- ...",
            memory_snapshot="MEMORY content...",
        )
    """

    def __init__(self, sections_dir: Path):
        self._sections_dir = sections_dir
        self._cache: Dict[str, str] = {}

    def _load_section(self, name: str) -> str:
        """Load and cache a single section file."""
        if name in self._cache:
            return self._cache[name]

        path = self._sections_dir / f"{name}.md"
        if not path.exists():
            logger.warning("Prompt section not found: %s", path)
            return ""

        try:
            content = path.read_text(encoding="utf-8").strip()
        except (OSError, IOError) as e:
            logger.warning("Failed to read section %s: %s", name, e)
            return ""

        # Scan for injection
        content = _scan_section(content, f"{name}.md")

        self._cache[name] = content
        return content

    def build(self) -> str:
        """Build the base system prompt from all sections in order."""
        parts = []
        for section_name in SECTION_ORDER:
            content = self._load_section(section_name)
            if content:
                parts.append(content)
        return "\n\n".join(parts)

    def build_with_context(
        self,
        skills_index: str = "",
        memory_snapshot: str = "",
    ) -> str:
        """Build full system prompt with dynamic context layers.

        Layers (in order):
          1. Base sections (identity, rules, framework, ...)
          2. Skills index (from SkillsLoader)
          3. Memory snapshot (from MemoryStore)
        """
        prompt = self.build()

        if skills_index:
            prompt += f"\n\n{skills_index}"

        if memory_snapshot:
            prompt += f"\n\n{memory_snapshot}"

        return prompt

    def reload(self):
        """Clear cache and force reload of all sections."""
        self._cache.clear()
        logger.info("Prompt builder cache cleared — sections will reload on next build()")
