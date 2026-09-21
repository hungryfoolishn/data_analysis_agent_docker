"""Skills loader — progressive-disclosure architecture.

Three tiers:
  Tier 1  skills_list()     → metadata (name + description + tags), injected into system prompt
  Tier 2  skill_view()      → full SKILL.md body, loaded on demand
  Tier 3  references/       → supporting docs, loaded for deep scenarios

Compatible with the agentskills.io YAML-frontmatter convention.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class SkillMeta(BaseModel):
    """Tier-1 metadata — lightweight, shown in system prompt."""
    name: str
    description: str
    version: str = "1.0.0"
    tags: List[str] = []
    category: str = ""
    trigger_keywords: List[str] = []
    data_patterns: Dict = {}
    related_skills: List[str] = []


class Skill(BaseModel):
    """Full skill (Tier 2)."""
    meta: SkillMeta
    content: str                         # SKILL.md body
    references: Dict[str, Path] = {}     # ref-name → file path
    path: Path


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

class SkillsLoader:
    """Discover, parse, and match analysis skills."""

    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self._cache: Dict[str, Skill] = {}
        self._load_all()

    # ── Discovery & parsing ────────────────────────────────────────────────

    def _load_all(self) -> None:
        """Scan ``skills/`` for every SKILL.md and parse metadata."""
        if not self.skills_dir.exists():
            logger.warning("Skills directory not found: %s", self.skills_dir)
            return

        for skill_file in sorted(self.skills_dir.rglob("SKILL.md")):
            try:
                meta, content = self._parse_skill(skill_file)
                if meta:
                    self._cache[meta.name] = Skill(
                        meta=meta,
                        content=content,
                        references=self._find_references(skill_file),
                        path=skill_file,
                    )
                    logger.debug("Loaded skill: %s", meta.name)
            except Exception as exc:
                logger.error("Failed to parse skill %s: %s", skill_file, exc)

    @staticmethod
    def _parse_skill(path: Path) -> Tuple[Optional[SkillMeta], str]:
        """Parse YAML frontmatter + Markdown body from a SKILL.md."""
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return None, ""
        parts = text.split("---", 2)
        if len(parts) < 3:
            return None, ""
        _, fm_raw, body = parts
        data = yaml.safe_load(fm_raw) or {}
        meta_hermes = data.get("metadata", {})
        # Support both nested (hermes style) and flat metadata
        flat = data.get("metadata", {})
        meta = SkillMeta(
            name=data.get("name", path.parent.name),
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            tags=flat.get("tags", []),
            category=flat.get("category", ""),
            trigger_keywords=flat.get("trigger_keywords", []),
            data_patterns=flat.get("data_patterns", {}),
            related_skills=flat.get("related_skills", []),
        )
        return meta, body.strip()

    @staticmethod
    def _find_references(skill_file: Path) -> Dict[str, Path]:
        """Find all files under a skill's ``references/`` directory."""
        refs: Dict[str, Path] = {}
        ref_dir = skill_file.parent / "references"
        if ref_dir.is_dir():
            for ref in ref_dir.rglob("*"):
                if ref.is_file():
                    refs[ref.name] = ref
        return refs

    # ── Tier 1: list (lightweight) ────────────────────────────────────────

    def skills_list(self, category: Optional[str] = None) -> List[SkillMeta]:
        """Return metadata for all (or category-filtered) skills."""
        skills = [s.meta for s in self._cache.values()]
        if category:
            skills = [s for s in skills if s.category == category]
        return skills

    def build_skills_prompt(self) -> str:
        """Build a compact skills index to append to the system prompt."""
        if not self._cache:
            return ""
        lines = [
            "",
            "## Available Analysis Skills (技能)",
            "",
            "When the analysis task matches a skill, load its full instructions with `skill_view`.",
            "",
        ]
        for meta in sorted(self.skills_list(), key=lambda m: m.category):
            kw = ", ".join(meta.trigger_keywords[:5]) if meta.trigger_keywords else ""
            lines.append(f"### {meta.name}  ({meta.category})")
            lines.append(f"{meta.description}")
            if kw:
                lines.append(f"Keywords: {kw}")
            lines.append("")
        lines.append(
            "Usage: call `skill_view(skill_name='...')` to load the full workflow, "
            "then follow its steps."
        )
        return "\n".join(lines)

    # ── Tier 2: view full content ─────────────────────────────────────────

    def skill_view(self, name: str) -> Optional[str]:
        """Load full skill instructions (Agent calls on demand)."""
        skill = self._cache.get(name)
        return skill.content if skill else None

    # ── Tier 3: references ─────────────────────────────────────────────────

    def skill_version(self, name: str) -> Optional[str]:
        """Return declared metadata version for a loaded skill."""
        skill = self._cache.get(name)
        return skill.meta.version if skill else None

    def skill_hash(self, name: str) -> Optional[str]:
        """Return the SHA-256 hash of the loaded SKILL.md file."""
        skill = self._cache.get(name)
        if not skill:
            return None
        return f"sha256:{hashlib.sha256(skill.path.read_bytes()).hexdigest()}"

    def skill_reference(self, name: str, ref_name: str) -> Optional[str]:
        """Load a reference document from a skill."""
        skill = self._cache.get(name)
        if not skill:
            return None
        ref_path = skill.references.get(ref_name)
        if ref_path and ref_path.exists():
            return ref_path.read_text(encoding="utf-8")
        return None

    # ── Skill matching ─────────────────────────────────────────────────────

    def match(
        self,
        question: str,
        df=None,
        top_k: int = 3,
    ) -> List[Tuple[str, float]]:
        """Match the best skills for *question* (and optionally *df*).

        Returns ``[(skill_name, confidence), ...]`` sorted by confidence.
        """
        scored: List[Tuple[str, float]] = []
        q_lower = question.lower()

        for skill in self._cache.values():
            score = 0.0
            # keyword match
            for kw in skill.meta.trigger_keywords:
                if kw.lower() in q_lower:
                    score += 0.3
            # tag match
            for tag in skill.meta.tags:
                if tag.lower() in q_lower:
                    score += 0.2
            # data-pattern match
            if df is not None and skill.meta.data_patterns:
                score += self._score_data_match(df, skill.meta.data_patterns)

            if score > 0:
                scored.append((skill.meta.name, min(score, 1.0)))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    @staticmethod
    def _score_data_match(df, patterns: Dict) -> float:
        """Score how well a DataFrame matches the declared data patterns."""
        score = 0.0
        col_keywords: Dict[str, List[str]] = {
            "has_user_id": ["user", "uid", "customer", "member", "用户"],
            "has_timestamp": ["time", "date", "datetime", "timestamp", "时间", "日期"],
            "has_event_type": ["event", "action", "type", "status", "事件", "状态"],
            "has_amount": ["amount", "price", "revenue", "money", "金额", "价格", "收入"],
        }
        cols_lower = [c.lower() for c in df.columns]

        for pattern_key, keywords in col_keywords.items():
            if patterns.get(pattern_key):
                if any(kw in c for c in cols_lower for kw in keywords):
                    score += 0.2

        return score

    # ── Reload (after skill creation) ──────────────────────────────────────

    def reload(self) -> int:
        """Re-scan the skills directory and return the new count."""
        self._cache.clear()
        self._load_all()
        return len(self._cache)
