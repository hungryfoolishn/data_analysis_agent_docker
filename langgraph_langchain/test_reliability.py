#!/usr/bin/env python
# coding=utf-8
"""
后端可靠性测试 — 覆盖以下优化点：
  1. Session 持久化（重启后恢复）
  2. 并发控制（信号量限流）
  3. finish_report 结构化校验
  4. 日志文件生成
  5. 取消机制
"""
from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_CSV = PROJECT_ROOT / "temp_uploads" / "test.csv"


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Session 持久化
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionPersistence:
    """验证 session 在服务重启后能从磁盘恢复。"""

    def test_save_and_reload(self, tmp_path):
        """_save_sessions 写盘，_load_sessions 重新读取后 SESSIONS 内容一致。"""
        now = time.strftime("%Y-%m-%dT%H:%M:%S")

        # 隔离模块状态，使用 tmp_path 作为 workspace
        with patch("langgraph_langchain.api_server_langgraph.WORKSPACE_DIR", tmp_path), \
             patch("langgraph_langchain.api_server_langgraph._SESSIONS_FILE",
                   tmp_path / ".sessions.json"):
            from langgraph_langchain.api_server_langgraph import (
                SESSIONS, _save_sessions, _load_sessions,
            )
            SESSIONS.clear()

            sid = "test-persist-001"
            ws = tmp_path / sid
            ws.mkdir()
            SESSIONS[sid] = {
                "files": [],
                "artifacts": [{"name": "chart.png"}],
                "workspace": str(ws),
                "created_at": now,
                "last_accessed_at": now,
            }
            _save_sessions()

            # 清空内存，模拟重启
            SESSIONS.clear()
            assert sid not in SESSIONS

            _load_sessions()
            assert sid in SESSIONS
            assert SESSIONS[sid]["artifacts"] == [{"name": "chart.png"}]

    def test_load_skips_missing_workspace(self, tmp_path):
        """重启后，workspace 目录已删除的 session 不应被恢复。"""
        sessions_file = tmp_path / ".sessions.json"
        dead_sid = "dead-session"
        sessions_file.write_text(
            json.dumps({
                dead_sid: {
                    "files": [],
                    "artifacts": [],
                    "workspace": "/nonexistent/path/xyz",
                    "created_at": "2024-01-01T00:00:00",
                }
            }),
            encoding="utf-8",
        )
        with patch("langgraph_langchain.api_server_langgraph.WORKSPACE_DIR", tmp_path), \
             patch("langgraph_langchain.api_server_langgraph._SESSIONS_FILE", sessions_file):
            from langgraph_langchain.api_server_langgraph import SESSIONS, _load_sessions
            SESSIONS.clear()
            _load_sessions()
            assert dead_sid not in SESSIONS

    def test_load_skips_stale_workspace_and_prunes_directory(self, tmp_path):
        """重启后，超过 TTL 的 session 不应恢复，且其 workspace 应被清理。"""
        sessions_file = tmp_path / ".sessions.json"
        stale_sid = "stale-session"
        stale_ws = tmp_path / stale_sid
        stale_ws.mkdir()
        (stale_ws / "artifact.txt").write_text("old", encoding="utf-8")
        sessions_file.write_text(
            json.dumps({
                stale_sid: {
                    "files": [],
                    "artifacts": [],
                    "workspace": str(stale_ws),
                    "created_at": "2024-01-01T00:00:00",
                    "last_accessed_at": "2024-01-01T00:00:00",
                }
            }),
            encoding="utf-8",
        )
        with patch("langgraph_langchain.api_server_langgraph.WORKSPACE_DIR", tmp_path), \
             patch("langgraph_langchain.api_server_langgraph._SESSIONS_FILE", sessions_file), \
             patch("langgraph_langchain.api_server_langgraph._SESSION_TTL", timedelta(hours=24), create=True):
            from langgraph_langchain.api_server_langgraph import SESSIONS, _load_sessions
            SESSIONS.clear()
            _load_sessions()
            assert stale_sid not in SESSIONS
            assert not stale_ws.exists()

    def test_ensure_session_consistency_rejects_expired_session(self, tmp_path):
        """访问过期 session 时应返回 Session expired 并清理记录。"""
        from fastapi import HTTPException
        from langgraph_langchain.api_server_langgraph import SESSIONS, _ensure_session_consistency

        sid = "expired-session"
        ws = tmp_path / sid
        ws.mkdir()
        SESSIONS[sid] = {
            "files": [],
            "artifacts": [],
            "workspace": str(ws),
            "created_at": "2024-01-01T00:00:00",
            "last_accessed_at": "2024-01-01T00:00:00",
        }

        with patch("langgraph_langchain.api_server_langgraph._SESSION_TTL", timedelta(hours=24), create=True), \
             patch("langgraph_langchain.api_server_langgraph._save_sessions"):
            with pytest.raises(HTTPException) as exc_info:
                _ensure_session_consistency(sid)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["code"] == "session_expired"
        assert exc_info.value.detail["type"] == "session_error"
        assert exc_info.value.detail["retryable"] is False
        assert sid not in SESSIONS
        assert not ws.exists()

    def test_get_or_create_session_sets_last_accessed_at(self, tmp_path):
        """新建 session 时应写入 last_accessed_at，便于 TTL 清理。"""
        with patch("langgraph_langchain.api_server_langgraph.WORKSPACE_DIR", tmp_path), \
             patch("langgraph_langchain.api_server_langgraph._SESSIONS_FILE", tmp_path / ".sessions.json"):
            from langgraph_langchain.api_server_langgraph import SESSIONS, get_or_create_session
            SESSIONS.clear()
            sid, session = get_or_create_session("ttl-meta")
            assert sid == "ttl-meta"
            assert session["last_accessed_at"]
            assert session["created_at"]

    def test_is_report_rejected_accepts_current_finish_report_prefix(self):
        from langgraph_langchain.api_server_langgraph import _is_report_rejected

        assert _is_report_rejected("REPORT REJECTED. Fix the following before calling finish_report again:\n- issue") is True
        assert _is_report_rejected("[REPORT REJECTED]\nlegacy") is True
        assert _is_report_rejected("Report submitted successfully.") is False

    def test_workspace_file_response_rejects_path_traversal(self, tmp_path):
        secret = tmp_path / "secret.txt"
        secret.write_text("do not expose", encoding="utf-8")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "ok.txt").write_text("safe", encoding="utf-8")

        with patch("langgraph_langchain.api_server_langgraph.WORKSPACE_DIR", workspace):
            from fastapi import HTTPException
            from langgraph_langchain.api_server_langgraph import _workspace_file_response

            response = _workspace_file_response("ok.txt")
            assert Path(response.path) == (workspace / "ok.txt").resolve()

            with pytest.raises(HTTPException) as exc_info:
                _workspace_file_response("../secret.txt")
            assert exc_info.value.status_code == 404


class TestStructuredFailurePayloads:
    """验证 API 层 failure taxonomy 的结构化返回。"""

    def test_ensure_session_consistency_rejects_missing_session_with_structured_detail(self):
        from fastapi import HTTPException
        from langgraph_langchain.api_server_langgraph import SESSIONS, _ensure_session_consistency

        SESSIONS.clear()

        with pytest.raises(HTTPException) as exc_info:
            _ensure_session_consistency("missing-session")

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["code"] == "session_not_found"
        assert exc_info.value.detail["type"] == "session_error"
        assert exc_info.value.detail["retryable"] is False
        assert exc_info.value.detail["recovery_action"] == "user_action_required"
        assert "hint" in exc_info.value.detail

    def test_ensure_session_consistency_rejects_missing_workspace_with_structured_detail(self, tmp_path):
        from fastapi import HTTPException
        from langgraph_langchain.api_server_langgraph import SESSIONS, _ensure_session_consistency

        sid = "workspace-missing-session"
        SESSIONS[sid] = {
            "files": [],
            "artifacts": [],
            "workspace": str(tmp_path / sid),
            "created_at": "2024-01-01T00:00:00",
            "last_accessed_at": "2024-01-01T00:00:00",
        }

        with patch("langgraph_langchain.api_server_langgraph._save_sessions"):
            with pytest.raises(HTTPException) as exc_info:
                _ensure_session_consistency(sid)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["code"] == "session_workspace_missing"
        assert exc_info.value.detail["type"] == "session_error"
        assert exc_info.value.detail["retryable"] is False
        assert exc_info.value.detail["recovery_action"] == "user_action_required"
        assert sid not in SESSIONS

    def test_resolve_data_file_rejects_missing_upload_with_structured_detail(self):
        from fastapi import HTTPException
        from langgraph_langchain.api_server_langgraph import _resolve_data_file

        with pytest.raises(HTTPException) as exc_info:
            _resolve_data_file({"files": []}, None)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["code"] == "missing_data_file"
        assert exc_info.value.detail["type"] == "request_error"
        assert exc_info.value.detail["retryable"] is True
        assert exc_info.value.detail["recovery_action"] == "user_action_required"

    def test_resolve_data_file_rejects_nonexistent_path_with_structured_detail(self, tmp_path):
        from fastapi import HTTPException
        from langgraph_langchain.api_server_langgraph import _resolve_data_file

        missing_file = tmp_path / "missing.csv"

        with pytest.raises(HTTPException) as exc_info:
            _resolve_data_file({"files": []}, str(missing_file))

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["code"] == "missing_data_file"
        assert exc_info.value.detail["type"] == "request_error"
        assert exc_info.value.detail["recovery_action"] == "user_action_required"
        assert exc_info.value.detail["file_path"] == str(missing_file)

    def test_structured_failure_from_output_maps_report_rejection(self):
        from langgraph_langchain.api_server_langgraph import _structured_failure_from_output

        detail = _structured_failure_from_output(
            "REPORT REJECTED. Fix the following before calling finish_report again:\n"
            "- finish_report can only be called after the analysis reaches synthesis/final_report stage"
        )

        assert detail is not None
        assert detail["code"] == "report_rejected"
        assert detail["type"] == "analysis_error"
        assert detail["retryable"] is True
        assert detail["recovery_action"] == "retry_narrower_scope"
        assert detail["stage"] == "final_report"

    def test_structured_failure_from_output_maps_python_execution_error(self):
        from langgraph_langchain.api_server_langgraph import _structured_failure_from_output

        detail = _structured_failure_from_output(
            "[ERROR] repeated python_repl errors without recovery at deep_dive stage"
        )

        assert detail is not None
        assert detail["code"] == "python_execution_error"
        assert detail["stage"] == "deep_dive"
        assert detail["retryable"] is True
        assert detail["recovery_action"] == "retry_narrower_scope"

    def test_structured_failure_from_output_maps_max_steps_exceeded(self):
        from langgraph_langchain.api_server_langgraph import _structured_failure_from_output

        detail = _structured_failure_from_output(
            "analysis halted at synthesis stage because it exceeded the maximum tool-step budget"
        )

        assert detail is not None
        assert detail["code"] == "max_steps_exceeded"
        assert detail["stage"] == "synthesis"
        assert detail["retryable"] is True
        assert detail["recovery_action"] == "retry_narrower_scope"

    def test_structured_failure_from_output_maps_cancelled(self):
        from langgraph_langchain.api_server_langgraph import _structured_failure_from_output

        detail = _structured_failure_from_output(
            "analysis cancelled during deep_dive stage"
        )

        assert detail is not None
        assert detail["code"] == "cancelled"
        assert detail["stage"] == "deep_dive"
        assert detail["recovery_action"] == "retry_same_scope"
        assert detail["status_code"] == 499

    @pytest.mark.asyncio
    async def test_run_analysis_raises_structured_failure_for_python_execution_error(self, tmp_path):
        from langgraph_langchain import api_server_langgraph as srv

        session_id = "analysis-python-error"
        session = {"files": [], "artifacts": [], "workspace": str(tmp_path)}
        srv._agent_semaphore = asyncio.Semaphore(1)

        async def fake_stream(*args, **kwargs):
            yield "repeated python_repl errors without recovery", []

        with patch("langgraph_langchain.api_server_langgraph.run_analysis_stream", fake_stream):
            with pytest.raises(srv.AnalysisFailureError) as exc_info:
                await srv._run_analysis(session_id, session, "analyze", str(SAMPLE_CSV))

        assert exc_info.value.detail["code"] == "python_execution_error"
        assert exc_info.value.detail["type"] == "analysis_error"
        assert exc_info.value.detail["recovery_action"] == "retry_narrower_scope"

    @pytest.mark.asyncio
    async def test_run_analysis_raises_structured_failure_for_max_steps(self, tmp_path):
        from langgraph_langchain import api_server_langgraph as srv

        session_id = "analysis-max-steps"
        session = {"files": [], "artifacts": [], "workspace": str(tmp_path)}
        srv._agent_semaphore = asyncio.Semaphore(1)

        async def fake_stream(*args, **kwargs):
            yield "exceeded the maximum tool-step budget during synthesis stage", []

        with patch("langgraph_langchain.api_server_langgraph.run_analysis_stream", fake_stream):
            with pytest.raises(srv.AnalysisFailureError) as exc_info:
                await srv._run_analysis(session_id, session, "analyze", str(SAMPLE_CSV))

        assert exc_info.value.detail["code"] == "max_steps_exceeded"
        assert exc_info.value.detail["stage"] == "synthesis"
        assert exc_info.value.detail["recovery_action"] == "retry_narrower_scope"

