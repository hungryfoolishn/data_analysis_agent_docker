#!/usr/bin/env python3
"""
系统监控仪表板
实时展示分析系统的健康状况和性能指标
"""

import streamlit as st
import requests
import os
import time
from datetime import datetime
from typing import Dict, Any, List

# API 配置
API_BASE_URL = "http://localhost:8888"
DASHBOARD_ENDPOINT = f"{API_BASE_URL}/metrics/dashboard"
API_AUTH_TOKEN = os.environ.get("API_AUTH_TOKEN", "")

# 页面配置
st.set_page_config(
    page_title="系统监控仪表板",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed"
)


def fetch_dashboard_metrics() -> Dict[str, Any]:
    """获取仪表板指标"""
    try:
        headers = {"Authorization": f"Bearer {API_AUTH_TOKEN}"} if API_AUTH_TOKEN else {}
        response = requests.get(DASHBOARD_ENDPOINT, headers=headers, timeout=5)
        if response.status_code == 200:
            return response.json()
        else:
            st.error(f"获取指标失败: {response.status_code}")
            return None
    except Exception as e:
        st.error(f"连接 API 服务器失败: {str(e)}")
        return None


def format_duration(seconds: float) -> str:
    """格式化时长"""
    if seconds < 60:
        return f"{seconds:.1f}秒"
    elif seconds < 3600:
        return f"{seconds/60:.1f}分钟"
    else:
        return f"{seconds/3600:.1f}小时"


def render_overview_metrics(overview: Dict[str, Any]):
    """渲染概览指标"""
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            label="总会话数",
            value=overview.get("total_sessions", 0),
            help="系统启动以来的总分析会话数"
        )

    with col2:
        success_rate = overview.get("success_rate", 0) * 100
        st.metric(
            label="成功率",
            value=f"{success_rate:.1f}%",
            delta=f"{success_rate - 70:.1f}%" if success_rate > 0 else None,
            delta_color="normal",
            help="成功完成的分析占比"
        )

    with col3:
        avg_steps = overview.get("avg_steps", 0)
        st.metric(
            label="平均步数",
            value=f"{avg_steps:.1f}",
            help="每个分析会话的平均执行步数"
        )

    with col4:
        avg_duration = overview.get("avg_duration", 0)
        st.metric(
            label="平均耗时",
            value=format_duration(avg_duration),
            help="每个分析会话的平均执行时间"
        )


def render_detailed_metrics(detailed: Dict[str, Any]):
    """渲染详细指标"""
    st.subheader("📈 详细指标")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**会话统计**")
        st.write(f"- 成功会话: {detailed.get('successful_sessions', 0)}")
        st.write(f"- 失败会话: {detailed.get('failed_sessions', 0)}")
        st.write(f"- 取消会话: {detailed.get('cancelled_sessions', 0)}")
        st.write(f"- 总步数: {detailed.get('total_steps', 0)}")

    with col2:
        st.markdown("**恢复统计**")
        recovery_rate = detailed.get('recovery_success_rate', 0) * 100
        st.write(f"- 恢复尝试: {detailed.get('recovery_attempts', 0)}")
        st.write(f"- 成功恢复: {detailed.get('successful_recoveries', 0)}")
        st.write(f"- 恢复成功率: {recovery_rate:.1f}%")


def render_failure_breakdown(breakdown: Dict[str, Any]):
    """渲染失败分析"""
    st.subheader("⚠️ 失败分析")

    total_failures = breakdown.get("total_failures", 0)
    if total_failures == 0:
        st.success("🎉 暂无失败记录！")
        return

    st.write(f"总失败数: **{total_failures}**")

    # 显示失败分布
    failure_list = breakdown.get("breakdown", [])
    if failure_list:
        st.markdown("**失败类型分布**")
        for item in failure_list[:10]:  # 只显示前10个
            code = item.get("failure_code", "unknown")
            count = item.get("count", 0)
            percentage = item.get("percentage", 0)

            # 创建进度条
            st.write(f"**{code}**: {count} 次 ({percentage:.1f}%)")
            st.progress(percentage / 100)


