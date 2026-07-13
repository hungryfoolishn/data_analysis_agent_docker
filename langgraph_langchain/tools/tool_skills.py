"""Skill tools — progressive disclosure for analysis skills."""

from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.tools import tool

from langgraph_langchain.tools.registry import registry
from langgraph_langchain.tools._shared import _validate_tool_stage_factory
from langgraph_langchain.skills_loader import SkillsLoader

logger = logging.getLogger(__name__)

_skills_loader = SkillsLoader(Path(__file__).resolve().parent.parent / "skills")


def _factory(session):
    _validate = _validate_tool_stage_factory(session)

    @tool
    def skill_view(skill_name: str) -> str:
        """Load the full instructions for a named analysis skill.

        When the analysis task matches a skill listed in the system prompt,
        call this tool to get the step-by-step workflow. Then follow the skill's
        steps using python_repl / record_finding as directed.

        Args:
            skill_name: Skill name, e.g. "funnel-analysis", "cohort-analysis".
        """
        error_msg = _validate("skill_view")
        if error_msg:
            return f"[ERROR] {error_msg}"

        content = _skills_loader.skill_view(skill_name)
        if content:
            return content
        available = ", ".join(m.name for m in _skills_loader.skills_list())
        return f"Skill '{skill_name}' not found. Available: {available}"

    @tool
    def skill_reference(skill_name: str, reference: str) -> str:
        """Load a reference document from an analysis skill.

        Use this for deeper domain knowledge: metric definitions, benchmarks,
        validation rules, etc.

        Args:
            skill_name: Skill name, e.g. "funnel-analysis".
            reference: Reference file name, e.g. "ecommerce_benchmarks.md".
        """
        error_msg = _validate("skill_reference")
        if error_msg:
            return f"[ERROR] {error_msg}"

        content = _skills_loader.skill_reference(skill_name, reference)
        if content:
            return content
        return f"Reference '{reference}' not found in skill '{skill_name}'."

    return [skill_view, skill_reference]


registry.register(
    name="skill_view",
    toolset="skills",
    factory=_factory,
    description="Load the full instructions for a named analysis skill.",
    emoji="🎯",
)

registry.register(
    name="skill_reference",
    toolset="skills",
    factory=_factory,
    description="Load a reference document from an analysis skill.",
    emoji="📚",
)
