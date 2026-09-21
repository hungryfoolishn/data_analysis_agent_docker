from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from langgraph_langchain.runtime.plans import AnalysisPlan
from langgraph_langchain.runtime.models import RuntimePlanStep
from langgraph_langchain.runtime.scheduler import TaskScheduler
from langgraph_langchain.runtime.skill_retriever import (
    SkillMatch,
    SkillRetriever,
    default_tool_for_task_type,
)
from langgraph_langchain.schemas import AnalysisTask, TaskType
from langgraph_langchain.skills_loader import SkillsLoader


def make_task(
    task_id: str = "task_1",
    *,
    task_type: TaskType = "comparison",
    method: str | None = None,
    question: str = "Analyze revenue",
    constraints: dict[str, Any] | None = None,
) -> AnalysisTask:
    return AnalysisTask(
        task_id=task_id,
        session_id="session_1",
        question=question,
        task_type=task_type,
        executor_type="structured",
        method=method,
        constraints=constraints or {},
    )


def write_skill(
    directory: Path,
    *,
    name: str,
    keywords: list[str],
    tags: list[str] | None = None,
    description: str = "Test skill.",
) -> Path:
    skill_dir = directory / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    tags_text = ", ".join(tags or [])
    keywords_text = "\n".join(f"    - {keyword}" for keyword in keywords)
    text = f"""---
name: {name}
description: >
  {description}
version: 1.0.0
metadata:
  tags: [{tags_text}]
  category: data-analysis
  trigger_keywords:
{keywords_text}
---

# {name}
"""
    path = skill_dir / "SKILL.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_default_tool_mapping_covers_all_task_types():
    for task_type in get_args_task_types():
        assert default_tool_for_task_type(task_type)


def get_args_task_types() -> list[str]:
    # Keep the test explicit and independent from typing internals.
    return [
        "schema",
        "profile",
        "metric",
        "comparison",
        "trend",
        "breakdown",
        "contribution",
        "anomaly",
        "correlation",
        "statistical_test",
        "root_cause",
        "visualization",
        "report",
    ]


def test_default_tool_mapping_rejects_unknown_type():
    with pytest.raises(ValueError, match="Unknown task type"):
        default_tool_for_task_type("unknown")


def test_empty_loader_returns_structured_fallback():
    loader = SkillsLoader(Path("does/not/exist"))
    retriever = SkillRetriever(loader)
    task = make_task(method="compare_groups")

    match = retriever.retrieve_for_task(task)

    assert isinstance(match, SkillMatch)
    assert match.fallback is True
    assert match.matched is False
    assert match.skill_name is None
    assert match.tool_name == "compare_groups"


def test_exact_requested_skill_from_constraints(tmp_path: Path):
    write_skill(
        tmp_path,
        name="cohort-analysis",
        keywords=["retention"],
    )
    loader = SkillsLoader(tmp_path)
    retriever = SkillRetriever(loader)
    task = make_task(
        method="python_repl",
        constraints={"skill_name": "cohort-analysis"},
    )

    match = retriever.retrieve_for_task(task)

    assert match.fallback is False
    assert match.source == "exact"
    assert match.score == 1.0
    assert match.skill_name == "cohort-analysis"
    assert match.skill is not None
    assert match.skill.name == "cohort-analysis"
    assert match.tool_name == "python_repl"


def test_exact_requested_skill_missing_falls_back(tmp_path: Path):
    write_skill(tmp_path, name="other-skill", keywords=["other"])
    loader = SkillsLoader(tmp_path)
    retriever = SkillRetriever(loader)
    task = make_task(
        method="compare_groups",
        constraints={"skill_name": "cohort-analysis"},
    )

    match = retriever.retrieve_for_task(task)

    assert match.fallback is True
    assert match.reason == "Requested skill 'cohort-analysis' is not loaded"
    assert match.tool_name == "compare_groups"


def test_exact_method_match_prefers_skill(tmp_path: Path):
    write_skill(
        tmp_path,
        name="cohort-analysis",
        keywords=["retention"],
    )
    loader = SkillsLoader(tmp_path)
    retriever = SkillRetriever(loader)
    task = make_task(method="cohort-analysis")

    match = retriever.retrieve_for_task(task)

    assert match.fallback is False
    assert match.source == "exact"
    assert match.score == 1.0
    assert match.skill_name == "cohort-analysis"


def test_metadata_keyword_match(tmp_path: Path):
    write_skill(
        tmp_path,
        name="cohort-analysis",
        keywords=["retention", "cohort"],
        tags=["LTV"],
    )
    loader = SkillsLoader(tmp_path)
    retriever = SkillRetriever(loader)
    task = make_task(
        method=None,
        question="Analyze user retention by cohort and LTV",
    )

    match = retriever.retrieve_for_task(task)

    assert match.fallback is False
    assert match.source == "metadata"
    assert match.skill_name == "cohort-analysis"
    assert match.score > 0
    assert "trigger keywords" in match.reason
    assert "tags" in match.reason


def test_unrelated_metadata_falls_back(tmp_path: Path):
    write_skill(
        tmp_path,
        name="cohort-analysis",
        keywords=["retention", "cohort"],
    )
    loader = SkillsLoader(tmp_path)
    retriever = SkillRetriever(loader)
    task = make_task(
        task_type="comparison",
        method=None,
        question="Compare department revenue",
    )

    match = retriever.retrieve_for_task(task)

    assert match.fallback is True
    assert match.skill_name is None
    assert match.tool_name == "compare_groups"


def test_retrieve_for_plan_preserves_order_and_uses_fallback(tmp_path: Path):
    write_skill(
        tmp_path,
        name="cohort-analysis",
        keywords=["retention"],
    )
    loader = SkillsLoader(tmp_path)
    retriever = SkillRetriever(loader)
    first = RuntimePlanStep(
        objective="Load source data",
        method="load_data",
    )
    second = RuntimePlanStep(
        objective="Analyze user retention by cohort",
        method="python_repl",
        depends_on=[first.step_id],
    )
    plan = AnalysisPlan(goal="Analyze retention", steps=[first, second])
    scheduler = TaskScheduler.from_plan(plan, session_id="session_plan")

    matches = retriever.retrieve_for_plan(scheduler)

    assert list(matches) == [first.step_id, second.step_id]
    assert matches[first.step_id].fallback is True
    assert matches[first.step_id].tool_name == "load_data"
    assert matches[second.step_id].fallback is False
    assert matches[second.step_id].skill_name == "cohort-analysis"
    assert matches[second.step_id].tool_name == "python_repl"


def test_retriever_is_deterministic(tmp_path: Path):
    write_skill(tmp_path, name="skill-a", keywords=["retention"])
    write_skill(tmp_path, name="skill-b", keywords=["retention"])
    loader = SkillsLoader(tmp_path)
    retriever = SkillRetriever(loader)
    task = make_task(question="Analyze retention")

    first = retriever.retrieve_for_task(task)
    second = retriever.retrieve_for_task(task)

    assert first == second