def render_stage_stats(stage_stats: Dict[str, Any]):
    """渲染阶段统计"""
    st.subheader("🔄 分析阶段统计")

    stats = stage_stats.get("stage_stats", [])
    if not stats:
        st.info("暂无阶段统计数据")
        return

    # 创建表格
    stage_names = {
        "schema_understanding": "数据结构理解",
        "data_quality_check": "数据质量检查",
        "basic_eda": "基础探索分析",
        "deep_dive": "深度分析",
        "conclusion_synthesis": "结论综合",
        "report_generation": "报告生成"
    }

    for stat in stats:
        stage = stat.get("stage", "")
        reached = stat.get("sessions_reached", 0)
        succeeded = stat.get("sessions_succeeded", 0)
        success_rate = stat.get("success_rate", 0) * 100

        if reached > 0:
            stage_name = stage_names.get(stage, stage)
            col1, col2, col3 = st.columns([2, 1, 1])
            with col1:
                st.write(f"**{stage_name}**")
            with col2:
                st.write(f"{reached} 次到达")
            with col3:
                st.write(f"{success_rate:.1f}% 成功")


def render_recent_sessions(sessions: List[Dict[str, Any]]):
    """渲染最近会话"""
    st.subheader("🕐 最近会话")

    if not sessions:
        st.info("暂无会话记录")
        return

    for session in sessions[:5]:  # 只显示最近5个
        session_id = session.get("session_id", "unknown")
        status = session.get("status", "unknown")
        steps = session.get("steps", 0)
        duration = session.get("duration_seconds", 0)
        start_time = session.get("start_time", "")

        # 状态图标
        status_icon = {
            "success": "✅",
            "failed": "❌",
            "running": "🔄",
            "cancelled": "⏹️"
        }.get(status, "❓")

        # 格式化时间
        try:
            dt = datetime.fromisoformat(start_time)
            time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            time_str = str(start_time)

        with st.expander(f"{status_icon} {session_id[:16]}... - {time_str}"):
            col1, col2, col3 = st.columns(3)
            with col1:
                st.write(f"**状态**: {status}")
            with col2:
                st.write(f"**步数**: {steps}")
            with col3:
                st.write(f"**耗时**: {format_duration(duration)}")

            instruction = session.get("instruction", "")
            if instruction:
                st.write(f"**问题**: {instruction}")


def main():
    """主函数"""
    st.title("📊 系统监控仪表板")
    st.markdown("实时监控数据分析系统的健康状况和性能指标")

    # 添加刷新按钮
    col1, col2, col3 = st.columns([1, 1, 4])
    with col1:
        if st.button("🔄 刷新数据", use_container_width=True):
            st.rerun()
    with col2:
        auto_refresh = st.checkbox("自动刷新", value=False)

    # 获取数据
    metrics = fetch_dashboard_metrics()

    if metrics is None:
        st.error("无法获取仪表板数据，请确保 API 服务器正在运行")
        return

    # 显示更新时间
    timestamp = metrics.get("timestamp", "")
    if timestamp:
        try:
            dt = datetime.fromisoformat(timestamp)
            st.caption(f"最后更新: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
        except (ValueError, TypeError):
            st.caption(f"最后更新: {timestamp}")

    st.divider()

    # 渲染各个部分
    overview = metrics.get("overview", {})
    render_overview_metrics(overview)

    st.divider()

    col1, col2 = st.columns(2)

    with col1:
        detailed = metrics.get("detailed_metrics", {})
        render_detailed_metrics(detailed)

        st.divider()

        breakdown = metrics.get("failure_breakdown", {})
        render_failure_breakdown(breakdown)

    with col2:
        stage_stats = metrics.get("stage_stats", {})
        render_stage_stats(stage_stats)

    st.divider()

    recent_sessions = metrics.get("recent_sessions", [])
    render_recent_sessions(recent_sessions)

    # 自动刷新
    if auto_refresh:
        time.sleep(5)
        st.rerun()


if __name__ == "__main__":
    main()
