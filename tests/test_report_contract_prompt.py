from pathlib import Path

from langgraph_langchain.prompts.prompt_builder import PromptBuilder


def test_effective_prompt_contains_report_preflight_contract():
    sections = Path(__file__).parents[1] / "langgraph_langchain" / "prompts" / "sections"
    prompt = PromptBuilder(sections).build()

    assert "## Final report contract" in prompt
    assert "static cross-sectional analysis" in prompt
    assert "## Visualizations" in prompt
    assert "state the numerical pattern" in prompt
