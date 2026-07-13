"""eda_profile tool — automated EDA with visualization and business hints."""

from __future__ import annotations

import logging

import matplotlib
matplotlib.use("Agg")

import numpy as np
import pandas as pd
from langchain_core.tools import tool

from langgraph_langchain.tools.registry import registry
from langgraph_langchain.tools._shared import _validate_tool_stage_factory
from langgraph_langchain.tracing import get_trace_context

logger = logging.getLogger(__name__)


def _factory(session):
    _validate = _validate_tool_stage_factory(session)

    @tool
    def eda_profile(max_numeric_cols: int = 5, max_cat_cols: int = 5) -> str:  # noqa: C901
        """Run automatic EDA on the loaded dataset. Generates charts and returns a structured summary.

        Covers: missing values, descriptive stats, correlations, distributions, and time-series trends.
        Must call load_data before calling this.

        Args:
            max_numeric_cols: Max numeric columns to plot distributions for (default 5).
            max_cat_cols: Max categorical columns to plot value counts for (default 3).
        """
        # Validate stage before execution
        error_msg = _validate("eda_profile")
        if error_msg:
            return f"[ERROR] {error_msg}"

        import matplotlib.pyplot as plt
        import seaborn as sns

        trace_ctx = get_trace_context(session.session_id)
        if trace_ctx:
            trace_ctx.start_span(
                "eda_profile",
                attributes={
                    "max_numeric_cols": max_numeric_cols,
                    "max_cat_cols": max_cat_cols,
                    "tool": "eda_profile",
                },
            )

        session.structured_logger.log_tool_call(
            tool_name="eda_profile",
            stage=session.state_machine.current_stage,
        )

        # Track tool usage in state machine
        session.state_machine.record_tool_use("eda_profile")

        # R&D domain: Check if this looks like R&D efficiency data and suggest template
        df_preview: pd.DataFrame = session.ns.get("df")
        if df_preview is not None:
            from langgraph_langchain.rd_templates import suggest_template
            cols = df_preview.columns.tolist()
            user_question = session.user_question
            template = suggest_template(user_question, cols)
            if template:
                session.ns["suggested_template"] = template
                logger.info(f"Detected R&D data pattern, suggested template: {template.template_name}")

        df: pd.DataFrame = session.ns.get("df")
        if df is None:
            return "[ERROR] No dataset loaded. Call load_data first."

        ws = session.workspace_dir
        lines: list[str] = []
        charts: list[tuple[str, object]] = []

        # ── Column type detection ──────────────────────────────────────────────
        original_numeric_cols = df.select_dtypes(include="number").columns.tolist()
        business_numeric_cols = session.ns["filter_business_metrics"](df, original_numeric_cols)
        numeric_cols = business_numeric_cols or original_numeric_cols
        cat_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()
        datetime_cols = df.select_dtypes(include=["datetime", "datetimetz"]).columns.tolist()

        # Auto-detect datetime strings
        for col in list(cat_cols):
            try:
                parsed = pd.to_datetime(df[col], errors="coerce", format="mixed")
                if parsed.notna().mean() > 0.8:
                    df = df.copy()
                    df[col] = parsed
                    datetime_cols.append(col)
                    cat_cols.remove(col)
            except Exception:
                pass
        session.ns["df"] = df  # persist updated dtypes

        lines.append("## EDA Profile")
        lines.append("\n### Dataset Overview")
        lines.append(f"- Shape: {df.shape[0]} rows × {df.shape[1]} cols")
        lines.append(f"- Numeric columns: {len(numeric_cols)}")
        lines.append(f"- Categorical columns: {len(cat_cols)}")
        lines.append(f"- Datetime columns: {len(datetime_cols)}")
        excluded_metrics = [col for col in original_numeric_cols if col not in business_numeric_cols]
        if excluded_metrics:
            lines.append(f"- Business metric candidates: {len(business_numeric_cols)}")
            lines.append(f"- Excluded id-like numeric fields from metric analysis: {', '.join(excluded_metrics)}")

        analysis_signals: list[str] = []
        business_hints: list[str] = []
        strong_pairs: list[tuple[str, str, float]] = []
        outlier_info: list[tuple[str, int, float, float, float]] = []
        numeric_desc = None

        # ── 1. Missing values ─────────────────────────────────────────────────
        missing = df.isnull().sum()
        missing_pct = (missing / len(df) * 100).round(1) if len(df) else missing * 0
        missing_info = missing_pct[missing_pct > 0].sort_values(ascending=False)
        lines.append("\n### Missing Values")
        if missing_info.empty:
            lines.append("- none")
        else:
            for col, pct in missing_info.head(10).items():
                lines.append(f"- {col}: {int(missing[col])} ({pct}%)")
            for col, pct in missing_info.head(5).items():
                if pct >= 30:
                    analysis_signals.append(f"{col} 缺失率 {pct:.1f}%，需要优先判断是否为系统性缺失或特定分组缺失。")
                else:
                    analysis_signals.append(f"{col} 存在 {pct:.1f}% 缺失，可在后续按分组检查缺失是否集中。")
            if len(missing_info) >= 2:
                fig, ax = plt.subplots(figsize=(10, max(3, len(missing_info) * 0.35)))
                missing_info.plot(kind="barh", ax=ax, color="coral")
                ax.set_xlabel("Missing %")
                ax.set_title("Missing Values by Column")
                plt.tight_layout()
                p = ws / "eda_missing_values.png"
                plt.savefig(p, bbox_inches="tight", dpi=100)
                plt.close()
                charts.append(("Missing Values", p))

        # ── 2. Descriptive stats + correlation ────────────────────────────────
        if numeric_cols:
            numeric_desc = df[numeric_cols].describe().round(3)
            lines.append(f"\n### Descriptive Statistics\n{numeric_desc.to_string()}")

            skew_series = df[numeric_cols].skew(numeric_only=True).sort_values(key=lambda s: s.abs(), ascending=False)
            if not skew_series.empty:
                lines.append("\n### Skewness")
                for col, val in skew_series.head(8).items():
                    lines.append(f"- {col}: skew={val:.3f}")
                    if abs(val) >= 1:
                        analysis_signals.append(f"{col} 偏度 {val:.2f}，可能存在长尾分布，均值易受极端值影响。")

            # ── 2b. Outlier detection (IQR method) ────────────────────────────
            for col in numeric_cols:
                s = df[col].dropna()
                if s.empty:
                    continue
                q1, q3 = s.quantile(0.25), s.quantile(0.75)
                iqr = q3 - q1
                lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                n_out = int(((s < lo) | (s > hi)).sum())
                if n_out > 0:
                    outlier_info.append((col, n_out, n_out / len(s) * 100, lo, hi))
            lines.append("\n### Outliers (IQR method)")
            if outlier_info:
                for col, n_out, pct, lo, hi in sorted(outlier_info, key=lambda x: x[2], reverse=True):
                    lines.append(f"- {col}: {n_out} outliers ({pct:.1f}%) | bounds [{lo:.3g}, {hi:.3g}]")
                    analysis_signals.append(
                        f"{col} 有 {pct:.1f}% 异常值，后续需要结合业务含义区分数据问题、真实业务事件或样本过小噪声。"
                    )
                plot_out_cols = [c for c, *_ in outlier_info][:8]
                n_box = min(8, len(plot_out_cols))
                fig, ax = plt.subplots(figsize=(max(6, n_box * 1.4), 5))
                box_data = [df[col].dropna().values for col in plot_out_cols]
                ax.boxplot(box_data, labels=plot_out_cols)
                ax.set_title("Outlier Detection (IQR) - Box Plots")
                ax.tick_params(axis="x", rotation=30)
                plt.tight_layout()
                p = ws / "eda_outliers.png"
                plt.savefig(p, bbox_inches="tight", dpi=100)
                plt.close()
                charts.append(("Outliers", p))
            else:
                lines.append("- none detected")

            if len(numeric_cols) >= 2:
                corr = df[numeric_cols].corr()
                n = len(numeric_cols)
                sz = min(16, max(6, n))
                fig, ax = plt.subplots(figsize=(sz, sz * 0.8))
                mask = np.triu(np.ones_like(corr, dtype=bool))
                sns.heatmap(
                    corr, mask=mask, annot=True, fmt=".2f",
                    cmap="coolwarm", center=0, square=True, ax=ax,
                    annot_kws={"size": max(6, 10 - n // 3)},
                )
                ax.set_title("Correlation Heatmap")
                plt.tight_layout()
                p = ws / "eda_correlation_heatmap.png"
                plt.savefig(p, bbox_inches="tight", dpi=100)
                plt.close()
                charts.append(("Correlation Heatmap", p))

                lines.append("\n### Strong Correlations (|r| ≥ 0.7)")
                for i in range(len(corr.columns)):
                    for j in range(i + 1, len(corr.columns)):
                        r = float(corr.iloc[i, j])
                        if abs(r) >= 0.7:
                            strong_pairs.append((corr.columns[i], corr.columns[j], r))
                if strong_pairs:
                    for c1, c2, r in sorted(strong_pairs, key=lambda x: abs(x[2]), reverse=True)[:10]:
                        lines.append(f"- {c1} vs {c2}: r={r:.3f}")
                        analysis_signals.append(
                            f"{c1} 与 {c2} 的相关系数为 {r:.3f}，可作为线索，但后续需做分层或分组验证，避免直接当作因果关系。"
                        )
                else:
                    lines.append("- none")

        # ── 3. Distributions ──────────────────────────────────────────────────
        if numeric_cols:
            plot_cols = numeric_cols[:max_numeric_cols]
            n_cols = min(3, len(plot_cols))
            n_rows = (len(plot_cols) + n_cols - 1) // n_cols
            fig, axes = plt.subplots(n_rows, n_cols, figsize=(5 * n_cols, 4 * n_rows))
            axes_flat = np.array(axes).flatten() if n_rows * n_cols > 1 else [axes]
            sample = df if len(df) <= 50_000 else df.sample(50_000, random_state=42)
            for i, col in enumerate(plot_cols):
                ax = axes_flat[i]
                try:
                    sns.histplot(sample[col].dropna(), kde=True, ax=ax, bins=30)
                except Exception:
                    ax.hist(sample[col].dropna(), bins=30)
                ax.set_title(col)
                ax.set_xlabel("")
            for i in range(len(plot_cols), len(axes_flat)):
                axes_flat[i].set_visible(False)
            plt.suptitle("Numeric Distributions", y=1.02)
            plt.tight_layout()
            p = ws / "eda_distributions.png"
            plt.savefig(p, bbox_inches="tight", dpi=100)
            plt.close()
            charts.append(("Distributions", p))

        # ── 4. Categorical value counts + balance + cross-analysis ──────────
        low_card = []
        if cat_cols:
            plot_cats = sorted(cat_cols, key=lambda c: df[c].nunique())[:max_cat_cols]
            n_cols = min(2, len(plot_cats))
            n_rows = (len(plot_cats) + n_cols - 1) // n_cols
            fig, axes = plt.subplots(n_rows, n_cols, figsize=(7 * n_cols, 4 * n_rows))
            axes_flat = np.array(axes).flatten() if n_rows * n_cols > 1 else [axes]
            lines.append("\n### Categorical Columns")
            for i, col in enumerate(plot_cats):
                vc = df[col].value_counts().head(15)
                top1_pct = float(vc.iloc[0] / vc.sum() * 100) if len(vc) else 0
                probs = vc / vc.sum() if len(vc) else vc
                gini = float(1 - (probs ** 2).sum()) if len(vc) else 0
                balance_flag = " ⚠ highly imbalanced" if top1_pct > 80 else ""
                lines.append(
                    f"- {col}: {df[col].nunique()} unique"
                    f" | top1={top1_pct:.0f}%{balance_flag}"
                    f" | gini={gini:.2f}"
                    f" | top values: {', '.join(str(v) for v in vc.index[:5])}"
                )
                if top1_pct > 80:
                    top_count = int(vc.iloc[0]) if len(vc) else 0
                    support = session.ns["support_label"](top_count, len(df))
                    if support in {"very_thin", "thin"}:
                        analysis_signals.append(
                            f"{col} 头部类别占比 {top1_pct:.0f}% ，但头部样本仅 {top_count} 行；该不均衡信号目前支持较薄，只能作为探索性线索。"
                        )
                    else:
                        analysis_signals.append(
                            f"{col} 高度不均衡，头部类别占比 {top1_pct:.0f}%（头部样本 {top_count} 行），比较分组结论时需注意样本偏斜。"
                        )
                ax = axes_flat[i]
                vc.plot(kind="barh", ax=ax)
                ax.set_title(f"{col} (top {len(vc)})")
                ax.set_xlabel("count")
            for i in range(len(plot_cats), len(axes_flat)):
                axes_flat[i].set_visible(False)
            plt.tight_layout()
            p = ws / "eda_categorical.png"
            plt.savefig(p, bbox_inches="tight", dpi=100)
            plt.close()
            charts.append(("Categorical", p))

            # Cross-analysis: low-cardinality cat × first numeric col
            if numeric_cols:
                num_col = numeric_cols[0]
                low_card = [c for c in cat_cols if 2 <= df[c].nunique() <= 10]
                for cat_col in low_card[:3]:
                    try:
                        groups = [grp[num_col].dropna().values
                                  for _, grp in df.groupby(cat_col)]
                        labels = [str(k) for k in df[cat_col].dropna().unique()[:10]]
                        fig, ax = plt.subplots(figsize=(max(6, len(labels)), 4))
                        ax.boxplot(groups, labels=labels)
                        ax.set_title(f"{num_col} by {cat_col}")
                        ax.set_xlabel(cat_col)
                        ax.set_ylabel(num_col)
                        ax.tick_params(axis="x", rotation=30)
                        plt.tight_layout()
                        safe_name = cat_col.replace("/", "_").replace(" ", "_")
                        p = ws / f"eda_cat_vs_num_{safe_name}.png"
                        plt.savefig(p, bbox_inches="tight", dpi=100)
                        plt.close()
                        charts.append((f"{num_col} by {cat_col}", p))
                        lines.append(f"- Cross-analysis: {num_col} by {cat_col} → boxplot saved")
                    except Exception:
                        pass

        # ── 5. Time-series trend ───────────────────────────────────────────────
        if datetime_cols and numeric_cols:
            dt_col = datetime_cols[0]
            trend_cols = numeric_cols[:3]
            try:
                tmp = df[[dt_col] + trend_cols].dropna(subset=[dt_col]).sort_values(dt_col)
                fig, axes = plt.subplots(
                    len(trend_cols), 1,
                    figsize=(12, 4 * len(trend_cols)),
                    sharex=True,
                )
                if len(trend_cols) == 1:
                    axes = [axes]
                for ax, col in zip(axes, trend_cols):
                    ax.plot(tmp[dt_col], tmp[col], linewidth=0.8)
                    ax.set_ylabel(col)
                    ax.set_title(f"{col} over time")
                plt.suptitle(f"Time-Series Trends (by {dt_col})")
                plt.tight_layout()
                p = ws / "eda_trend.png"
                plt.savefig(p, bbox_inches="tight", dpi=100)
                plt.close()
                charts.append(("Time-Series Trends", p))
                lines.append(f"\n### Time-Series\n- detected datetime column '{dt_col}'")

                trend_frame = session.ns["time_trend"](df, dt_col, trend_cols[:2], freq="ME")
                if isinstance(trend_frame, pd.DataFrame) and not trend_frame.empty:
                    latest = trend_frame.tail(2)
                    if len(latest) == 2:
                        prev = latest.iloc[0]
                        curr = latest.iloc[1]
                        for metric in trend_cols[:2]:
                            prev_val = prev.get(metric)
                            curr_val = curr.get(metric)
                            if pd.notna(prev_val) and pd.notna(curr_val) and prev_val not in (0, None):
                                pct = (curr_val - prev_val) / prev_val
                                period_series = pd.to_datetime(tmp[dt_col], errors="coerce").dt.to_period("M")
                                current_period = pd.to_datetime(curr.get(dt_col), errors="coerce").to_period("M")
                                previous_period = pd.to_datetime(prev.get(dt_col), errors="coerce").to_period("M")
                                current_support = int(tmp.loc[period_series == current_period, metric].dropna().shape[0])
                                previous_support = int(tmp.loc[period_series == previous_period, metric].dropna().shape[0])
                                support = session.ns["support_label"](min(previous_support, current_support))
                                if support in {"very_thin", "thin"}:
                                    business_hints.append(
                                        f"{metric} 最近一个周期相较前一周期变化 {pct:.1%}，但前后窗口可用样本仅 {previous_support}/{current_support} 行；当前只可视为待验证波动线索。"
                                    )
                                    if abs(pct) >= 0.15:
                                        analysis_signals.append(
                                            f"{metric} 在最近窗口变化 {pct:.1%}，但前后窗口样本仅 {previous_support}/{current_support} 行，暂不宜升级为稳定趋势判断。"
                                        )
                                elif support == "limited":
                                    business_hints.append(
                                        f"{metric} 最近一个周期相较前一周期变化 {pct:.1%}，前后窗口可用样本 {previous_support}/{current_support} 行，需结合更多窗口确认是否稳定。"
                                    )
                                    if abs(pct) >= 0.15:
                                        analysis_signals.append(
                                            f"{metric} 在最近窗口变化 {pct:.1%}，需要结合时间段、分组与更多窗口验证异常波动是否持续。"
                                        )
                                else:
                                    business_hints.append(
                                        f"{metric} 最近一个周期相较前一周期变化 {pct:.1%}，可进一步核查驱动该变化的分组或事件。"
                                    )
                                    if abs(pct) >= 0.15:
                                        analysis_signals.append(
                                            f"{metric} 在最近窗口变化 {pct:.1%}，需要结合时间段与分组定位异常波动来源。"
                                        )
            except Exception as exc:
                lines.append(f"\n### Time-Series\n- skipped ({exc})")

        # ── 6. Business-oriented summaries ────────────────────────────────────
        lines.append("\n### Analysis Signals")
        if analysis_signals:
            seen = set()
            for signal in analysis_signals:
                if signal not in seen:
                    seen.add(signal)
                    lines.append(f"- {signal}")
        else:
            lines.append("- No major automatic warning signals detected; proceed with metric definition and grouped analysis manually.")

        # R&D domain: Add template-specific hints if detected
        suggested_template = session.ns.get("suggested_template")
        if suggested_template:
            lines.append("\n### 📋 Recommended Analysis Template")
            lines.append(f"\n**{suggested_template.template_name}** (ID: {suggested_template.template_id})")
            lines.append(f"- **Description**: {suggested_template.description}")
            lines.append(f"- **Difficulty**: {suggested_template.difficulty}")
            lines.append(f"- **Target User**: {suggested_template.target_user}")
            lines.append(f"- **Key Metrics**: {', '.join(suggested_template.key_metrics[:5])}")

            lines.append(f"\n**Recommended Analysis Steps**:")
            for step in suggested_template.analysis_steps[:5]:
                lines.append(f"  {step}")

            if suggested_template.caveats:
                lines.append(f"\n**Important Caveats**:")
                for caveat in suggested_template.caveats[:3]:
                    lines.append(f"  ⚠️ {caveat}")

            if suggested_template.example_questions:
                lines.append(f"\n**Example Questions This Template Can Answer**:")
                for q in suggested_template.example_questions[:3]:
                    lines.append(f"  • {q}")

            business_hints.append(
                f"💡 建议遵循 {suggested_template.template_name} 模板的分析步骤，"
                f"重点关注 {', '.join(suggested_template.key_metrics[:2])} 等指标。"
            )

        lines.append("\n### Business Analysis Hints")

        if numeric_cols and cat_cols:
            candidate_metrics = []
            for col in numeric_cols:
                if pd.api.types.is_numeric_dtype(df[col]) and df[col].notna().any():
                    candidate_metrics.append(col)
            low_card_candidates = [c for c in cat_cols if 2 <= df[c].nunique() <= 8]
            if candidate_metrics and low_card_candidates:
                metric = candidate_metrics[0]
                dim = low_card_candidates[0]
                profile = session.ns["profile_dimension"](df, dim, metric)
                if isinstance(profile, pd.DataFrame) and not profile.empty:
                    mean_col = f"{metric}_mean"
                    sum_col = f"{metric}_sum"
                    if mean_col in profile.columns:
                        ordered = profile.sort_values(mean_col, ascending=False)
                        top_row = ordered.iloc[0]
                        bottom_row = ordered.iloc[-1]
                        business_hints.append(
                            f"Top/Bottom group summary: 在维度 {dim} 上，{top_row[dim]} 的 {metric} 均值最高 ({top_row[mean_col]:.3g})，"
                            f"{bottom_row[dim]} 最低 ({bottom_row[mean_col]:.3g})。"
                        )
                    if sum_col in profile.columns and f"{metric}_share" in profile.columns:
                        head = profile.sort_values(sum_col, ascending=False).iloc[0]
                        business_hints.append(
                            f"Contribution hint: 维度 {dim} 的头部组 {head[dim]} 贡献了 {head[f'{metric}_share']:.1%} 的 {metric} 总量。"
                        )

        if datetime_cols and numeric_cols:
            primary_metric = numeric_cols[0]
            available_dims = session.ns["filter_explanation_dims"](df, [c for c in cat_cols if 2 <= df[c].nunique() <= 12])
            if available_dims:
                definition_risk = session.ns["check_metric_definition_risk"](df, primary_metric, datetime_cols[0])
                decomposition = session.ns["decompose_metric_change"](
                    df, primary_metric, datetime_cols[0], available_dims[:3]
                )
                if decomposition:
                    driver_ranking = session.ns["rank_driver_candidates"](
                        df, primary_metric, datetime_cols[0], available_dims[:3]
                    )
                    key_dimension = driver_ranking[0]["dimension"] if driver_ranking else available_dims[0]
                    counterfactual_checks = session.ns["run_counterfactual_checks"](
                        df, primary_metric, key_dimension, datetime_cols[0]
                    )
                    recommendations = session.ns["generate_recommendation_candidates"](
                        findings=[{
                            "finding": f"{primary_metric} period-over-period change",
                            "evidence_level": "C" if definition_risk.get("exploratory_only") else ("B" if decomposition.get("coverage_ratio", 0) >= 0.5 else "C"),
                        }],
                        drivers=[
                            {**driver, "evidence_level": "C"}
                            for driver in driver_ranking
                        ] if definition_risk.get("exploratory_only") else driver_ranking,
                    )
                    session.ns.setdefault("explanation_bundle", {}).update({
                        "recommendations": recommendations,
                        "primary_metric": primary_metric,
                        "time_col": datetime_cols[0],
                        "candidate_dims": available_dims[:3],
                    })
                    business_hints.append(
                        f"如果需要解释 {primary_metric} 在 {decomposition['previous_period']} 到 {decomposition['current_period']} 的变化，"
                        "应先做 contribution breakdown，再讨论 driver。"
                    )
                    business_hints.append(
                        f"Report contract: 最终报告至少要在 Summary 或 Key Findings 中写明 {primary_metric} 的比较窗口 "
                        f"{decomposition['previous_period']} -> {decomposition['current_period']}，并引用量化变化或 contributor。"
                    )
                    top_negative = decomposition.get("top_negative_contributors") or []
                    if top_negative:
                        lead = top_negative[0]
                        business_hints.append(
                            f"解释结论应落到具体对象：例如维度 {lead['dimension']} 下 group={lead['group']}"
                            f" 对变化贡献 {lead['contribution']:.3g}。"
                        )
                    if driver_ranking:
                        top_driver = driver_ranking[0]
                        business_hints.append(
                            f"Driver ranking hint: {top_driver['dimension']}={top_driver['group']} 当前排第 1，"
                            f"evidence level {top_driver['evidence_level']}，score={top_driver['score']:.2f}。"
                        )
                        business_hints.append(
                            f"Report contract: 最终报告需在 Key Findings 或 Analysis 中引用 top driver "
                            f"{top_driver['dimension']}={top_driver['group']}，并写出 evidence level {top_driver['evidence_level']}。"
                        )
                    if recommendations:
                        validation_recs = [item for item in recommendations if item.get("type") in {"validation", "observe"}]
                        if validation_recs:
                            business_hints.append("Weak-evidence recommendation hint: 当前证据较弱时，应优先写 validation / observe 类建议，例如先验证、继续观察，而不是直接行动。")
                            business_hints.append("Report contract: 如果 explanation_bundle 的 recommendations 只有 validation / observe，最终 Recommendations 也必须保持验证/观察语气，不能升级成 should / prioritize / 立即调整。")
                    if counterfactual_checks:
                        business_hints.append("解释性结论应补充稳定性说明，例如去掉头部组或切换窗口后方向是否仍一致。")
                        business_hints.append("Report contract: 最终报告需在 Analysis 段明确写出 stability / robustness check 结果，例如去掉头部组后是否仍成立、切换窗口后是否仍同向。")
                    if definition_risk.get("exploratory_only"):
                        business_hints.append("当前指标存在口径/重复/退款等风险，解释应降级为 exploratory 并显式写出口径 caveat。")
                        business_hints.append("Report contract: 由于存在 definition risk，最终 Summary 或 Data Quality 必须显式写 exploratory / metric definition note。")

        if strong_pairs:
            business_hints.append("Strong correlations are only clues; use non-causal language and suggest validation or stratified checks before claiming drivers.")
        if outlier_info:
            business_hints.append("异常解释应至少区分三类：数据问题、真实业务事件、小样本噪声。")

        if business_hints:
            seen = set()
            for hint in business_hints:
                if hint not in seen:
                    seen.add(hint)
                    lines.append(f"- {hint}")
        else:
            lines.append("- No business-oriented hints were generated automatically; define metrics and compare meaningful segments manually.")

        # ── Register artifacts ────────────────────────────────────────────────
        for _label, chart_path in charts:
            if chart_path.exists():
                rel = chart_path.relative_to(session.workspace_dir.parent)
                session.new_artifacts.append({
                    "name": chart_path.name,
                    "path": str(chart_path),
                    "relative_path": str(rel),
                    "url": f"/workspace/files/{rel}",
                })
                session.known_image_files.add(chart_path)

        chart_names = [lbl for lbl, _ in charts]
        lines.append("\n### Charts Generated")
        if chart_names:
            for chart_name in chart_names:
                lines.append(f"- {chart_name}")
        else:
            lines.append("- none")

        if trace_ctx:
            trace_ctx.current_span.set_attribute("charts_generated", len(charts))
            template = session.ns.get("suggested_template")
            if template:
                trace_ctx.current_span.set_attribute("template_suggested", template.template_name)
            trace_ctx.end_current_span(status="completed")

        # Mark quality assessment complete and try to advance stage
        session.state_machine.add_condition("quality_assessed")
        session.state_machine.add_condition("issues_documented")

        # Try to advance to BASIC_EDA stage
        session.try_advance_stage()

        return "\n".join(lines)

    return eda_profile


registry.register(
    name="eda_profile",
    toolset="analysis",
    factory=_factory,
    description="Run automatic EDA on the loaded dataset. Generates charts and returns a structured summary.",
    emoji="📊",
)
