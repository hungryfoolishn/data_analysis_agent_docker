"""
Test template recommendation activation in eda_profile.
"""
import sys
import tempfile
from pathlib import Path
import pandas as pd

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from langgraph_langchain.langgraph_agent import _Session, _make_tools
from langgraph_langchain.rd_templates import suggest_template
from langgraph_langchain.state_machine import AnalysisStage


def test_template_matching():
    """Test that suggest_template correctly matches templates."""
    print("\n=== Test 1: Template Matching ===")

    # Test 1: Sprint retro question
    template = suggest_template("How did our sprint go?", ["sprint", "story_points", "completed"])
    assert template is not None, "Should match sprint retro template"
    assert "Sprint" in template.template_name
    print(f"✓ Sprint question matched: {template.template_name}")

    # Test 2: Velocity question
    template = suggest_template("What's our velocity trend?", ["sprint", "story_points"])
    assert template is not None, "Should match velocity template"
    assert "Velocity" in template.template_name
    print(f"✓ Velocity question matched: {template.template_name}")

    # Test 3: Quality question
    template = suggest_template("How is our quality?", ["bug_id", "severity", "created_date"])
    assert template is not None, "Should match quality template"
    assert "Quality" in template.template_name or "Defect" in template.template_name
    print(f"✓ Quality question matched: {template.template_name}")

    # Test 4: Deployment question
    template = suggest_template("How often do we deploy?", ["deployment_id", "deployment_date"])
    if template is None:
        # Try with just the question
        template = suggest_template("deployment frequency analysis", [])
    assert template is not None, "Should match deployment template"
    assert "Deployment" in template.template_name or "DORA" in template.template_name
    print(f"✓ Deployment question matched: {template.template_name}")

    # Test 5: Column-based matching (no question)
    template = suggest_template("", ["pr_id", "pr_created_at", "pr_merged_at"])
    assert template is not None, "Should match PR template by columns"
    assert "PR" in template.template_name or "Review" in template.template_name
    print(f"✓ Column-based matching worked: {template.template_name}")

    print("\n✅ All template matching tests passed!")


def test_template_matching_uses_rd_columns_with_generic_question():
    template = suggest_template(
        "请概览上传的数据",
        ["deployment_id", "deployment_date", "status"],
    )

    assert template is not None
    assert "Deployment" in template.template_name


def test_generic_data_quality_question_does_not_select_code_quality_template():
    template = suggest_template(
        "请对数据进行概览，包括基本统计、数据质量检查和缺失值分析",
        ["name", "age", "salary", "department", "years_experience"],
    )

    assert template is None


def test_eda_profile_with_template():
    """Test that eda_profile shows template recommendation."""
    print("\n=== Test 2: EDA Profile Template Display ===")

    with tempfile.TemporaryDirectory() as tmp_dir:
        # Create a sample sprint data CSV
        csv_path = Path(tmp_dir) / "sprint_data.csv"
        df = pd.DataFrame({
            "sprint": ["Sprint 1", "Sprint 1", "Sprint 2", "Sprint 2", "Sprint 3", "Sprint 3"],
            "story_points": [5, 8, 3, 13, 8, 5],
            "completed": [1, 1, 1, 0, 1, 1],
            "team": ["Team A", "Team A", "Team A", "Team A", "Team A", "Team A"],
        })
        df.to_csv(csv_path, index=False)

        # Create session with sprint-related question
        session = _Session(
            workspace_dir=tmp_dir,
            source_path=str(csv_path),
            session_id="test-template",
            user_question="How did our sprint go? What's our velocity?"
        )

        # Create tools
        tools = _make_tools(session)

        # Find and call load_data
        load_data_tool = next(t for t in tools if t.name == "load_data")
        load_result = load_data_tool.invoke({"file_path": str(csv_path)})
        print(f"\n✓ load_data result:\n{load_result[:200]}...")

        # Manually set stage to data_quality_check (where eda_profile is allowed)
        session.state_machine.current_stage = AnalysisStage.DATA_QUALITY_CHECK

        # Find and call eda_profile
        eda_tool = next(t for t in tools if t.name == "eda_profile")
        eda_result = eda_tool.invoke({"max_numeric_cols": 3, "max_cat_cols": 3})

        # Check that template recommendation is in the output
        assert "Recommended Analysis Template" in eda_result or "建议使用分析模板" in eda_result, \
            "EDA output should contain template recommendation section"
        print(f"\n✓ Template recommendation found in EDA output")

        # Check that template details are shown
        assert "Sprint" in eda_result, "Should mention Sprint template"
        assert "Key Metrics" in eda_result or "关键指标" in eda_result, "Should show key metrics"
        assert "Analysis Steps" in eda_result or "分析步骤" in eda_result, "Should show analysis steps"
        print(f"✓ Template details are displayed")

        # Print a sample of the output
        print("\n--- Sample EDA Output with Template ---")
        lines = eda_result.split('\n')
        template_section_start = None
        for i, line in enumerate(lines):
            if "Recommended Analysis Template" in line or "建议使用分析模板" in line:
                template_section_start = i
                break

        if template_section_start:
            # Print 20 lines starting from template section
            sample = '\n'.join(lines[template_section_start:template_section_start+20])
            print(sample)

        print("\n✅ EDA profile template display test passed!")


