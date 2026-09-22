"""Deterministic skill retrieval for Runtime V2.

The retriever deliberately uses only lightweight metadata available from
``SkillsLoader``.  It does not use a vector database and never executes a tool
or an LLM.  If no skill is relevant, it returns an explicit fallback so the
scheduler/executor can continue with the task's structured tool.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping, Optional

from pydantic import BaseModel, Field

from langgraph_langchain.schemas import AnalysisTask
from langgraph_langchain.skills_loader import SkillMeta, SkillsLoader


MatchSource = Literal["exact", "metadata", "fallback"]


class SkillMatch(BaseModel):
    """A retrieval decision for one Runtime task."""

    skill_name: Optional[str] = Field(default=None, description="Matched skill name")
    skill_id: Optional[str] = Field(default=None, description="Stable matched skill ID")
    skill: Optional[SkillMeta] = Field(default=None, description="Matched skill metadata")
    skill_version: Optional[str] = Field(default=None, description="Matched skill version")
    skill_hash: Optional[str] = Field(default=None, description="SHA-256 hash of the matched SKILL.md")
    score: float = Field(default=0.0, ge=0, le=1, description="Match confidence")
    source: MatchSource = Field(default="fallback", description="How the match was made")
    reason: str = Field(default="No relevant skill; use the task's structured tool")
    fallback: bool = Field(default=True, description="Whether no skill was selected")
    tool_name: Optional[str] = Field(
        default=None,
        description="Tool used when no skill is selected, or the task's explicit method",
    )

    @property
    def matched(self) -> bool:
        return not self.fallback


# A small, explicit tool mapping is preferable to a complex policy layer while
# Runtime V2 is being stabilized.  Skills may override these defaults.
_TOOL_BY_TASK_TYPE: dict[str, str] = {
    "schema": "load_data",
    "profile": "eda_profile",
    "metric": "calculate_ratio",
    "comparison": "compare_groups",
    "trend": "analyze_time_trend",
    "breakdown": "compare_groups",
    "contribution": "decompose_contribution",
    "anomaly": "detect_anomalies",
    "correlation": "analyze_correlation",
    "statistical_test": "test_group_difference",
    "root_cause": "python_repl",
    "visualization": "python_repl",
    "report": "finish_report",
}

# Lightweight lexical hints used only to score already-loaded skill metadata.
_TASK_TYPE_HINTS: dict[str, tuple[str, ...]] = {
    "schema": ("schema", "field", "column", "字段"),
    "profile": ("profile", "quality", "分布", "质量"),
    "metric": ("metric", "ratio", "指标", "比率"),
    "comparison": ("comparison", "compare", "对比", "同比", "环比"),
    "trend": ("trend", "velocity", "趋势", "速率"),
    "breakdown": ("breakdown", "segment", "拆分", "分群", "分层"),
    "contribution": ("contribution", "attribution", "贡献", "归因"),
    "anomaly": ("anomaly", "outlier", "异常", "离群"),
    "correlation": ("correlation", "相关"),
    "statistical_test": ("test", "significance", "显著性", "检验"),
    "root_cause": ("root cause", "diagnosis", "根因", "原因"),
    "visualization": ("chart", "plot", "图", "可视化"),
    "report": ("report", "summary", "报告", "总结"),
}


def default_tool_for_task_type(task_type: str) -> str:
    """Return the first-stage structured tool for a task type."""
    try:
        return _TOOL_BY_TASK_TYPE[task_type]
    except KeyError as exc:
        raise ValueError(f"Unknown task type: {task_type}") from exc


class SkillRetriever:
    """Retrieve skills for Runtime V2 tasks using deterministic rules."""

    def __init__(
        self,
        skills_loader: Optional[SkillsLoader] = None,
        *,
        task_type_tools: Optional[Mapping[str, str]] = None,
        learning_memory: Any = None,
    ) -> None:
        self._skills_loader = skills_loader
        self._task_type_tools = dict(_TOOL_BY_TASK_TYPE)
        if task_type_tools:
            self._task_type_tools.update(task_type_tools)
        self._learning_memory = learning_memory

    def _identity(
        self,
        skill: Optional[SkillMeta],
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        if skill is None or self._skills_loader is None:
            return None, None, None
        get_skill_id = getattr(self._skills_loader, "skill_id", None)
        get_version = getattr(self._skills_loader, "skill_version", None)
        get_hash = getattr(self._skills_loader, "skill_hash", None)
        return (
            get_skill_id(skill.name) if callable(get_skill_id) else skill.skill_id,
            get_version(skill.name) if callable(get_version) else skill.version,
            get_hash(skill.name) if callable(get_hash) else None,
        )

    @property
    def skills(self) -> list[SkillMeta]:
        if self._skills_loader is None:
            return []
        return list(self._skills_loader.skills_list())

    def _fallback(self, task: AnalysisTask, reason: str) -> SkillMatch:
        tool_name = task.method or self._task_type_tools.get(task.task_type)
        return SkillMatch(
            skill_name=None,
            skill_id=None,
            skill=None,
            skill_version=None,
            skill_hash=None,
            score=0.0,
            source="fallback",
            reason=reason,
            fallback=True,
            tool_name=tool_name,
        )

    def retrieve_for_task(self, task: AnalysisTask) -> SkillMatch:
        """Return the best skill match or an explicit fallback for a task."""
        requested_skill = task.constraints.get("skill_name")
        if isinstance(requested_skill, str) and requested_skill.strip():
            requested_skill = requested_skill.strip()
            for skill in self.skills:
                if skill.name == requested_skill:
                    return SkillMatch(
                        skill_name=skill.name,
                        skill=skill,
                        score=1.0,
                        source="exact",
                        reason="Task constraints requested this skill by name",
                        fallback=False,
                        tool_name=task.method,
                    )
            return self._fallback(
                task,
                f"Requested skill '{requested_skill}' is not loaded",
            )

        if task.method:
            for skill in self.skills:
                if skill.name == task.method:
                    return SkillMatch(
                        skill_name=skill.name,
                        skill=skill,
                        score=1.0,
                        source="exact",
                        reason="Task method exactly matches a loaded skill",
                        fallback=False,
                        tool_name=task.method,
                    )

        candidates = self.skills
        if not candidates:
            return self._fallback(task, "No skills are loaded")

        haystack_parts = [
            task.question,
            task.method or "",
            str(task.task_type),
        ]
        haystack = " ".join(part for part in haystack_parts if part).lower()
        hints = _TASK_TYPE_HINTS.get(task.task_type, ())

        scored: list[tuple[float, SkillMeta, list[str]]] = []
        for skill in candidates:
            match_skill_id, _, _ = self._identity(skill)
            score = 0.0
            reasons: list[str] = []

            if task.method and task.method.lower() in skill.name.lower():
                score += 0.65
                reasons.append(f"method resembles skill name '{skill.name}'")
            if skill.name.lower() in haystack:
                score += 0.55
                reasons.append(f"skill name '{skill.name}' appears in task text")

            matched_keywords = [
                keyword
                for keyword in skill.trigger_keywords
                if keyword and keyword.lower() in haystack
            ]
            if matched_keywords:
                score += min(0.35 * len(matched_keywords), 0.7)
                reasons.append(f"trigger keywords: {', '.join(matched_keywords)}")

            matched_tags = [
                tag
                for tag in skill.tags
                if tag and tag.lower() in haystack
            ]
            if matched_tags:
                score += min(0.2 * len(matched_tags), 0.4)
                reasons.append(f"tags: {', '.join(matched_tags)}")

            task_type_hints = [
                hint
                for hint in hints
                if hint and (
                    hint in skill.name.lower()
                    or hint in skill.description.lower()
                    or any(hint in str(item).lower() for item in skill.tags)
                    or any(hint in str(item).lower() for item in skill.trigger_keywords)
                )
            ]
            if task_type_hints:
                score += min(0.1 * len(task_type_hints), 0.2)
                reasons.append(f"task-type hints: {', '.join(task_type_hints)}")

            if score > 0:
                if self._learning_memory is not None:
                    if match_skill_id:
                        recommendation = (
                            self._learning_memory.recommendation_for_skill_id(
                                match_skill_id
                            )
                        )
                    else:
                        recommendation = (
                            self._learning_memory.recommendation_for_skill(
                                skill.name
                            )
                        )
                    if recommendation == "reliable":
                        score = min(1.0, score + 0.10)
                        reasons.append("learning memory marks this skill reliable")
                    elif recommendation == "needs_review":
                        score = max(0.0, score - 0.30)
                        reasons.append(
                            "learning memory marks this skill needs_review"
                        )
                scored.append((min(score, 1.0), skill, reasons))

        if not scored:
            return self._fallback(task, "No loaded skill metadata matched this task")

        scored.sort(key=lambda item: (-item[0], item[1].name))
        best_score, best_skill, reasons = scored[0]
        skill_id, version, skill_hash = self._identity(best_skill)
        return SkillMatch(
            skill_name=best_skill.name,
            skill_id=skill_id,
            skill=best_skill,
            skill_version=version,
            skill_hash=skill_hash,
            score=best_score,
            source="metadata",
            reason="; ".join(reasons),
            fallback=False,
            tool_name=task.method,
        )

    def retrieve_for_plan(
        self,
        tasks: Any,
        *,
        preserve_order: bool = True,
    ) -> dict[str, SkillMatch]:
        """Retrieve matches for tasks or a scheduler's task collection."""
        if hasattr(tasks, "tasks"):
            task_items = list(tasks.tasks)
        elif isinstance(tasks, Mapping):
            task_items = list(tasks.values())
        elif isinstance(tasks, (list, tuple)):
            task_items = list(tasks)
        else:
            task_items = list(tasks)

        matches = {task.task_id: self.retrieve_for_task(task) for task in task_items}
        if preserve_order and hasattr(tasks, "tasks"):
            ordered_ids = [task.task_id for task in tasks.tasks]
            return {task_id: matches[task_id] for task_id in ordered_ids}
        return matches