class TestConcurrencyControl:
    """验证信号量正确限制并发 agent 数量。"""

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """同时发起 MAX+1 个协程，最多 MAX 个能同时持有信号量。"""
        max_concurrent = 2
        sem = asyncio.Semaphore(max_concurrent)
        active: list[int] = []
        peak: list[int] = []

        async def worker(i: int):
            async with sem:
                active.append(i)
                peak.append(len(active))
                await asyncio.sleep(0.05)
                active.remove(i)

        await asyncio.gather(*[worker(i) for i in range(max_concurrent + 2)])
        assert max(peak) <= max_concurrent

    @pytest.mark.asyncio
    async def test_startup_initialises_semaphore(self):
        """startup 事件后 _agent_semaphore 可正常使用。"""
        from langgraph_langchain import api_server_langgraph as srv
        await srv._startup()
        assert isinstance(srv._agent_semaphore, asyncio.Semaphore)
        # Should allow acquisition without blocking
        acquired = srv._agent_semaphore.locked() is False
        assert acquired


# ═══════════════════════════════════════════════════════════════════════════════
# 3. finish_report 结构化校验
# ═══════════════════════════════════════════════════════════════════════════════

class TestFinishReportValidation:
    """验证 finish_report 工具的质量门控。"""

    def _make_finish_report(self, tmp_path, *, ready_for_finish: bool = True):
        """创建一个真实的 finish_report 工具实例。"""
        from langgraph_langchain.langgraph_agent import _Session, _make_tools
        session = _Session(str(tmp_path), str(SAMPLE_CSV), session_id="test")
        if ready_for_finish:
            session.start_stage("synthesis")
        tools = _make_tools(session)
        finish = next(t for t in tools if t.name == "finish_report")
        return finish, session

    def _valid_report(self):
        return (
            "# Analysis Report\n\n"
            "## Summary\n"
            "本报告基于 12 个月销售数据，重点评估 revenue、orders 与 region 维度表现，并对异常月份进行验证。"
            "总体上，North 区域贡献最高，最近一个季度 revenue 较前一季度提升 18%，但 8 月出现短期波动。\n\n"
            "## Key Findings\n"
            "- North 区域 revenue 占比 42%，排名第 1，显著高于 South 的 27%。\n"
            "- 2025 年 Q3 orders 环比下降 12%，其中 8 月样本量仅 31 行，需要谨慎解释。\n"
            "- 图 1 显示 revenue 与 orders 同步变化，但这只是线索，不直接代表因果。\n\n"
            "## Data Quality\n"
            "缺失值主要集中在 discount 列，占比 6.5%；amount 列存在 3 个异常值，占样本 1.2%，已单独核查。\n\n"
            "## Analysis\n"
            "先按 region 与 month 做分组汇总，再检查趋势、环比、异常值与 top group 贡献。对于驱动因素，仅将相关性作为线索，并保留需进一步验证的部分。\n\n"
            "## Recommendations\n"
            "建议优先复盘 8 月渠道投放与库存变化，并继续跟踪 North 区域高贡献组是否可持续。\n"
        )

    def test_finish_report_rejects_before_synthesis_stage(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path, ready_for_finish=False)

        result = finish.invoke({"markdown": self._valid_report()})

        assert "REJECTED" in result
        assert "synthesis/final_report stage" in result
        assert session.report is None
        assert session.stage_failures
        assert session.stage_failures[-1].code == "report_rejected"
        assert session.stage_failures[-1].stage == "schema_understanding"
        assert session.stage_failures[-1].recovery_action == "retry_narrower_scope"

    def test_finish_report_accepts_after_synthesis_stage(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path, ready_for_finish=True)

        result = finish.invoke({"markdown": self._valid_report()})

        assert result == "Report submitted successfully."
        assert session.report == self._valid_report().strip()

    def test_rejects_no_headings(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        long_no_headings = "a" * 450
        result = finish.invoke({"markdown": long_no_headings})
        assert "REJECTED" in result
        assert session.report is None

    def test_rejects_missing_summary(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        md = (
            "# Report\n\n"
            "## Key Findings\n- Revenue increased 20% in North.\n\n"
            "## Data Quality\nMissing ratio is 3%.\n\n"
            "## Analysis\nCompared groups and months across 12 periods.\n\n"
            + "x" * 450
        )
        result = finish.invoke({"markdown": md})
        assert "REJECTED" in result
        assert session.report is None

    def test_rejects_missing_key_findings(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        md = (
            "# Report\n\n"
            "## Summary\nThis report reviews 12 months of sales across 4 regions with enough detail to support downstream analysis.\n\n"
            "## Data Quality\nMissing rate is 2% and one outlier group was found.\n\n"
            "## Analysis\nWe compared regions, months, and product groups using grouped aggregates and trend checks.\n\n"
            + "x" * 450
        )
        result = finish.invoke({"markdown": md})
        assert "REJECTED" in result
        assert session.report is None

    def test_rejects_key_findings_without_evidence_markers(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        md = self._valid_report().replace(
            "## Key Findings\n"
            "- North 区域 revenue 占比 42%，排名第 1，显著高于 South 的 27%。\n"
            "- 2025 年 Q3 orders 环比下降 12%，其中 8 月样本量仅 31 行，需要谨慎解释。\n"
            "- 图 1 显示 revenue 与 orders 同步变化，但这只是线索，不直接代表因果。\n\n",
            "## Key Findings\n"
            "- 各区域表现存在明显差异。\n"
            "- 整体趋势出现变化。\n"
            "- 有一些值得关注的现象。\n\n",
        )
        result = finish.invoke({"markdown": md})
        assert "REJECTED" in result
        assert "key findings" in result.lower()
        assert session.report is None

    def test_rejects_trend_claims_without_time_window(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        md = (
            self._valid_report()
            .replace("2025 年 Q3 orders 环比下降 12%，其中 8 月样本量仅 31 行，需要谨慎解释。", "orders 明显下降，但没有说明比较周期。")
            .replace("总体上，North 区域贡献最高，最近一个季度 revenue 较前一季度提升 18%，但 8 月出现短期波动。", "总体上 revenue 呈上升趋势，但没有给出时间窗口。")
            .replace("先按 region 与 month 做分组汇总，再检查趋势、环比、异常值与 top group 贡献。", "先按 region 做分组汇总，再检查趋势、异常值与 top group 贡献。")
        )
        result = finish.invoke({"markdown": md})
        assert "REJECTED" in result
        assert "time window" in result.lower() or "comparison period" in result.lower()
        assert session.report is None

    def test_rejects_data_quality_without_concrete_evidence(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        md = self._valid_report().replace(
            "缺失值主要集中在 discount 列，占比 6.5%；amount 列存在 3 个异常值，占样本 1.2%，已单独核查。",
            "数据质量总体可接受，但仍需持续关注潜在问题。",
        )
        result = finish.invoke({"markdown": md})
        assert "REJECTED" in result
        assert "data quality" in result.lower()
        assert session.report is None

    def test_rejects_strong_recommendation_without_evidence(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        md = (
            self._valid_report()
            .replace("North 区域 revenue 占比 42%，排名第 1，显著高于 South 的 27%。", "North 区域表现更好。")
            .replace("2025 年 Q3 orders 环比下降 12%，其中 8 月样本量仅 31 行，需要谨慎解释。", "订单出现变化。")
            .replace("图 1 显示 revenue 与 orders 同步变化，但这只是线索，不直接代表因果。", "图表提供了一些参考。")
            .replace("建议优先复盘 8 月渠道投放与库存变化，并继续跟踪 North 区域高贡献组是否可持续。", "建议立即全面推广当前策略并直接扩大预算投入。")
        )
        result = finish.invoke({"markdown": md})
        assert "REJECTED" in result
        assert "recommendations" in result.lower()
        assert session.report is None


    def test_rejects_ratio_claim_without_metric_definition(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        md = (
            self._valid_report()
            .replace("本报告基于 12 个月销售数据，重点评估 revenue、orders 与 region 维度表现，并对异常月份进行验证。总体上，North 区域贡献最高，最近一个季度 revenue 较前一季度提升 18%，但 8 月出现短期波动。",
                     "本报告基于 12 个月销售数据，重点评估 revenue、orders 与 region 维度表现。总体上转化率与 revenue share 同时改善。")
            .replace("## Data Quality\n缺失值主要集中在 discount 列，占比 6.5%；amount 列存在 3 个异常值，占样本 1.2%，已单独核查。",
                     "## Data Quality\n缺失值主要集中在 discount 列，占比 6.5%；amount 列存在 3 个异常值，占样本 1.2%，已单独核查。")
            .replace("- North 区域 revenue 占比 42%，排名第 1，显著高于 South 的 27%。",
                     "- North 区域 conversion rate 提升到 42%，share 也高于 South，但未说明分母与口径。")
        )
        result = finish.invoke({"markdown": md})
        assert "REJECTED" in result
        assert "missing_metric_definition" in result
        assert session.report is None

    def test_rejects_semantic_business_label_without_assumption_note(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        md = (
            self._valid_report()
            .replace("本报告基于 12 个月销售数据，重点评估 revenue、orders 与 region 维度表现，并对异常月份进行验证。总体上，North 区域贡献最高，最近一个季度 revenue 较前一季度提升 18%，但 8 月出现短期波动。",
                     "本报告基于 12 个月销售数据，重点评估新客、老客与 region 维度表现。总体上高价值用户贡献更高。")
            .replace("- North 区域 revenue 占比 42%，排名第 1，显著高于 South 的 27%。",
                     "- 新客 revenue 规模高于老客，且高价值用户贡献更高。")
            .replace("- 2025 年 Q3 orders 环比下降 12%，其中 8 月样本量仅 31 行，需要谨慎解释。",
                     "- 2025 年 Q3 orders 环比下降 12%，其中 8 月样本量仅 31 行。")
            .replace("先按 region 与 month 做分组汇总，再检查趋势、环比、异常值与 top group 贡献。对于驱动因素，仅将相关性作为线索，并保留需进一步验证的部分。",
                     "先按 region 与 month 做分组汇总，再检查趋势、环比、异常值与 top group 贡献。")
        )
        result = finish.invoke({"markdown": md})
        assert "REJECTED" in result
        assert "missing_semantic_assumption_note" in result
        assert session.report is None

    def test_rejects_definition_risk_without_explicit_caveat(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        md = (
            self._valid_report()
            .replace("缺失值主要集中在 discount 列，占比 6.5%；amount 列存在 3 个异常值，占样本 1.2%，已单独核查。",
                     "refund 字段与 duplicate order 风险会影响 revenue 解释，但报告没有给出明确边界说明。")
            .replace("总体上，North 区域贡献最高，最近一个季度 revenue 较前一季度提升 18%，但 8 月出现短期波动。",
                     "总体上 revenue 受 refund 影响，但报告仍直接给出业务结论。")
        )
        result = finish.invoke({"markdown": md})
        assert "REJECTED" in result
        assert "missing_definition_risk_note" in result
        assert session.report is None

    def test_rejects_non_validation_recommendation_without_quantified_support(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        md = (
            self._valid_report()
            .replace("总体上，North 区域贡献最高，最近一个季度 revenue 较前一季度提升 18%，但 8 月出现短期波动。", "总体上 North 区域贡献最高，但近期表现需要继续观察。")
            .replace("- North 区域 revenue 占比 42%，排名第 1，显著高于 South 的 27%。", "- North 区域表现较好。")
            .replace("- 2025 年 Q3 orders 环比下降 12%，其中 8 月样本量仅 31 行，需要谨慎解释。", "- 订单有一些波动。")
            .replace("先按 region 与 month 做分组汇总，再检查趋势、环比、异常值与 top group 贡献。对于驱动因素，仅将相关性作为线索，并保留需进一步验证的部分。", "先按 region 做分组汇总，并做基础异常检查。")
            .replace("建议优先复盘 8 月渠道投放与库存变化，并继续跟踪 North 区域高贡献组是否可持续。", "建议安排专项复盘，并考虑后续策略调整。")
        )
        result = finish.invoke({"markdown": md})
        assert "REJECTED" in result
        assert "recommendation_without_support" in result
        assert session.report is None

    def test_accepts_cautious_report_with_metric_note_and_validation_recommendation(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        md = (
            "# Analysis Report\n\n"
            "## Summary\n"
            "本报告基于 12 个月销售数据，重点评估 revenue、orders 与 region 维度表现。conversion rate 按已支付订单/访问会话口径计算，当前仅用于 exploratory 观察。\n\n"
            "## Key Findings\n"
            "- North 区域 revenue 占比 42%，排名第 1，显著高于 South 的 27%。\n"
            "- 2025 年 Q3 orders 环比下降 12%，其中 8 月样本量仅 31 行，需要谨慎解释。\n"
            "- 图 1 显示 revenue 与 orders 同步变化，但这只是线索，不直接代表因果。\n\n"
            "## Data Quality\n"
            "metric definition note: conversion rate 采用已支付订单/访问会话作为分母，且 order_id 仍需去重校验；discount 列缺失占比 6.5%。\n\n"
            "## Analysis\n"
            "先按 region 与 month 做分组汇总，再检查趋势、环比、异常值与 top group 贡献。对于新客/高价值用户等业务语义，仅作为假设标签使用，需进一步验证字段映射。\n\n"
            "## Recommendations\n"
            "建议先验证 8 月样本波动与渠道投放变化是否稳定存在，并继续观察 North 区域高贡献组在下一个时间窗口是否延续。\n"
        )
        result = finish.invoke({"markdown": md})
        assert result == "Report submitted successfully."
        assert session.report == md.strip()

    def test_rejects_charts_without_visualization_section(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        session.known_image_files.add("chart.png")
        md = self._valid_report().replace("## Recommendations", "## Appendix")
        result = finish.invoke({"markdown": md})
        assert "REJECTED" in result
        assert "Visualizations" in result or "图表说明" in result

    def test_rejects_visualization_section_without_supported_conclusion(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        md = (
            self._valid_report()
            .replace("- 图 1 显示 revenue 与 orders 同步变化，但这只是线索，不直接代表因果。\n", "- Revenue 与 orders 存在同步变化线索。\n")
            + "\n## Visualizations\n- 图 1、图 2、图 3。\n"
        )
        result = finish.invoke({"markdown": md})
        assert "REJECTED" in result
        assert "charts support" in result or "support" in result

    def test_rejects_vague_driver_claims(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        md = (
            self._valid_report()
            .replace("仅将相关性作为线索，并保留需进一步验证的部分。", "已经确认驱动原因来自渠道投放。")
            .replace("建议优先复盘 8 月渠道投放与库存变化，并继续跟踪 North 区域高贡献组是否可持续。", "建议立即按该驱动全面推广。")
        )
        result = finish.invoke({"markdown": md})
        assert "REJECTED" in result
        assert "uncertainty" in result or "uncertain" in result


    def test_rejects_driver_claim_without_evidence_level(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        md = self._valid_report().replace(
            "先按 region 与 month 做分组汇总，再检查趋势、环比、异常值与 top group 贡献。对于驱动因素，仅将相关性作为线索，并保留需进一步验证的部分。",
            "先按 region 与 month 做分组汇总，并判断下降主要由渠道变化驱动。"
        )
        result = finish.invoke({"markdown": md})
        assert "REJECTED" in result
        assert "missing_driver_evidence_level" in result
        assert session.report is None

    def test_rejects_action_recommendation_without_supporting_numbers(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        md = (
            self._valid_report()
            .replace("North 区域 revenue 占比 42%，排名第 1，显著高于 South 的 27%。", "North 区域表现更好。")
            .replace("2025 年 Q3 orders 环比下降 12%，其中 8 月样本量仅 31 行，需要谨慎解释。", "订单出现变化。")
            .replace(
                "建议优先复盘 8 月渠道投放与库存变化，并继续跟踪 North 区域高贡献组是否可持续。",
                "建议 should prioritize 渠道预算调整。"
            )
        )
        result = finish.invoke({"markdown": md})
        assert "REJECTED" in result
        assert "recommendation_without_support" in result
        assert session.report is None

    def test_rejects_driver_statement_without_group_contribution(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        md = self._valid_report().replace(
            "先按 region 与 month 做分组汇总，再检查趋势、环比、异常值与 top group 贡献。对于驱动因素，仅将相关性作为线索，并保留需进一步验证的部分。",
            "先按 region 与 month 做分组汇总，结论认为本轮下滑主要由渠道变化驱动；证据等级 B。"
        )
        result = finish.invoke({"markdown": md})
        assert "REJECTED" in result
        assert "driver_claim_without_contribution_breakdown" in result
        assert session.report is None

    def test_rejects_causal_claim_without_uncertainty_or_controls(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        md = self._valid_report().replace(
            "先按 region 与 month 做分组汇总，再检查趋势、环比、异常值与 top group 贡献。对于驱动因素，仅将相关性作为线索，并保留需进一步验证的部分。",
            "先按 region 与 month 做分组汇总，并确认 8 月 revenue 下滑是由渠道预算削减直接导致。"
        )
        result = finish.invoke({"markdown": md})
        assert "REJECTED" in result
        assert "causal_claim_without_boundary" in result
        assert session.report is None

    def test_accepts_explanatory_report_with_evidence_level_and_validation_recommendation(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        md = (
            "# Analysis Report\n\n"
            "## Summary\n"
            "本报告围绕 revenue 在 2025-08 相比 2025-07 的变化做解释性分析，结论用于业务复盘而非直接因果认定。\n\n"
            "## Key Findings\n"
            "- 2025-08 revenue 较 2025-07 下降 12%，其中 region=South 贡献 -9.8k，channel=Paid Search 贡献 -6.4k。\n"
            "- 该 driver 结论证据等级 B：有时间窗口、有分组贡献拆解，且在最近两个窗口方向一致，但仍需进一步验证。\n"
            "- 图 1 显示 Paid Search 与 revenue 同期下滑，这是相关线索，不直接代表因果。\n\n"
            "## Data Quality\n"
            "metric definition note: revenue 当前按订单入账口径统计，存在 refund 字段未完全映射的风险；缺失值主要集中在 discount 列，占比 6.5%。\n\n"
            "## Analysis\n"
            "先按 month 与 channel 做汇总，再做 contribution breakdown。主驱动是 channel=Paid Search 在 2025-08 相比 2025-07 贡献 -6.4k；证据等级 B；去掉头部组后方向仍一致，并在最近两个窗口保持同向。该表述仅限 observed contribution，不把其升级为根因。\n\n"
            "## Recommendations\n"
            "建议先验证 2025-08 Paid Search 下滑是否与投放节奏变化有关，再决定是否调整预算。\n\n"
            "## Visualizations\n"
            "- 图 1 支持 2025-07 到 2025-08 的 channel contribution 变化，其中 Paid Search 是主要负向 contributor。\n"
        )
        result = finish.invoke({"markdown": md})
        assert result == "Report submitted successfully."
        assert session.report == md.strip()


    def test_finish_report_rejects_missing_explanation_bundle_reference(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        session.ns["explanation_bundle"] = {
            "metric_decomposition": {
                "previous_period": "2025-07",
                "current_period": "2025-08",
            },
            "driver_ranking": [
                {"dimension": "channel", "group": "Paid Search", "score": 0.82, "evidence_level": "B"}
            ],
            "definition_risk": {"exploratory_only": False},
            "counterfactual_checks": [{"check": "alternate_window", "status": "stable"}],
            "recommendations": [{"type": "validation", "recommendation": "validate Paid Search decline", "evidence_level": "B"}],
        }
        md = (
            "# Analysis Report\n\n"
            "## Summary\n"
            "本报告围绕 revenue 变化做解释性分析。\n\n"
            "## Key Findings\n"
            "- revenue 下降 12%，但这里只笼统说由渠道驱动。\n"
            "- 结论证据等级 B，但未引用具体贡献对象。\n\n"
            "## Data Quality\n"
            "缺失值主要集中在 discount 列，占比 6.5%。\n\n"
            "## Analysis\n"
            "先按 month 与 channel 做汇总，主驱动是渠道变化驱动；证据等级 B。\n\n"
            "## Recommendations\n"
            "建议继续观察。\n"
        )
        result = finish.invoke({"markdown": md})
        assert "REJECTED" in result
        assert "missing_explanation_bundle_reference" in result

    def test_finish_report_accepts_report_that_references_explanation_bundle(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        session.ns["explanation_bundle"] = {
            "metric_decomposition": {
                "previous_period": "2025-07",
                "current_period": "2025-08",
            },
            "driver_ranking": [
                {"dimension": "channel", "group": "Paid Search", "score": 0.82, "evidence_level": "B"}
            ],
            "definition_risk": {"exploratory_only": True},
            "counterfactual_checks": [{"check": "alternate_window", "status": "stable"}],
            "recommendations": [{"type": "validation", "recommendation": "validate Paid Search decline", "evidence_level": "B"}],
        }
        md = (
            "# Analysis Report\n\n"
            "## Summary\n"
            "本报告围绕 revenue 在 2025-08 相比 2025-07 的变化做解释性分析，当前结论属于 exploratory 复盘，不做直接因果认定。\n\n"
            "## Key Findings\n"
            "- 2025-08 revenue 较 2025-07 下降 12%，其中 channel=Paid Search 是主要负向 contributor。\n"
            "- 对 Paid Search 的 driver 判断证据等级 B，当前仍属于需验证线索。\n\n"
            "## Data Quality\n"
            "metric definition note: revenue 口径可能受 refund 与 duplicate order 风险影响，因此解释需保留边界。\n\n"
            "## Analysis\n"
            "先按 month 与 channel 做汇总，再做 contribution breakdown。主驱动是 channel=Paid Search 在 2025-07 到 2025-08 的负向贡献；证据等级 B；多窗口下方向仍稳定，去掉头部组后结论仍基本一致。\n\n"
            "## Recommendations\n"
            "建议先验证 Paid Search 下滑是否稳定持续，再继续观察后续窗口。\n"
        )
        result = finish.invoke({"markdown": md})
        assert result == "Report submitted successfully."


    def test_finish_report_rejects_action_language_when_bundle_only_supports_validation(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        session.ns["explanation_bundle"] = {
            "metric_decomposition": {
                "metric": "revenue",
                "previous_period": "2025-07",
                "current_period": "2025-08",
                "top_negative_contributors": [{"dimension": "channel", "group": "Paid Search", "contribution": -6400}],
            },
            "driver_ranking": [
                {"dimension": "channel", "group": "Paid Search", "score": 0.82, "evidence_level": "B"}
            ],
            "definition_risk": {"exploratory_only": True},
            "counterfactual_checks": [{"check": "alternate_window", "status": "stable"}],
            "recommendations": [{"type": "validation", "recommendation": "validate Paid Search decline", "evidence_level": "B"}],
        }
        md = (
            "# Analysis Report\n\n"
            "## Summary\n"
            "本报告围绕 revenue 在 2025-08 相比 2025-07 的变化做解释性分析，当前结论属于 exploratory 复盘，不做直接因果认定。\n\n"
            "## Key Findings\n"
            "- 2025-08 revenue 较 2025-07 下降 12%，其中 channel=Paid Search 是主要负向 contributor。\n"
            "- 对 Paid Search 的 driver 判断证据等级 B，当前仍属于需验证线索。\n\n"
            "## Data Quality\n"
            "metric definition note: revenue 口径可能受 refund 与 duplicate order 风险影响；discount 缺失占比 6.5%。\n\n"
            "## Analysis\n"
            "先按 month 与 channel 做汇总，再做 contribution breakdown。主驱动是 channel=Paid Search 在 2025-07 到 2025-08 的负向贡献；证据等级 B；多窗口下方向仍稳定，去掉头部组后结论仍基本一致。\n\n"
            "## Recommendations\n"
            "建议 should prioritize 立即调整 Paid Search 预算。\n"
        )
        result = finish.invoke({"markdown": md})
        assert "REJECTED" in result
        assert "missing_explanation_bundle_reference" in result

    def test_finish_report_rejects_report_missing_metric_decomposition_window(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        session.ns["explanation_bundle"] = {
            "metric_decomposition": {
                "metric": "revenue",
                "previous_period": "2025-07",
                "current_period": "2025-08",
                "top_negative_contributors": [{"dimension": "channel", "group": "Paid Search", "contribution": -6400}],
            },
            "driver_ranking": [
                {"dimension": "channel", "group": "Paid Search", "score": 0.82, "evidence_level": "B"}
            ],
            "definition_risk": {"exploratory_only": False},
            "counterfactual_checks": [{"check": "alternate_window", "status": "stable"}],
            "recommendations": [{"type": "validation", "recommendation": "validate Paid Search decline", "evidence_level": "B"}],
        }
        md = (
            "# Analysis Report\n\n"
            "## Summary\n"
            "本报告围绕 revenue 变化做解释性分析，并把结论用于业务复盘。\n\n"
            "## Key Findings\n"
            "- revenue 下降 12%，其中 channel=Paid Search 是主要负向 contributor。\n"
            "- 对 Paid Search 的 driver 判断证据等级 B，当前仍属于需验证线索。\n\n"
            "## Data Quality\n"
            "缺失值主要集中在 discount 列，占比 6.5%。\n\n"
            "## Analysis\n"
            "先按 month 与 channel 做汇总，再做 contribution breakdown。主驱动是 channel=Paid Search 的负向贡献；证据等级 B；去掉头部组后方向仍一致。\n\n"
            "## Recommendations\n"
            "建议先验证 Paid Search 下滑是否稳定持续。\n"
        )
        result = finish.invoke({"markdown": md})
        assert "REJECTED" in result
        assert "comparison window" in result

    def test_finish_report_rejects_report_missing_top_driver_evidence_level_reference(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        session.ns["explanation_bundle"] = {
            "metric_decomposition": {
                "metric": "revenue",
                "previous_period": "2025-07",
                "current_period": "2025-08",
                "top_negative_contributors": [{"dimension": "channel", "group": "Paid Search", "contribution": -6400}],
            },
            "driver_ranking": [
                {"dimension": "channel", "group": "Paid Search", "score": 0.82, "evidence_level": "B"}
            ],
            "definition_risk": {"exploratory_only": False},
            "counterfactual_checks": [{"check": "alternate_window", "status": "stable"}],
            "recommendations": [{"type": "validation", "recommendation": "validate Paid Search decline", "evidence_level": "B"}],
        }
        md = (
            "# Analysis Report\n\n"
            "## Summary\n"
            "本报告围绕 revenue 在 2025-08 相比 2025-07 的变化做解释性分析。\n\n"
            "## Key Findings\n"
            "- 2025-08 revenue 较 2025-07 下降 12%，其中 channel=Paid Search 是主要负向 contributor。\n"
            "- Paid Search 当前仍属于需验证线索。\n\n"
            "## Data Quality\n"
            "缺失值主要集中在 discount 列，占比 6.5%。\n\n"
            "## Analysis\n"
            "先按 month 与 channel 做汇总，再做 contribution breakdown。主驱动是 channel=Paid Search 在 2025-07 到 2025-08 的负向贡献；去掉头部组后方向仍一致，并在最近两个窗口保持同向。\n\n"
            "## Recommendations\n"
            "建议先验证 Paid Search 下滑是否稳定持续。\n"
        )
        result = finish.invoke({"markdown": md})
        assert "REJECTED" in result
        assert "cite evidence level" in result

    def test_finish_report_rejects_report_missing_stability_reference_when_counterfactual_checks_exist(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        session.ns["explanation_bundle"] = {
            "metric_decomposition": {
                "metric": "revenue",
                "previous_period": "2025-07",
                "current_period": "2025-08",
                "top_negative_contributors": [{"dimension": "channel", "group": "Paid Search", "contribution": -6400}],
            },
            "driver_ranking": [
                {"dimension": "channel", "group": "Paid Search", "score": 0.82, "evidence_level": "B"}
            ],
            "definition_risk": {"exploratory_only": False},
            "counterfactual_checks": [{"check": "alternate_window", "status": "stable"}],
            "recommendations": [{"type": "validation", "recommendation": "validate Paid Search decline", "evidence_level": "B"}],
        }
        md = (
            "# Analysis Report\n\n"
            "## Summary\n"
            "本报告围绕 revenue 在 2025-08 相比 2025-07 的变化做解释性分析。\n\n"
            "## Key Findings\n"
            "- 2025-08 revenue 较 2025-07 下降 12%，其中 channel=Paid Search 是主要负向 contributor。\n"
            "- 对 Paid Search 的 driver 判断证据等级 B，当前仍属于需验证线索。\n\n"
            "## Data Quality\n"
            "缺失值主要集中在 discount 列，占比 6.5%。\n\n"
            "## Analysis\n"
            "先按 month 与 channel 做汇总，再做 contribution breakdown。主驱动是 channel=Paid Search 在 2025-07 到 2025-08 的负向贡献；证据等级 B。\n\n"
            "## Recommendations\n"
            "建议先验证 Paid Search 下滑是否稳定持续。\n"
        )
        result = finish.invoke({"markdown": md})
        assert "REJECTED" in result
        assert "missing_stability_check_for_explanation" in result

    def test_finish_report_accepts_report_with_explicit_bundle_backed_sections(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        session.ns["explanation_bundle"] = {
            "metric_decomposition": {
                "metric": "revenue",
                "previous_period": "2025-07",
                "current_period": "2025-08",
                "top_negative_contributors": [{"dimension": "channel", "group": "Paid Search", "contribution": -6400}],
                "top_positive_contributors": [{"dimension": "region", "group": "North", "contribution": 2200}],
            },
            "driver_ranking": [
                {"dimension": "channel", "group": "Paid Search", "score": 0.82, "evidence_level": "B"}
            ],
            "definition_risk": {"exploratory_only": True},
            "counterfactual_checks": [{"check": "alternate_window", "status": "stable"}],
            "recommendations": [{"type": "validation", "recommendation": "validate Paid Search decline", "evidence_level": "B"}],
        }
        md = (
            "# Analysis Report\n\n"
            "## Summary\n"
            "本报告围绕 revenue 在 2025-07 到 2025-08 的变化做解释性分析，当前结论属于 exploratory 复盘，不做直接因果认定。\n\n"
            "## Key Findings\n"
            "- 2025-08 revenue 较 2025-07 下降 12%，其中 channel=Paid Search 贡献 -6.4k，是主要负向 contributor。\n"
            "- 对 channel=Paid Search 的 driver 判断证据等级 B，当前仍属于需验证线索。\n\n"
            "## Data Quality\n"
            "metric definition note: revenue 口径可能受 refund 与 duplicate order 风险影响，因此当前解释需保留边界。\n\n"
            "## Analysis\n"
            "先按 month 与 channel 做汇总，再做 contribution breakdown。主驱动是 channel=Paid Search 在 2025-07 到 2025-08 的负向贡献；证据等级 B；去掉头部组后方向仍一致，并在 alternate window 下保持同向，因此当前结论更接近 observed contribution 而非根因。\n\n"
            "## Recommendations\n"
            "建议先验证 Paid Search 下滑是否稳定持续，再继续观察后续窗口。\n"
        )
        result = finish.invoke({"markdown": md})
        assert result == "Report submitted successfully."

    def test_finish_report_rejects_second_submission_after_success(self, tmp_path):
        finish, session = self._make_finish_report(tmp_path)
        md = self._valid_report()

        first = finish.invoke({"markdown": md})
        second = finish.invoke({"markdown": md})

        assert first == "Report submitted successfully."
        assert second == "Report already submitted. Do not call finish_report again."
        assert session.report == md.strip()

    def test_eda_profile_includes_report_contract_hints(self, tmp_path):
        import pandas as pd
        from langgraph_langchain.langgraph_agent import _Session, _make_tools

        csv_path = tmp_path / "contract_signal.csv"
        df = pd.DataFrame({
            "date": ["2025-07-01", "2025-07-01", "2025-08-01", "2025-08-01"],
            "channel": ["Organic", "Paid Search", "Organic", "Paid Search"],
            "revenue": [100, 80, 120, 70],
            "refund_amount": [0, 0, 0, 15],
            "order_id": ["A001", "A002", "A003", "A003"],
        })
        df.to_csv(csv_path, index=False)
        session = _Session(str(tmp_path), str(csv_path), session_id="contract-signal")
        session.ns["df"] = df
        tools = _make_tools(session)
        eda = next(t for t in tools if t.name == "eda_profile")

        result = eda.invoke({})

        assert "Report contract:" in result
        assert "Key Findings" in result or "Summary" in result
        assert "Recommendations" in result

    def test_eda_profile_filters_id_like_dimensions_from_explanation_bundle(self, tmp_path):
        import pandas as pd
        from langgraph_langchain.langgraph_agent import _Session, _make_tools

        csv_path = tmp_path / "id_like_signal.csv"
        df = pd.DataFrame({
            "date": ["2025-07-01", "2025-07-01", "2025-08-01", "2025-08-01"],
            "channel": ["Organic", "Paid Search", "Organic", "Paid Search"],
            "revenue": [100, 80, 120, 70],
            "refund_amount": [0, 0, 0, 15],
            "order_id": ["A001", "A002", "A003", "A003"],
        })
        df.to_csv(csv_path, index=False)
        session = _Session(str(tmp_path), str(csv_path), session_id="id-like-signal")
        session.ns["df"] = df
        tools = _make_tools(session)
        eda = next(t for t in tools if t.name == "eda_profile")

        eda.invoke({})

        bundle = session.ns["explanation_bundle"]
        assert bundle["candidate_dims"] == ["channel"]
        assert bundle["driver_ranking"]
        assert bundle["driver_ranking"][0]["dimension"] == "channel"
        assert all(item["dimension"] != "order_id" for item in bundle["driver_ranking"])

    def test_eda_profile_excludes_id_like_numeric_metrics_from_business_hints(self, tmp_path):
        import pandas as pd
        from langgraph_langchain.langgraph_agent import _Session, _make_tools

        csv_path = tmp_path / "id_numeric_metric.csv"
        df = pd.DataFrame({
            "date": ["2025-07-01", "2025-07-01", "2025-08-01", "2025-08-01"],
            "channel": ["Organic", "Paid Search", "Organic", "Paid Search"],
            "user_id": [1001, 1002, 1003, 1004],
            "revenue": [100, 80, 120, 70],
        })
        df.to_csv(csv_path, index=False)
        session = _Session(str(tmp_path), str(csv_path), session_id="id-metric-eda")
        session.ns["df"] = df
        tools = _make_tools(session)
        eda = next(t for t in tools if t.name == "eda_profile")

        result = eda.invoke({})
        bundle = session.ns["explanation_bundle"]

        assert "Excluded id-like numeric fields from metric analysis: user_id" in result
        assert bundle["primary_metric"] == "revenue"
        assert "user_id 最近一个周期" not in result

    def test_support_label_classifies_thin_support(self, tmp_path):
        session = self._make_session(tmp_path)
        support_label = session.ns["support_label"]

        assert support_label(4) == "very_thin"
        assert support_label(12) == "thin"
        assert support_label(40) == "limited"
        assert support_label(120) == "adequate"

    def test_eda_profile_downgrades_recent_trend_with_thin_window_support(self, tmp_path):
        import pandas as pd
        from langgraph_langchain.langgraph_agent import _Session, _make_tools

        csv_path = tmp_path / "thin_trend_support.csv"
        df = pd.DataFrame({
            "date": [
                "2025-07-01", "2025-07-02", "2025-07-03",
                "2025-08-01", "2025-08-02", "2025-08-03",
            ],
            "channel": ["Organic", "Organic", "Paid", "Organic", "Organic", "Paid"],
            "revenue": [100, 110, 90, 180, 170, 160],
        })
        df.to_csv(csv_path, index=False)
        session = _Session(str(tmp_path), str(csv_path), session_id="thin-trend-support")
        session.ns["df"] = df
        tools = _make_tools(session)
        eda = next(t for t in tools if t.name == "eda_profile")

        result = eda.invoke({})

        assert "前后窗口可用样本仅 3/3 行" in result
        assert "暂不宜升级为稳定趋势判断" in result

    def test_eda_profile_downgrades_imbalanced_category_when_head_support_is_thin(self, tmp_path):
        import pandas as pd
        from langgraph_langchain.langgraph_agent import _Session, _make_tools

        csv_path = tmp_path / "thin_category_support.csv"
        df = pd.DataFrame({
            "segment": ["A", "A", "A", "B"],
            "revenue": [100, 110, 90, 95],
        })
        df.to_csv(csv_path, index=False)
        session = _Session(str(tmp_path), str(csv_path), session_id="thin-category-support")
        session.ns["df"] = df
        tools = _make_tools(session)
        eda = next(t for t in tools if t.name == "eda_profile")

        result = eda.invoke({})

    def _make_session(self, tmp_path):
        from langgraph_langchain.langgraph_agent import _Session
        return _Session(str(tmp_path), str(SAMPLE_CSV), session_id="explain-test")


    def test_decompose_metric_change_returns_contributors(self, tmp_path):
        import pandas as pd

        session = self._make_session(tmp_path)
        df = pd.DataFrame({
            "date": [
                "2025-07-01", "2025-07-01", "2025-07-01",
                "2025-08-01", "2025-08-01", "2025-08-01",
            ],
            "region": ["North", "South", "East", "North", "South", "East"],
            "channel": ["Organic", "Paid", "Paid", "Organic", "Paid", "Paid"],
            "revenue": [100, 70, 50, 130, 20, 40],
        })

        result = session.ns["decompose_metric_change"](df, "revenue", "date", ["region", "channel"])

        assert result["metric"] == "revenue"
        assert result["previous_period"] == "2025-07"
        assert result["current_period"] == "2025-08"
        assert result["absolute_change"] == pytest.approx(-30.0)
        assert isinstance(result["coverage_ratio"], float)
        assert result["top_positive_contributors"]
        assert result["top_negative_contributors"]
        lead_negative = result["top_negative_contributors"][0]
        assert lead_negative["dimension"] in {"region", "channel"}
        assert lead_negative["contribution"] < 0

    def test_assess_evidence_level_returns_expected_grades(self, tmp_path):
        session = self._make_session(tmp_path)
        assess = session.ns["assess_evidence_level"]

        assert assess(
            has_quantitative_support=True,
            has_group_breakdown=True,
            has_time_window=True,
            cross_slice_consistent=True,
            relies_on_unobserved_assumption=False,
        ) == "A"
        assert assess(
            has_quantitative_support=True,
            has_group_breakdown=False,
            has_time_window=True,
            cross_slice_consistent=True,
            relies_on_unobserved_assumption=False,
        ) == "B"
        assert assess(
            has_quantitative_support=False,
            has_group_breakdown=False,
            has_time_window=False,
            cross_slice_consistent=False,
            relies_on_unobserved_assumption=True,
        ) == "C"

    def test_rank_driver_candidates_prioritizes_largest_contributor(self, tmp_path):
        import pandas as pd

        session = self._make_session(tmp_path)
        rank = session.ns["rank_driver_candidates"]
        df = pd.DataFrame({
            "date": [
                "2025-07-01", "2025-07-01", "2025-07-01",
                "2025-08-01", "2025-08-01", "2025-08-01",
            ],
            "region": ["North", "South", "East", "North", "South", "East"],
            "channel": ["Organic", "Paid", "Paid", "Organic", "Paid", "Paid"],
            "revenue": [100, 70, 50, 130, 20, 40],
        })

        ranked = rank(df, "revenue", "date", ["region", "channel"])

        assert ranked
        assert ranked[0]["dimension"] in {"region", "channel"}
        assert ranked[0]["group"] in {"South", "Paid"}
        assert session.ns["explanation_bundle"]["driver_ranking"] == ranked

    def test_rank_driver_candidates_skips_id_like_dimensions(self, tmp_path):
        import pandas as pd

        session = self._make_session(tmp_path)
        rank = session.ns["rank_driver_candidates"]
        df = pd.DataFrame({
            "date": ["2025-07-01", "2025-07-01", "2025-08-01", "2025-08-01"],
            "channel": ["Organic", "Paid Search", "Organic", "Paid Search"],
            "order_id": ["A001", "A002", "A003", "A003"],
            "revenue": [100, 80, 120, 70],
        })

        ranked = rank(df, "revenue", "date", ["channel", "order_id"])

        assert ranked
        assert ranked[0]["dimension"] == "channel"
        assert all(item["dimension"] != "order_id" for item in ranked)
    def test_check_metric_definition_risk_detects_ambiguity(self, tmp_path):
        import pandas as pd

        session = self._make_session(tmp_path)
        check_risk = session.ns["check_metric_definition_risk"]
        df = pd.DataFrame({
            "date": ["2025-07-01", "2025-08-15"],
            "revenue": [100, 80],
            "refund_amount": [0, 10],
            "order_id": ["A001", "A001"],
        })

        result = check_risk(df, "revenue", "date")

        assert result["has_refund_or_return_fields"] is True
        assert result["possible_duplicate_keys"] == ["order_id"]
        assert result["exploratory_only"] is True
        assert session.ns["explanation_bundle"]["definition_risk"] == result

    def test_generate_recommendation_candidates_downgrades_a_level_driver_with_low_concentration_score(self, tmp_path):
        session = self._make_session(tmp_path)
        session.ns["explanation_bundle"].update({
            "metric_decomposition": {"coverage_ratio": 0.8, "broad_based": False, "absolute_change": -10000},
            "counterfactual_checks": [{"check": "alternate_window", "status": "stable"}],
            "definition_risk": {"exploratory_only": False},
        })

        recommendations = session.ns["generate_recommendation_candidates"](
            findings=[],
            drivers=[{
                "driver": "channel=Paid Search decline",
                "dimension": "channel",
                "group": "Paid Search",
                "contribution": -500,
                "score": 0.42,
                "evidence_level": "A",
            }],
        )

        assert recommendations[0]["type"] == "validation"
        assert "validate" in recommendations[0]["recommendation"]

    def test_generate_recommendation_candidates_downgrades_a_level_driver_when_coverage_is_thin(self, tmp_path):
        session = self._make_session(tmp_path)
        session.ns["explanation_bundle"].update({
            "metric_decomposition": {"coverage_ratio": 0.45, "broad_based": False, "absolute_change": -10000},
            "counterfactual_checks": [{"check": "alternate_window", "status": "stable"}],
            "definition_risk": {"exploratory_only": False},
        })

        recommendations = session.ns["generate_recommendation_candidates"](
            findings=[],
            drivers=[{
                "driver": "channel=Paid Search decline",
                "dimension": "channel",
                "group": "Paid Search",
                "contribution": -6400,
                "score": 0.82,
                "evidence_level": "A",
            }],
        )

        assert recommendations[0]["type"] == "validation"
        assert recommendations[0]["evidence_level"] == "A"

    def test_generate_recommendation_candidates_blocks_action_when_definition_risk_is_exploratory(self, tmp_path):
        session = self._make_session(tmp_path)
        session.ns["explanation_bundle"].update({
            "metric_decomposition": {"coverage_ratio": 0.8, "broad_based": False, "absolute_change": -10000},
            "counterfactual_checks": [{"check": "alternate_window", "status": "stable"}],
            "definition_risk": {"exploratory_only": True},
        })

        recommendations = session.ns["generate_recommendation_candidates"](
            findings=[],
            drivers=[{
                "driver": "channel=Paid Search decline",
                "dimension": "channel",
                "group": "Paid Search",
                "contribution": -6400,
                "score": 0.82,
                "evidence_level": "A",
            }],
        )

        assert recommendations[0]["type"] == "validation"
        assert recommendations[0]["evidence_level"] == "A"

    def test_generate_recommendation_candidates_downgrades_a_level_driver_when_contribution_share_is_small(self, tmp_path):
        session = self._make_session(tmp_path)
        session.ns["explanation_bundle"].update({
            "metric_decomposition": {"coverage_ratio": 0.8, "broad_based": False, "absolute_change": -10000},
            "counterfactual_checks": [{"check": "alternate_window", "status": "stable"}],
            "definition_risk": {"exploratory_only": False},
        })

        recommendations = session.ns["generate_recommendation_candidates"](
            findings=[],
            drivers=[{
                "driver": "channel=Paid Search decline",
                "dimension": "channel",
                "group": "Paid Search",
                "contribution": -1200,
                "score": 0.91,
                "evidence_level": "A",
            }],
        )

        assert recommendations[0]["type"] == "validation"
        assert recommendations[0]["evidence_level"] == "A"

    def test_generate_recommendation_candidates_allows_action_only_with_stable_concentrated_a_level_driver(self, tmp_path):
        session = self._make_session(tmp_path)
        session.ns["explanation_bundle"].update({
            "metric_decomposition": {"coverage_ratio": 0.8, "broad_based": False, "absolute_change": -10000},
            "counterfactual_checks": [{"check": "alternate_window", "status": "stable"}],
            "definition_risk": {"exploratory_only": False},
        })

        recommendations = session.ns["generate_recommendation_candidates"](
            findings=[],
            drivers=[{
                "driver": "channel=Paid Search decline",
                "dimension": "channel",
                "group": "Paid Search",
                "contribution": -6400,
                "score": 0.82,
                "evidence_level": "A",
            }],
        )

        assert recommendations[0]["type"] == "action"
        assert "prioritize review" in recommendations[0]["recommendation"]

    def test_decompose_metric_change_updates_explanation_bundle(self, tmp_path):
        import pandas as pd

        session = self._make_session(tmp_path)
        df = pd.DataFrame({
            "date": ["2025-07-01", "2025-07-01", "2025-08-01", "2025-08-01"],
            "region": ["North", "South", "North", "South"],
            "revenue": [100, 70, 130, 20],
        })

        result = session.ns["decompose_metric_change"](df, "revenue", "date", ["region"])

        assert session.ns["explanation_bundle"]["metric_decomposition"] == result


class TestLogging:
    """验证 per-session 日志文件被正确创建。"""

    def test_logger_creates_log_file(self, tmp_path):
        from langgraph_langchain.langgraph_agent import _make_session_logger
        import logging

        logger = _make_session_logger(tmp_path, "sess-log-test")
        logger.info("test message")

        log_file = tmp_path / "agent_sess-log-test.log"
        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert "test message" in content

    def test_logger_idempotent(self, tmp_path):
        """多次调用 _make_session_logger 不应重复添加 handler。"""
        from langgraph_langchain.langgraph_agent import _make_session_logger
        import logging

        logger1 = _make_session_logger(tmp_path, "sess-idem")
        handler_count = len(logger1.handlers)
        logger2 = _make_session_logger(tmp_path, "sess-idem")
        assert len(logger2.handlers) == handler_count

    def test_session_has_logger(self, tmp_path):
        from langgraph_langchain.langgraph_agent import _Session
        import logging

        session = _Session(str(tmp_path), str(SAMPLE_CSV), session_id="sess-check")
        assert isinstance(session.logger, logging.Logger)
        log_file = tmp_path / "agent_sess-check.log"
        assert log_file.exists()


# ═══════════════════════════════════════════════════════════════════════════════
# 5. 取消机制
# ═══════════════════════════════════════════════════════════════════════════════

class TestCancellation:
    """验证取消 Event 能及时终止流式生成。"""

    @pytest.mark.asyncio
    async def test_cancel_event_stops_stream(self, tmp_path):
        """cancel_event.set() 后流应在下一个 checkpoint 停止并 yield 取消消息。"""
        from langgraph_langchain.langgraph_agent import run_analysis_stream

        cancel_event = asyncio.Event()
        chunks: list[str] = []

        # Mock LLM + agent to produce a steady stream of events
        fake_events = [
            {"event": "on_chat_model_stream", "name": "",
             "data": {"chunk": MagicMock(content="thinking...")}},
        ] * 20  # 20 events before cancel would normally stop it

        async def fake_astream_events(*args, **kwargs):
            for i, ev in enumerate(fake_events):
                if i == 3:
                    cancel_event.set()  # cancel after 3 events
                yield ev

        with patch("langgraph_langchain.langgraph_agent.create_react_agent") as mock_create:
            mock_agent = MagicMock()
            mock_agent.astream_events = fake_astream_events
            mock_create.return_value = mock_agent

            async for text, _ in run_analysis_stream(
                instruction="test",
                source_path=str(SAMPLE_CSV),
                workspace_dir=str(tmp_path),
                api_key="fake",
                model_id="fake",
                api_base="https://fake",
                session_id="cancel-test",
                cancel_event=cancel_event,
            ):
                chunks.append(text)

        assert any("cancelled" in c.lower() or "cancel" in c.lower() for c in chunks)
        # Should not have consumed all 20 events
        text_chunks = [c for c in chunks if "thinking" in c]
        assert len(text_chunks) < 20

    @pytest.mark.asyncio
    async def test_cancel_endpoint_sets_event(self):
        """POST /sessions/{id}/cancel 应设置 _ACTIVE_CANCELS 中的 Event。"""
        from langgraph_langchain.api_server_langgraph import (
            SESSIONS, _ACTIVE_CANCELS, cancel_session,
        )

        sid = "cancel-api-test"
        SESSIONS[sid] = {"files": [], "artifacts": [], "workspace": "/tmp", "created_at": ""}
        ev = asyncio.Event()
        _ACTIVE_CANCELS[sid] = ev

        result = await cancel_session(sid)
        assert result["success"] is True
        assert ev.is_set()

        # Cleanup
        del SESSIONS[sid]
        _ACTIVE_CANCELS.pop(sid, None)

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_session_raises_404(self):
        from fastapi import HTTPException
        from langgraph_langchain.api_server_langgraph import cancel_session, SESSIONS

        SESSIONS.pop("no-such-session", None)
        with pytest.raises(HTTPException) as exc_info:
            await cancel_session("no-such-session")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_cancel_no_active_analysis(self):
        """session 存在但没有正在运行的 agent 时，返回 success=False。"""
        from langgraph_langchain.api_server_langgraph import (
            SESSIONS, _ACTIVE_CANCELS, cancel_session,
        )

        sid = "cancel-idle-test"
        SESSIONS[sid] = {"files": [], "artifacts": [], "workspace": "/tmp", "created_at": ""}
        _ACTIVE_CANCELS.pop(sid, None)

        result = await cancel_session(sid)
        assert result["success"] is False

        del SESSIONS[sid]


# ═══════════════════════════════════════════════════════════════════════════════
# 6. EDA 模板（分析能力）
# ═══════════════════════════════════════════════════════════════════════════════
class TestEdaProfile:
    """测试 eda_profile 工具的自动分析流程覆盖：缺失值、描述统计、相关性、分布。"""

    def _make_eda(self, tmp_path):
        from langgraph_langchain.langgraph_agent import _Session, _make_tools
        from langgraph_langchain.state_machine import AnalysisStage
        import pandas as pd
        csv_path = tmp_path / "sample.csv"
        df = pd.DataFrame({"a": range(10), "b": range(10, 20), "c": [i*1.5 for i in range(10)]})
        df.to_csv(csv_path, index=False)
        session = _Session(str(tmp_path), str(csv_path), session_id="eda-test")
        session.ns["df"] = df
        session.state_machine.current_stage = AnalysisStage.BASIC_EDA
        session.state_machine.record_tool_use("load_data")
        tools = _make_tools(session)
        eda = next(t for t in tools if t.name == "eda_profile")
        return eda, session

    def test_eda_returns_shape_info(self, tmp_path):
        """eda_profile 输出应包含行列数信息。"""
        eda, _ = self._make_eda(tmp_path)
        result = eda.invoke({})
        assert "rows" in result or "Shape" in result

    def test_eda_returns_descriptive_stats(self, tmp_path):
        """eda_profile 应输出描述性统计（mean/std/min/max）。"""
        eda, _ = self._make_eda(tmp_path)
        result = eda.invoke({})
        assert "### Descriptive Statistics" in result or "mean" in result or "std" in result

    def test_eda_includes_analysis_signals_section(self, tmp_path):
        """eda_profile 应输出结构化的 analysis signals 小节。"""
        eda, _ = self._make_eda(tmp_path)
        result = eda.invoke({})
        assert "### Analysis Signals" in result

    def test_eda_includes_business_analysis_hints(self, tmp_path):
        """低基数分类列和数值列存在时应输出业务分析 hints。"""
        import pandas as pd
        from langgraph_langchain.langgraph_agent import _Session, _make_tools
        from langgraph_langchain.state_machine import AnalysisStage
        csv_path = tmp_path / "biz.csv"
        df = pd.DataFrame({
            "region": ["North", "South", "East", "West"] * 6,
            "revenue": [100, 90, 80, 70, 110, 95, 85, 60, 120, 98, 88, 75, 130, 100, 90, 80, 125, 102, 92, 83, 128, 105, 95, 86],
        })
        df.to_csv(csv_path, index=False)
        session = _Session(str(tmp_path), str(csv_path), session_id="eda-biz")
        session.ns["df"] = df
        session.state_machine.current_stage = AnalysisStage.BASIC_EDA
        session.state_machine.record_tool_use("load_data")
        tools = _make_tools(session)
        eda = next(t for t in tools if t.name == "eda_profile")
        result = eda.invoke({})
        assert "### Business Analysis Hints" in result
        assert "Top/Bottom group summary" in result or "contribution" in result.lower()

    def test_eda_flags_strong_correlations_as_non_causal_hints(self, tmp_path):
        """强相关输出应明确提示相关不等于因果。"""
        import pandas as pd
        from langgraph_langchain.langgraph_agent import _Session, _make_tools
        from langgraph_langchain.state_machine import AnalysisStage
        csv_path = tmp_path / "corr_hint.csv"
        df = pd.DataFrame({"x": range(30), "y": [v * 10 for v in range(30)], "z": range(30, 60)})
        df.to_csv(csv_path, index=False)
        session = _Session(str(tmp_path), str(csv_path), session_id="eda-corr-hint")
        session.ns["df"] = df
        session.state_machine.current_stage = AnalysisStage.BASIC_EDA
        session.state_machine.record_tool_use("load_data")
        tools = _make_tools(session)
        eda = next(t for t in tools if t.name == "eda_profile")
        result = eda.invoke({})
        assert "### Strong Correlations" in result
        assert "因果" in result or "causal" in result.lower()

    def test_eda_reports_missing_values(self, tmp_path):
        """eda_profile 应报告缺失值情况。"""
        import pandas as pd
        from langgraph_langchain.langgraph_agent import _Session, _make_tools
        from langgraph_langchain.state_machine import AnalysisStage
        csv_path = tmp_path / "missing.csv"
        df = pd.DataFrame({"a": [1, None, 3], "b": [4, 5, None], "c": [7, 8, 9]})
        df.to_csv(csv_path, index=False)
        session = _Session(str(tmp_path), str(csv_path), session_id="eda-missing")
        session.ns["df"] = df
        # Manually set stage and record load_data as used (bypass state machine for unit test)
        session.state_machine.current_stage = AnalysisStage.BASIC_EDA
        session.state_machine.record_tool_use("load_data")
        tools = _make_tools(session)
        eda = next(t for t in tools if t.name == "eda_profile")
        result = eda.invoke({})
        assert "Missing" in result

    def test_eda_no_missing_values_reports_none(self, tmp_path):
        """无缺失值时 eda_profile 应明确报告 'none'。"""
        import pandas as pd
        from langgraph_langchain.langgraph_agent import _Session, _make_tools
        from langgraph_langchain.state_machine import AnalysisStage
        csv_path = tmp_path / "clean.csv"
        df = pd.DataFrame({"x": [1, 2, 3], "y": [4, 5, 6]})
        df.to_csv(csv_path, index=False)
        session = _Session(str(tmp_path), str(csv_path), session_id="eda-clean")
        session.ns["df"] = df
        session.state_machine.current_stage = AnalysisStage.BASIC_EDA
        session.state_machine.record_tool_use("load_data")
        tools = _make_tools(session)
        eda = next(t for t in tools if t.name == "eda_profile")
        result = eda.invoke({})
        assert "none" in result.lower() or "Missing Values: none" in result

    def test_eda_generates_correlation_chart(self, tmp_path):
        """多数值列时应生成相关性热图文件。"""
        import pandas as pd
        from langgraph_langchain.langgraph_agent import _Session, _make_tools
        from langgraph_langchain.state_machine import AnalysisStage
        csv_path = tmp_path / "corr.csv"
        df = pd.DataFrame({"a": range(20), "b": range(20, 40), "c": [i*2 for i in range(20)]})
        df.to_csv(csv_path, index=False)
        session = _Session(str(tmp_path), str(csv_path), session_id="eda-corr")
        session.ns["df"] = df
        session.state_machine.current_stage = AnalysisStage.BASIC_EDA
        session.state_machine.record_tool_use("load_data")
        tools = _make_tools(session)
        eda = next(t for t in tools if t.name == "eda_profile")
        eda.invoke({})
        chart_files = list(tmp_path.glob("eda_correlation_heatmap.png"))
        assert len(chart_files) == 1, "应生成相关性热图 PNG"

    def test_eda_generates_distribution_charts(self, tmp_path):
        """数值列应生成分布图文件。"""
        import pandas as pd
        from langgraph_langchain.langgraph_agent import _Session, _make_tools
        from langgraph_langchain.state_machine import AnalysisStage
        csv_path = tmp_path / "dist.csv"
        df = pd.DataFrame({"val": [1,2,3,4,5,6,7,8,9,10]})
        df.to_csv(csv_path, index=False)
        session = _Session(str(tmp_path), str(csv_path), session_id="eda-dist")
        session.ns["df"] = df
        session.state_machine.current_stage = AnalysisStage.BASIC_EDA
        session.state_machine.record_tool_use("load_data")
        tools = _make_tools(session)
        eda = next(t for t in tools if t.name == "eda_profile")
        eda.invoke({})
        dist_files = list(tmp_path.glob("eda_distributions.png"))
        assert len(dist_files) == 1, "应生成分布图 PNG"

    def test_eda_registers_artifacts(self, tmp_path):
        """生成的图表应注册到 session.new_artifacts。"""
        import pandas as pd
        from langgraph_langchain.langgraph_agent import _Session, _make_tools
        from langgraph_langchain.state_machine import AnalysisStage
        csv_path = tmp_path / "art.csv"
        df = pd.DataFrame({"a": range(10), "b": range(10, 20), "c": range(20, 30)})
        df.to_csv(csv_path, index=False)
        session = _Session(str(tmp_path), str(csv_path), session_id="eda-art")
        session.ns["df"] = df
        session.state_machine.current_stage = AnalysisStage.BASIC_EDA
        session.state_machine.record_tool_use("load_data")
        tools = _make_tools(session)
        eda = next(t for t in tools if t.name == "eda_profile")
        eda.invoke({})
        assert len(session.new_artifacts) > 0, "应至少注册一个图表 artifact"
        names = [a["name"] for a in session.new_artifacts]
        assert any(".png" in n for n in names)

    def test_eda_no_data_loaded_returns_error(self, tmp_path):
        """未先调用 load_data（df 不在 namespace）时应返回错误而不是崩溃。"""
        from langgraph_langchain.langgraph_agent import _Session, _make_tools
        from langgraph_langchain.state_machine import AnalysisStage
        session = _Session(str(tmp_path), str(SAMPLE_CSV), session_id="eda-nodata")
        session.state_machine.current_stage = AnalysisStage.BASIC_EDA
        session.state_machine.record_tool_use("load_data")
        tools = _make_tools(session)
        eda = next(t for t in tools if t.name == "eda_profile")
        result = eda.invoke({})
        assert "error" in result.lower() or "ERROR" in result

    def test_eda_detects_outliers(self, tmp_path):
        """含明显异常值的数据集应在 EDA 输出中包含 Outlier 信息。"""
        from langgraph_langchain.langgraph_agent import _Session, _make_tools
        from langgraph_langchain.state_machine import AnalysisStage
        import pandas as pd
        import numpy as np
        csv_path = tmp_path / "outlier.csv"
        normal = list(range(50))
        df = pd.DataFrame({"val": normal + [1000, -1000], "grp": ["a"] * 52})
        df.to_csv(csv_path, index=False)
        session = _Session(str(tmp_path), str(csv_path), session_id="out-test")
        session.state_machine.current_stage = AnalysisStage.BASIC_EDA
        session.state_machine.record_tool_use("load_data")
        session.ns["df"] = df
        tools = _make_tools(session)
        eda = next(t for t in tools if t.name == "eda_profile")
        result = eda.invoke({})
        assert "Outlier" in result or "outlier" in result
        assert (tmp_path / "eda_outliers.png").exists()

    def test_eda_cat_balance(self, tmp_path):
        """高度不均衡分类列应标记 imbalanced。"""
        from langgraph_langchain.langgraph_agent import _Session, _make_tools
        from langgraph_langchain.state_machine import AnalysisStage
        import pandas as pd
        csv_path = tmp_path / "imbal.csv"
        df = pd.DataFrame({"cat": ["A"] * 95 + ["B"] * 5, "val": range(100)})
        df.to_csv(csv_path, index=False)
        session = _Session(str(tmp_path), str(csv_path), session_id="bal-test")
        session.ns["df"] = df
        session.state_machine.current_stage = AnalysisStage.BASIC_EDA
        session.state_machine.record_tool_use("load_data")
        tools = _make_tools(session)
        eda = next(t for t in tools if t.name == "eda_profile")
        result = eda.invoke({})
        assert "imbalanced" in result

    def test_eda_cat_vs_num_chart(self, tmp_path):
        """低基数分类列 × 数值列应生成 boxplot 图。"""
        from langgraph_langchain.langgraph_agent import _Session, _make_tools
        from langgraph_langchain.state_machine import AnalysisStage
        import pandas as pd
        csv_path = tmp_path / "cross.csv"
        df = pd.DataFrame({"group": ["A", "B", "C"] * 20, "score": range(60)})
        df.to_csv(csv_path, index=False)
        session = _Session(str(tmp_path), str(csv_path), session_id="cross-test")
        session.ns["df"] = df
        session.state_machine.current_stage = AnalysisStage.BASIC_EDA
        session.state_machine.record_tool_use("load_data")
        tools = _make_tools(session)
        eda = next(t for t in tools if t.name == "eda_profile")
        eda.invoke({})
        charts = list(tmp_path.glob("eda_cat_vs_num_*.png"))
        assert len(charts) >= 1

    def test_python_repl_has_save_fig(self, tmp_path):
        """save_fig 应在 python_repl namespace 中可调用。"""
        from langgraph_langchain.langgraph_agent import _Session, _make_tools
        from langgraph_langchain.state_machine import AnalysisStage
        import pandas as pd
        csv_path = tmp_path / "x.csv"
        pd.DataFrame({"a": [1]}).to_csv(csv_path, index=False)
        session = _Session(str(tmp_path), str(csv_path), session_id="savefig-test")
        session.state_machine.current_stage = AnalysisStage.DEEP_DIVE
        session.state_machine.record_tool_use("load_data")
        session.state_machine.record_tool_use("eda_profile")
        tools = _make_tools(session)
        repl = next(t for t in tools if t.name == "python_repl")
        result = repl.invoke({
            "code": (
                "print('step objective: create a simple chart')\n"
                "print('method: plot a tiny line chart and save it')\n"
                "import matplotlib.pyplot as plt\n"
                "plt.plot([1,2])\n"
                "save_fig('test.png')\n"
                "print('key results: chart saved')\n"
                "print('suggested next step: inspect the file')"
            )
        })
        assert "Saved" in result
        assert (tmp_path / "test.png").exists()

    def test_python_repl_has_fix_chinese(self, tmp_path):
        """fix_chinese 应在 python_repl namespace 中可调用且不报错。"""
        from langgraph_langchain.langgraph_agent import _Session, _make_tools
        from langgraph_langchain.state_machine import AnalysisStage
        import pandas as pd
        csv_path = tmp_path / "x.csv"
        pd.DataFrame({"a": [1]}).to_csv(csv_path, index=False)
        session = _Session(str(tmp_path), str(csv_path), session_id="fixcn-test")
        session.state_machine.current_stage = AnalysisStage.DEEP_DIVE
        session.state_machine.record_tool_use("load_data")
        session.state_machine.record_tool_use("eda_profile")
        tools = _make_tools(session)
        repl = next(t for t in tools if t.name == "python_repl")
        result = repl.invoke({
            "code": (
                "print('step objective: verify chinese font helper')\n"
                "print('method: call fix_chinese and print a marker')\n"
                "fix_chinese()\n"
                "print('key results: ok')\n"
                "print('suggested next step: render a chart if needed')"
            )
        })
        assert "ERROR" not in result
        assert "ok" in result

    def test_fix_chinese_updates_matplotlib_rcparams(self, tmp_path):
        """fix_chinese 应设置 unicode_minus 并保留可用字体族配置。"""
        from langgraph_langchain.langgraph_agent import _Session, _make_tools
        from langgraph_langchain.state_machine import AnalysisStage
        import pandas as pd
        import matplotlib as mpl

        csv_path = tmp_path / "font.csv"
        pd.DataFrame({"a": [1]}).to_csv(csv_path, index=False)
        session = _Session(str(tmp_path), str(csv_path), session_id="fontcfg-test")
        session.state_machine.current_stage = AnalysisStage.DEEP_DIVE
        session.state_machine.record_tool_use("load_data")
        session.state_machine.record_tool_use("eda_profile")
        tools = _make_tools(session)
        repl = next(t for t in tools if t.name == "python_repl")

        result = repl.invoke({
            "code": (
                "print('step objective: verify matplotlib rcparams update')\n"
                "print('method: call fix_chinese and inspect configuration marker')\n"
                "fix_chinese()\n"
                "print('key results: configured')\n"
                "print('suggested next step: draw a chinese-labeled chart if needed')"
            )
        })

        assert "ERROR" not in result
        assert "configured" in result
        assert mpl.rcParams["axes.unicode_minus"] is False
        assert mpl.rcParams["font.family"]




class TestPythonReplRuntimeValidation:
    """验证 python_repl 的运行时小步约束。"""

    def _make_repl(self, tmp_path):
        from langgraph_langchain.langgraph_agent import _Session, _make_tools
        from langgraph_langchain.state_machine import AnalysisStage
        import pandas as pd

        csv_path = tmp_path / "runtime_guard.csv"
        pd.DataFrame({"a": [1, 2, 3]}).to_csv(csv_path, index=False)
        session = _Session(str(tmp_path), str(csv_path), session_id="runtime-guard")
        session.state_machine.current_stage = AnalysisStage.DEEP_DIVE
        session.state_machine.record_tool_use("load_data")
        session.state_machine.record_tool_use("eda_profile")
        tools = _make_tools(session)
        repl = next(t for t in tools if t.name == "python_repl")
        return repl

    def test_python_repl_rejects_missing_step_markers(self, tmp_path):
        repl = self._make_repl(tmp_path)

        result = repl.invoke({"code": "print('hello world')"})

        assert result.startswith("[ERROR]")
        assert "missing required printed markers" in result
        assert "step objective" in result

    def test_python_repl_rejects_oversized_step(self, tmp_path):
        repl = self._make_repl(tmp_path)

        body = "\n".join([f"x{i} = {i}" for i in range(55)])
        code = (
            "print('step objective: oversized step')\n"
            "print('method: too much code in one step')\n"
            f"{body}\n"
            "print('key results: done')\n"
            "print('suggested next step: split it')"
        )
        result = repl.invoke({"code": code})

        assert result.startswith("[ERROR]")
        assert "too large" in result
        assert "one sub-goal at a time" in result

    def test_python_repl_accepts_step_within_runtime_line_limit(self, tmp_path):
        repl = self._make_repl(tmp_path)

        body = "\n".join([f"x{i} = {i}" for i in range(40)])
        code = (
            "print('step objective: bounded step')\n"
            "print('method: validate runtime line limit')\n"
            f"{body}\n"
            "print('key results: step stays within guardrail')\n"
            "print('suggested next step: continue analysis')"
        )
        result = repl.invoke({"code": code})

        assert "[ERROR]" not in result
        assert "bounded step" in result

    def test_python_repl_accepts_structured_small_step(self, tmp_path):
        repl = self._make_repl(tmp_path)

        code = (
            "print('step objective: inspect dataset size')\n"
            "print('method: use the preloaded dataframe if available')\n"
            "print('key results: rows=', 3)\n"
            "print('suggested next step: check missing values')"
        )
        result = repl.invoke({"code": code})

        assert "[ERROR]" not in result
        assert "step objective: inspect dataset size" in result
        assert "suggested next step: check missing values" in result

    def test_python_repl_accepts_chinese_step_markers(self, tmp_path):
        repl = self._make_repl(tmp_path)

        code = (
            "print('步骤目标：检查数据行数')\n"
            "print('方法：直接输出预加载数据的行数')\n"
            "print('关键结果：rows=', 3)\n"
            "print('建议下一步：检查缺失值')"
        )
        result = repl.invoke({"code": code})

        assert "[ERROR]" not in result
        assert "步骤目标：检查数据行数" in result
        assert "建议下一步：检查缺失值" in result


class TestStreamFormatting:
    """验证流式步骤与输出保留完整换行结构且不截断。"""

    @pytest.mark.asyncio
    async def test_python_repl_preview_preserves_newlines(self, tmp_path):
        from langgraph_langchain.langgraph_agent import run_analysis_stream

        long_code = (
            "import pandas as pd\n"
            "print('hello')\n"
            "print('world')\n"
            "print('line4')\n"
            "print('line5')\n"
            "print('line6')\n"
            "print('line7')\n"
            "print('line8')\n"
            "print('line9')\n"
        )
        long_output = "\n".join([f"line{i}" for i in range(1, 12)])
        events = [
            {
                "event": "on_tool_start",
                "name": "python_repl",
                "data": {"input": {"code": long_code}},
            },
            {
                "event": "on_tool_end",
                "name": "python_repl",
                "data": {"output": long_output},
            },
        ]

        async def fake_astream_events(*args, **kwargs):
            for event in events:
                yield event

        chunks: list[str] = []
        with patch("langgraph_langchain.langgraph_agent.create_react_agent") as mock_create:
            mock_agent = MagicMock()
            mock_agent.astream_events = fake_astream_events
            mock_create.return_value = mock_agent

            async for text, _ in run_analysis_stream(
                instruction="test",
                source_path=str(SAMPLE_CSV),
                workspace_dir=str(tmp_path),
                api_key="fake",
                model_id="fake",
                api_base="https://fake",
                session_id="fmt-python-repl",
            ):
                if text:
                    chunks.append(text)

        combined = "".join(chunks)
        assert "```python" in combined
        assert long_code.strip() in combined
        assert "> `out`" in combined
        assert long_output in combined
        assert "content=" not in combined
        assert "tool_call_id=" not in combined
        assert "..." not in combined

    @pytest.mark.asyncio
    async def test_eda_profile_done_uses_multiline_preview(self, tmp_path):
        from langgraph_langchain.langgraph_agent import run_analysis_stream

        eda_output = (
            "## EDA Profile\n"
            "- Shape: 12 rows × 4 cols\n"
            "- Numeric: 2, Categorical: 2\n\n"
            "### Missing Values: none\n"
            "### Descriptive Statistics\n"
            "id score\n"
            "count 12 12\n"
            "mean 6.5 91.2"
        )
        events = [
            {
                "event": "on_tool_end",
                "name": "eda_profile",
                "data": {"output": eda_output},
            },
        ]

        async def fake_astream_events(*args, **kwargs):
            for event in events:
                yield event

        chunks: list[str] = []
        with patch("langgraph_langchain.langgraph_agent.create_react_agent") as mock_create:
            mock_agent = MagicMock()
            mock_agent.astream_events = fake_astream_events
            mock_create.return_value = mock_agent

            async for text, _ in run_analysis_stream(
                instruction="test",
                source_path=str(SAMPLE_CSV),
                workspace_dir=str(tmp_path),
                api_key="fake",
                model_id="fake",
                api_base="https://fake",
                session_id="fmt-eda",
            ):
                if text:
                    chunks.append(text)


    @pytest.mark.asyncio
    async def test_run_analysis_stream_stops_after_first_successful_finish_report(self, tmp_path):
        from langgraph_langchain.langgraph_agent import run_analysis_stream

        report = (
            "# Analysis Report\n\n"
            "## Summary\n"
            "本报告总结 revenue 与 region 的表现，并保留证据边界。North 贡献最高，最新窗口较前窗口改善 10%。\n\n"
            "## Key Findings\n"
            "- North 区域 revenue 占比 42%，高于 South 的 27%。\n"
            "- 2025-08 revenue 较 2025-07 提升 10%，但样本规模有限，需谨慎解释。\n"
            "- 图表仅支持相关线索，不直接代表因果。\n\n"
            "## Data Quality\n"
            "discount 缺失占比 6.5%，amount 有 3 个异常值，已在分析中单独关注。\n\n"
            "## Analysis\n"
            "先按 month 与 region 做汇总，再比较趋势与贡献拆解；驱动判断保留为待验证线索，不直接升级为根因。\n\n"
            "## Recommendations\n"
            "建议继续观察 North 高贡献组，并复核最近窗口变化是否稳定。\n"
        )
        events = [
            {"event": "on_tool_start", "name": "finish_report", "data": {"input": {"markdown": report}}},
            {"event": "on_tool_end", "name": "finish_report", "data": {"output": "Report submitted successfully."}},
            {"event": "on_tool_start", "name": "finish_report", "data": {"input": {"markdown": report}}},
            {"event": "on_tool_end", "name": "finish_report", "data": {"output": "Report already submitted. Do not call finish_report again."}},
        ]

        async def fake_astream_events(*args, **kwargs):
            for event in events:
                yield event

        chunks: list[str] = []
        with patch("langgraph_langchain.langgraph_agent.create_react_agent") as mock_create:
            mock_agent = MagicMock()
            mock_agent.astream_events = fake_astream_events
            mock_create.return_value = mock_agent

            async for text, _ in run_analysis_stream(
                instruction="test",
                source_path=str(SAMPLE_CSV),
                workspace_dir=str(tmp_path),
                api_key="fake",
                model_id="fake",
                api_base="https://fake",
                session_id="finish-once",
            ):
                if text:
                    chunks.append(text)

        combined = "".join(chunks)
        assert combined.count("# Analysis Report") == 1
        assert "Report already submitted" not in combined

    @pytest.mark.asyncio
    async def test_run_analysis_stream_stops_after_repeated_python_errors(self, tmp_path):
        from langgraph_langchain.langgraph_agent import run_analysis_stream

        events = [
            {"event": "on_tool_start", "name": "python_repl", "data": {"input": {"code": "print('step objective: a')\nprint('method: b')\nprint('key results: c')\nprint('suggested next step: d')"}}},
            {"event": "on_tool_end", "name": "python_repl", "data": {"output": "[ERROR]\nfirst failure"}},
            {"event": "on_tool_start", "name": "python_repl", "data": {"input": {"code": "print('step objective: a')\nprint('method: b')\nprint('key results: c')\nprint('suggested next step: d')"}}},
            {"event": "on_tool_end", "name": "python_repl", "data": {"output": "[ERROR]\nsecond failure"}},
            {"event": "on_tool_start", "name": "python_repl", "data": {"input": {"code": "print('step objective: a')\nprint('method: b')\nprint('key results: c')\nprint('suggested next step: d')"}}},
            {"event": "on_tool_end", "name": "python_repl", "data": {"output": "[ERROR]\nthird failure"}},
            {"event": "on_tool_start", "name": "finish_report", "data": {"input": {}}},
        ]

        async def fake_astream_events(*args, **kwargs):
            for event in events:
                yield event

        chunks: list[str] = []
        with patch("langgraph_langchain.langgraph_agent.create_react_agent") as mock_create:
            mock_agent = MagicMock()
            mock_agent.astream_events = fake_astream_events
            mock_create.return_value = mock_agent

            async for text, _ in run_analysis_stream(
                instruction="test",
                source_path=str(SAMPLE_CSV),
                workspace_dir=str(tmp_path),
                api_key="fake",
                model_id="fake",
                api_base="https://fake",
                session_id="stream-stop-errors",
            ):
                if text:
                    chunks.append(text)

        combined = "".join(chunks)
        assert "repeated python_repl errors without recovery" in combined
        assert "finish_report" not in combined

    @pytest.mark.asyncio
    async def test_run_analysis_stream_stops_when_step_budget_exceeded(self, tmp_path):
        from langgraph_langchain.langgraph_agent import run_analysis_stream

        events = []
        for idx in range(26):
            events.append({
                "event": "on_tool_start",
                "name": "load_data",
                "data": {"input": {"file_path": str(SAMPLE_CSV), "seq": idx}},
            })

        async def fake_astream_events(*args, **kwargs):
            for event in events:
                yield event

        chunks: list[str] = []
        with patch("langgraph_langchain.langgraph_agent.create_react_agent") as mock_create:
            mock_agent = MagicMock()
            mock_agent.astream_events = fake_astream_events
            mock_create.return_value = mock_agent

            async for text, _ in run_analysis_stream(
                instruction="test",
                source_path=str(SAMPLE_CSV),
                workspace_dir=str(tmp_path),
                api_key="fake",
                model_id="fake",
                api_base="https://fake",
                session_id="stream-stop-steps",
            ):
                if text:
                    chunks.append(text)

        combined = "".join(chunks)
        assert "exceeded the maximum tool-step budget" in combined