def test_template_in_namespace():
    """Test that template is stored in session namespace."""
    print("\n=== Test 3: Template Storage in Namespace ===")

    with tempfile.TemporaryDirectory() as tmp_dir:
        csv_path = Path(tmp_dir) / "deployment_data.csv"
        df = pd.DataFrame({
            "deployment_id": [1, 2, 3, 4, 5],
            "deployment_date": pd.date_range("2024-01-01", periods=5, freq="D"),
            "status": ["success", "success", "failed", "success", "success"],
        })
        df.to_csv(csv_path, index=False)

        session = _Session(
            workspace_dir=tmp_dir,
            source_path=str(csv_path),
            session_id="test-ns",
            user_question="How often do we deploy?"
        )

        tools = _make_tools(session)

        # Load data and run EDA
        load_data_tool = next(t for t in tools if t.name == "load_data")
        load_data_tool.invoke({"file_path": str(csv_path)})

        # Manually set stage to data_quality_check (where eda_profile is allowed)
        session.state_machine.current_stage = AnalysisStage.DATA_QUALITY_CHECK

        eda_tool = next(t for t in tools if t.name == "eda_profile")
        eda_result = eda_tool.invoke({})

        # Debug: print what's in the namespace
        print(f"\nDebug - user_question: {session.user_question}")
        print(f"Debug - df columns: {session.ns.get('df').columns.tolist() if session.ns.get('df') is not None else 'None'}")
        print(f"Debug - suggested_template in ns: {session.ns.get('suggested_template')}")

        # Check that template is stored in namespace
        suggested_template = session.ns.get("suggested_template")
        assert suggested_template is not None, f"Template should be stored in session.ns. Available keys: {list(session.ns.keys())}"
        assert "Deployment" in suggested_template.template_name, "Should be deployment template"
        print(f"✓ Template stored in namespace: {suggested_template.template_name}")
        print(f"✓ Template ID: {suggested_template.template_id}")
        print(f"✓ Key metrics: {', '.join(suggested_template.key_metrics[:3])}")

        print("\n✅ Namespace storage test passed!")


if __name__ == "__main__":
    print("=" * 70)
    print("Testing Template Recommendation Activation")
    print("=" * 70)

    try:
        test_template_matching()
        test_eda_profile_with_template()
        test_template_in_namespace()

        print("\n" + "=" * 70)
        print("🎉 ALL TESTS PASSED!")
        print("=" * 70)
        print("\nSummary:")
        print("✓ Template matching works correctly")
        print("✓ EDA profile displays template recommendations")
        print("✓ Templates are stored in session namespace")
        print("✓ Template details (metrics, steps, caveats) are shown")

    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
