from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional

import matplotlib
matplotlib.use("Agg")

import numpy as np

logger = logging.getLogger(__name__)
import pandas as pd
from langchain_core.messages import HumanMessage
# @tool no longer imported here — tools are in langgraph_langchain/tools/ package
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langgraph.errors import GraphRecursionError

from langgraph_langchain.schemas import (
    AnalysisStage,
    AnalysisAssumption,
    EvidenceItem,
    FailureInfo,
    Finding,
    MetricDefinition,
    RecoveryAction,
    StageResult,
)
from langgraph_langchain.rd_efficiency_domain import (
    get_metric_definition,
    suggest_related_metrics,
)
from langgraph_langchain.rd_metric_library import (
    validate_velocity_calculation,
    validate_cycle_time_calculation,
    validate_defect_rate_calculation,
    interpret_velocity_trend,
    interpret_cycle_time,
    interpret_defect_rate,
)
from langgraph_langchain.rd_validators import (
    validate_rd_metric_definition,
    validate_rd_finding,
    validate_rd_analysis_completeness,
    validate_sprint_data,
    validate_pr_data,
    validate_deployment_data,
)
from langgraph_langchain.rd_templates import (
    suggest_template,
    get_template,
)
from langgraph_langchain.tracing import (
    create_trace_context,
    get_trace_context,
    remove_trace_context,
)
from langgraph_langchain.structured_logging import StructuredLogger
from langgraph_langchain.semantic.formatting import format_semantic_context
from langgraph_langchain.skills_loader import SkillsLoader
from langgraph_langchain.config import (
    MAX_OUTPUT_LEN as _MAX_OUTPUT_LEN,
    CODE_TIMEOUT as _CODE_TIMEOUT,
    MAX_PYTHON_REPL_LINES as _MAX_PYTHON_REPL_LINES,
    MAX_AGENT_STEPS as _MAX_AGENT_STEPS,
    MAX_CONSECUTIVE_PYTHON_ERRORS as _MAX_CONSECUTIVE_PYTHON_ERRORS,
    REQUIRED_STEP_MARKER_ALIASES as _REQUIRED_STEP_MARKER_ALIASES,
    MAX_RETAINED_ARTIFACTS as _MAX_RETAINED_ARTIFACTS,
    SYNTHESIZED_REPORT_MIN_FINDINGS as _SYNTH_REPORT_MIN_FINDINGS,
    LLM_REQUEST_TIMEOUT as _LLM_REQUEST_TIMEOUT,
    LLM_MAX_RETRIES as _LLM_MAX_RETRIES,
    LLM_EXTRA_HEADERS as _LLM_EXTRA_HEADERS,
    LLM_ENABLE_THINKING as _LLM_ENABLE_THINKING,
    LLM_EXTRA_BODY as _LLM_EXTRA_BODY,
    RUNTIME_V2_ENABLED as _RUNTIME_V2_ENABLED,
    RUNTIME_V2_FALLBACK_ENABLED as _RUNTIME_V2_FALLBACK_ENABLED,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── Skills loader (progressive disclosure) ──────────────────────────────────
_skills_loader = SkillsLoader(Path(__file__).resolve().parent / "skills")

# ── Dynamic prompt builder ──────────────────────────────────────────────────
from langgraph_langchain.prompts.prompt_builder import PromptBuilder
_prompt_builder = PromptBuilder(Path(__file__).resolve().parent / "prompts" / "sections")

# ── Shared helpers (moved to tools/_shared.py, re-exported for backward compat) ─
from langgraph_langchain.tools._shared import (  # noqa: F401 — re-exports
    _RE_EVIDENCE_MARKER,
    _RE_QUANTITATIVE_EVIDENCE,
    _RE_TIME_WINDOW,
    _RE_GROUP_REFERENCE,
    _RE_NEXT_HEADING,
    _safe_workspace_path,
    _validate_python_repl_step,
    _contains_evidence_marker,
    _contains_quantitative_evidence,
    _has_time_window_reference,
    _has_group_reference,
    _extract_section,
    _stage_rank,
)


def _format_preview_text(text: object) -> str:
    """Return text for frontend display while preserving full line structure."""
    return str(text or "").strip()


def _extract_event_output_text(raw: object) -> str:
    """Extract the meaningful tool output text from LangGraph event payloads."""
    if raw is None:
        return ""
    content = getattr(raw, "content", raw)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                piece = item.get("text") or item.get("content") or ""
            else:
                piece = getattr(item, "text", None) or str(item)
            if piece:
                parts.append(str(piece))
        return "\n".join(parts).strip()
    return str(content).strip()


def _make_session_logger(workspace_dir: Path, session_id: str) -> logging.Logger:
    """Create a logger that writes to both stderr and a per-session log file."""
    logger = logging.getLogger(f"agent.{session_id}")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    log_path = workspace_dir / f"agent_{session_id}.log"
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.propagate = False
    return logger

# Build system prompt from modular .md sections via PromptBuilder
# Note: skills index is injected dynamically via build_with_context() at runtime.
# _SYSTEM_PROMPT is the base prompt without skills/memory (for backward compat).
_SYSTEM_PROMPT = _prompt_builder.build()




class _Session:
    def __init__(self, workspace_dir: str, source_path: str, session_id: str = "", user_question: str = "", restore_state: Optional[Dict] = None, semantic_context: Optional[Dict] = None, force_new_run: bool = False, parent_run_id: Optional[str] = None, retry_of_step_id: Optional[str] = None):
        self.workspace_dir = Path(workspace_dir)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.source_path = source_path
        self.session_id = session_id
        self.user_question = user_question  # Store user's question for template matching
        self.current_stage: AnalysisStage = "schema_understanding"
        self.stage_history: List[StageResult] = []
        self.stage_failures: List[FailureInfo] = []
        self.total_steps: int = 0  # Track total tool steps for metrics
        # Persistent exec namespace - variables survive across python_repl calls
        _ws = Path(workspace_dir).resolve()

        def _save_fig(filename: str) -> None:
            """Save current plt figure to WORKSPACE_DIR and close."""
            import matplotlib.pyplot as _plt
            safe = _safe_workspace_path(_ws, filename)
            _plt.savefig(str(safe), bbox_inches="tight", dpi=100)
            _plt.close()
            print(f"Saved: {filename}")

        def _load_csv(path):
            """Read a CSV with automatic encoding detection.

            Tries utf-8, utf-8-sig, gbk, gb2312, latin-1 in order so GBK-encoded
            Chinese files do not raise UnicodeDecodeError. Prefer the `df`
            variable (already loaded by load_data) over re-reading; when you do
            need to read a CSV directly, use this instead of pd.read_csv.
            """
            import pandas as _pd
            p = str(path)
            for enc in ("utf-8", "utf-8-sig", "gbk", "gb2312", "latin-1"):
                try:
                    return _pd.read_csv(p, encoding=enc)
                except UnicodeDecodeError:
                    continue
            return _pd.read_csv(p, encoding="latin-1")

        def _fix_chinese() -> None:
            """Fix Chinese font rendering in matplotlib using system CJK fonts."""
            import matplotlib as _mpl
            import matplotlib.font_manager as _fm

            _candidates = [
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
                "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
                "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
                "/usr/share/fonts/truetype/arphic/uming.ttc",
                "/usr/share/fonts/truetype/arphic/ukai.ttc",
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc",
            ]
            _fallback_names = [
                "Noto Sans CJK SC",
                "Noto Serif CJK SC",
                "WenQuanYi Micro Hei",
                "WenQuanYi Zen Hei",
                "AR PL UMing CN",
                "AR PL UKai CN",
                "SimHei",
                "Microsoft YaHei",
                "PingFang SC",
                "Heiti SC",
                "Source Han Sans SC",
            ]
            _chosen_name = None
            for _fp in _candidates:
                if Path(_fp).exists():
                    try:
                        _fm.fontManager.addfont(_fp)
                        _prop = _fm.FontProperties(fname=_fp)
                        _chosen_name = _prop.get_name()
                        break
                    except Exception:
                        continue

            _sans = list(dict.fromkeys(([_chosen_name] if _chosen_name else []) + _fallback_names + list(_mpl.rcParams.get("font.sans-serif", []))))
            if _chosen_name:
                _mpl.rcParams["font.family"] = [_chosen_name, "sans-serif"]
            else:
                _mpl.rcParams["font.family"] = ["sans-serif"]
            _mpl.rcParams["font.sans-serif"] = _sans
            _mpl.rcParams["axes.unicode_minus"] = False

        _fix_chinese()

        def _profile_dimension(dataframe, dims, metrics):
            """Profile grouped metrics with count, sum, mean, median, and share."""
            import pandas as _pd

            if isinstance(dims, str):
                dims = [dims]
            if isinstance(metrics, str):
                metrics = [metrics]
            dims = [d for d in dims if d in dataframe.columns]
            metrics = [m for m in metrics if m in dataframe.columns]
            if not dims or not metrics:
                return _pd.DataFrame()

            grouped = dataframe.groupby(dims, dropna=False)
            rows = []
            for key, group in grouped:
                if not isinstance(key, tuple):
                    key = (key,)
                row = {dim: value for dim, value in zip(dims, key)}
                row["row_count"] = len(group)
                for metric in metrics:
                    series = _pd.to_numeric(group[metric], errors="coerce").dropna()
                    row[f"{metric}_non_null"] = int(series.notna().sum())
                    if not series.empty:
                        row[f"{metric}_sum"] = float(series.sum())
                        row[f"{metric}_mean"] = float(series.mean())
                        row[f"{metric}_median"] = float(series.median())
                rows.append(row)

            result = _pd.DataFrame(rows)
            for metric in metrics:
                sum_col = f"{metric}_sum"
                if sum_col in result.columns:
                    total = result[sum_col].sum()
                    if total:
                        result[f"{metric}_share"] = result[sum_col] / total
            return result.sort_values("row_count", ascending=False)

        def _compare_segments(dataframe, dim, metrics, top_n: int = 3):
            """Compare top and bottom segments for selected metrics."""
            profile = _profile_dimension(dataframe, dim, metrics)
            if profile.empty:
                return {}
            if isinstance(metrics, str):
                metrics = [metrics]
            summary = {}
            for metric in metrics:
                mean_col = f"{metric}_mean"
                if mean_col in profile.columns:
                    ordered = profile.sort_values(mean_col, ascending=False)
                    summary[metric] = {
                        "top": ordered.head(top_n).to_dict("records"),
                        "bottom": ordered.tail(top_n).to_dict("records"),
                    }
            return summary

        def _time_trend(dataframe, date_col, metrics, freq: str = "ME"):
            """Aggregate metrics over time and compute pct change + rolling mean."""
            import pandas as _pd

            if date_col not in dataframe.columns:
                return _pd.DataFrame()
            if isinstance(metrics, str):
                metrics = [metrics]
            metrics = [m for m in metrics if m in dataframe.columns]
            if not metrics:
                return _pd.DataFrame()
            tmp = dataframe[[date_col] + metrics].copy()
            tmp[date_col] = _pd.to_datetime(tmp[date_col], errors="coerce")
            tmp = tmp.dropna(subset=[date_col]).sort_values(date_col)
            if tmp.empty:
                return _pd.DataFrame()
            grouped = tmp.set_index(date_col).resample(freq)[metrics].sum(min_count=1)
            for metric in metrics:
                grouped[f"{metric}_pct_change"] = grouped[metric].pct_change()
                grouped[f"{metric}_rolling_mean"] = grouped[metric].rolling(3, min_periods=1).mean()
            return grouped.reset_index()

        def _detect_anomalies(values, method: str = "iqr"):
            """Detect anomalies in a series with IQR or z-score."""
            import pandas as _pd
            import numpy as _np

            series = _pd.to_numeric(_pd.Series(values), errors="coerce").dropna()
            if series.empty:
                return {"count": 0, "indices": [], "lower": None, "upper": None}
            if method == "zscore":
                std = series.std(ddof=0)
                if std == 0 or _np.isnan(std):
                    return {"count": 0, "indices": [], "lower": None, "upper": None}
                z = ((series - series.mean()) / std).abs()
                mask = z > 3
                return {"count": int(mask.sum()), "indices": series.index[mask].tolist(), "lower": None, "upper": None}

            q1, q3 = series.quantile(0.25), series.quantile(0.75)
            iqr = q3 - q1
            lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            mask = (series < lower) | (series > upper)
            return {"count": int(mask.sum()), "indices": series.index[mask].tolist(), "lower": float(lower), "upper": float(upper)}

        def _explain_metric_change(before, after):
            """Return contribution breakdown between two grouped metric snapshots."""
            import pandas as _pd

            before_df = _pd.DataFrame(before)
            after_df = _pd.DataFrame(after)
            if before_df.empty or after_df.empty:
                return _pd.DataFrame()
            common_cols = [c for c in before_df.columns if c in after_df.columns]
            value_cols = [c for c in common_cols if c.endswith("_sum") or c.endswith("_mean")]
            key_cols = [c for c in common_cols if c not in value_cols]
            if not key_cols or not value_cols:
                return _pd.DataFrame()
            metric = value_cols[0]
            merged = before_df[key_cols + [metric]].merge(
                after_df[key_cols + [metric]], on=key_cols, how="outer", suffixes=("_before", "_after")
            ).fillna(0)
            merged["change"] = merged[f"{metric}_after"] - merged[f"{metric}_before"]
            total_change = merged["change"].sum()
            if total_change:
                merged["contribution_share"] = merged["change"] / total_change
            return merged.sort_values("change", ascending=False)

        def _filter_explanation_dims(dataframe, dims, max_unique: int = 12):
            """Keep business-friendly explanation dimensions and exclude ID-like or overly unique fields."""
            if isinstance(dims, str):
                dims = [dims]
            filtered: list[str] = []
            for dim in dims:
                if dim not in dataframe.columns:
                    continue
                series = dataframe[dim]
                non_null = series.dropna()
                if non_null.empty:
                    continue
                dim_lower = str(dim).lower()
                unique_count = int(non_null.nunique(dropna=True))
                unique_ratio = unique_count / max(len(non_null), 1)
                if any(token in dim_lower for token in ["_id", "id_", "uuid", "guid", "identifier", "code"]):
                    continue
                if unique_count > max_unique:
                    continue
                if unique_ratio > 0.8:
                    continue
                filtered.append(dim)
            return filtered

        def _filter_business_metrics(dataframe, metrics):
            """Keep business-meaningful numeric metrics and exclude id-like counters."""
            if isinstance(metrics, str):
                metrics = [metrics]
            filtered: list[str] = []
            for metric in metrics:
                if metric not in dataframe.columns:
                    continue
                series = pd.to_numeric(dataframe[metric], errors="coerce")
                non_null = series.dropna()
                if non_null.empty:
                    continue
                metric_lower = str(metric).lower()
                unique_count = int(non_null.nunique(dropna=True))
                unique_ratio = unique_count / max(len(non_null), 1)
                is_integer_like = bool(np.isclose(non_null % 1, 0).all())
                monotonic_increasing = bool(non_null.is_monotonic_increasing)
                step_diffs = non_null.diff().dropna()
                looks_like_sequence = bool(
                    is_integer_like
                    and monotonic_increasing
                    and not step_diffs.empty
                    and np.isclose(step_diffs, step_diffs.iloc[0]).all()
                )
                if any(token in metric_lower for token in ["_id", "id_", "uuid", "guid", "identifier", "code", "index", "idx", "rank", "seq"]):
                    continue
                if looks_like_sequence and unique_ratio > 0.9:
                    continue
                filtered.append(metric)
            return filtered

        def _support_label(observed_count: int, distinct_units: int | None = None) -> str:
            """Return a qualitative support label for evidence-bounded wording."""
            units = distinct_units if distinct_units is not None else observed_count
            units = int(units or 0)
            observed_count = int(observed_count or 0)
            effective = min(observed_count, units) if units > 0 else observed_count
            if effective < 8:
                return "very_thin"
            if effective < 20:
                return "thin"
            if effective < 60:
                return "limited"
            return "adequate"

        def _decompose_metric_change(dataframe, metric, time_col, dims, compare: str = "mom", top_k: int = 5):
            """Return period-over-period metric decomposition by dimension/group."""
            import pandas as _pd

            if metric not in dataframe.columns or time_col not in dataframe.columns:
                return {}
            if isinstance(dims, str):
                dims = [dims]
            dims = _filter_explanation_dims(dataframe, [d for d in dims if d in dataframe.columns])
            if not dims:
                return {}

            tmp = dataframe[[time_col, metric] + dims].copy()
            tmp[time_col] = _pd.to_datetime(tmp[time_col], errors="coerce")
            tmp[metric] = _pd.to_numeric(tmp[metric], errors="coerce")
            tmp = tmp.dropna(subset=[time_col, metric])
            if tmp.empty:
                return {}

            if compare == "yoy":
                tmp["_period"] = tmp[time_col].dt.to_period("Y")
            elif compare == "qoq":
                tmp["_period"] = tmp[time_col].dt.to_period("Q")
            else:
                tmp["_period"] = tmp[time_col].dt.to_period("M")

            periods = sorted(tmp["_period"].dropna().unique())
            if len(periods) < 2:
                return {}
            previous_period = periods[-2]
            current_period = periods[-1]

            prev_df = tmp[tmp["_period"] == previous_period]
            curr_df = tmp[tmp["_period"] == current_period]
            previous_total = float(prev_df[metric].sum())
            current_total = float(curr_df[metric].sum())
            absolute_change = current_total - previous_total
            relative_change = (absolute_change / previous_total) if previous_total else None

            contributions: list[dict] = []
            abs_sum = 0.0
            for dim in dims:
                prev_grouped = prev_df.groupby(dim, dropna=False)[metric].sum().rename("previous")
                curr_grouped = curr_df.groupby(dim, dropna=False)[metric].sum().rename("current")
                merged = _pd.concat([prev_grouped, curr_grouped], axis=1).fillna(0).reset_index()
                merged["contribution"] = merged["current"] - merged["previous"]
                for _, row in merged.iterrows():
                    contribution = float(row["contribution"])
                    abs_sum += abs(contribution)
                    contributions.append({
                        "dimension": dim,
                        "group": str(row[dim]),
                        "previous": float(row["previous"]),
                        "current": float(row["current"]),
                        "contribution": contribution,
                    })

            if not contributions:
                return {}

            ranked = sorted(contributions, key=lambda item: item["contribution"], reverse=True)
            top_positive = [item for item in ranked if item["contribution"] > 0][:top_k]
            top_negative = [item for item in sorted(contributions, key=lambda item: item["contribution"]) if item["contribution"] < 0][:top_k]
            dominant_abs = sorted((abs(item["contribution"]) for item in contributions), reverse=True)
            top_abs = sum(dominant_abs[:top_k])
            denominator = abs(absolute_change) if abs(absolute_change) > 0 else abs_sum
            coverage_ratio = (top_abs / denominator) if denominator else 0.0
            broad_based = False
            non_zero = [item for item in contributions if item["contribution"] != 0]
            if non_zero and absolute_change:
                aligned = sum(
                    1 for item in non_zero
                    if np.sign(item["contribution"]) == np.sign(absolute_change)
                )
                broad_based = aligned / len(non_zero) >= 0.6

            result = {
                "metric": metric,
                "compare": compare,
                "current_period": str(current_period),
                "previous_period": str(previous_period),
                "current_total": current_total,
                "previous_total": previous_total,
                "absolute_change": absolute_change,
                "relative_change": relative_change,
                "top_positive_contributors": top_positive,
                "top_negative_contributors": top_negative,
                "broad_based": broad_based,
                "coverage_ratio": float(coverage_ratio),
            }
            self.ns.setdefault("explanation_bundle", {})["metric_decomposition"] = result
            return result

        def _assess_evidence_level(
            *,
            has_quantitative_support: bool,
            has_group_breakdown: bool,
            has_time_window: bool,
            cross_slice_consistent: bool,
            relies_on_unobserved_assumption: bool,
        ) -> str:
            """Return A/B/C evidence level for an explanatory claim."""
            if has_quantitative_support and has_group_breakdown and has_time_window and not relies_on_unobserved_assumption:
                return "A"
            if has_quantitative_support and (has_group_breakdown or has_time_window):
                if cross_slice_consistent or not relies_on_unobserved_assumption:
                    return "B"
            return "C"

        def _rank_driver_candidates(dataframe, metric, time_col, dims, compare: str = "mom"):
            """Rank candidate drivers using contribution strength, coverage, and stability clues."""
            if isinstance(dims, str):
                dims = [dims]
            dims = _filter_explanation_dims(dataframe, [d for d in dims if d in dataframe.columns])
            if metric not in dataframe.columns or time_col not in dataframe.columns or not dims:
                return []

            decomposition = _decompose_metric_change(dataframe, metric, time_col, dims, compare=compare, top_k=5)
            if not decomposition:
                return []

            candidates: list[dict] = []
            top_negative = decomposition.get("top_negative_contributors") or []
            top_positive = decomposition.get("top_positive_contributors") or []
            focus_groups = top_negative if decomposition.get("absolute_change", 0) < 0 else top_positive
            compare_has_time = bool(decomposition.get("current_period") and decomposition.get("previous_period"))
            coverage_ratio = float(decomposition.get("coverage_ratio") or 0.0)

            for item in focus_groups[:5]:
                contribution = float(item.get("contribution") or 0.0)
                score = min(1.0, abs(contribution) / (abs(decomposition.get("absolute_change") or 0.0) + 1e-9))
                if decomposition.get("broad_based"):
                    score *= 0.9
                evidence_level = _assess_evidence_level(
                    has_quantitative_support=True,
                    has_group_breakdown=True,
                    has_time_window=compare_has_time,
                    cross_slice_consistent=coverage_ratio >= 0.5,
                    relies_on_unobserved_assumption=False,
                )
                direction = "decline" if contribution < 0 else "increase"
                candidates.append({
                    "driver": f"{item['dimension']}={item['group']} {direction}",
                    "dimension": item["dimension"],
                    "group": item["group"],
                    "contribution": contribution,
                    "evidence_level": evidence_level,
                    "score": round(score, 4),
                    "reason": (
                        f"{item['dimension']}={item['group']} contributed {contribution:.3g} during "
                        f"{decomposition['previous_period']} to {decomposition['current_period']}; "
                        f"coverage_ratio={coverage_ratio:.2f}"
                    ),
                })

            candidates.sort(key=lambda item: item["score"], reverse=True)
            self.ns.setdefault("explanation_bundle", {})["driver_ranking"] = candidates
            return candidates

        def _check_metric_definition_risk(dataframe, metric, time_col=None):
            """Check whether metric interpretation should be downgraded due to definition ambiguity."""
            columns_lower = {str(col).lower(): str(col) for col in dataframe.columns}
            risk_keywords = {
                "refund": ["refund", "退款", "return", "chargeback"],
                "cancellation": ["cancel", "取消", "void"],
                "duplicate": ["duplicate", "重复", "dup", "order_id", "transaction_id"],
                "backfill": ["backfill", "补录", "late", "adjustment", "调整"],
            }
            matched = {
                label: [orig for lower, orig in columns_lower.items() if any(keyword in lower for keyword in keywords)]
                for label, keywords in risk_keywords.items()
            }
            has_time_col = bool(time_col and time_col in dataframe.columns)
            time_granularity_mixed = False
            if has_time_col:
                parsed = pd.to_datetime(dataframe[time_col], errors="coerce")
                non_null = parsed.dropna()
                if not non_null.empty:
                    has_day = bool((non_null.dt.day != 1).any())
                    has_month_boundary = bool((non_null.dt.day == 1).any())
                    time_granularity_mixed = has_day and has_month_boundary

            duplicate_ratio = 0.0
            duplicate_fields = matched["duplicate"]
            for field in duplicate_fields:
                if field in dataframe.columns:
                    duplicate_ratio = max(duplicate_ratio, float(dataframe[field].duplicated().mean()))

            risk_count = sum(bool(values) for values in matched.values())
            if time_granularity_mixed:
                risk_count += 1
            if duplicate_ratio > 0.05:
                risk_count += 1

            result = {
                "metric": metric,
                "time_col": time_col,
                "has_refund_or_return_fields": bool(matched["refund"]),
                "has_cancellation_fields": bool(matched["cancellation"]),
                "has_backfill_fields": bool(matched["backfill"]),
                "possible_duplicate_keys": duplicate_fields,
                "time_granularity_mixed": time_granularity_mixed,
                "duplicate_ratio": round(duplicate_ratio, 4),
                "exploratory_only": risk_count >= 2,
                "notes": [
                    f"{label}: {', '.join(values)}" for label, values in matched.items() if values
                ] + (["mixed time granularity detected"] if time_granularity_mixed else []),
            }
            self.ns.setdefault("explanation_bundle", {})["definition_risk"] = result
            return result

        def _run_counterfactual_checks(dataframe, metric, key_dimension, time_col=None):
            """Run lightweight robustness checks for explanatory claims."""
            if metric not in dataframe.columns or key_dimension not in dataframe.columns:
                return []

            metric_series = pd.to_numeric(dataframe[metric], errors="coerce")
            base_total = float(metric_series.sum()) if metric_series.notna().any() else 0.0
            grouped = dataframe.assign(_metric=metric_series).groupby(key_dimension, dropna=False)["_metric"].sum().sort_values(ascending=False)
            checks: list[dict] = []
            if grouped.empty:
                return checks

            lead_group = grouped.index[0]
            without_top = dataframe[dataframe[key_dimension] != lead_group]
            without_top_total = float(pd.to_numeric(without_top[metric], errors="coerce").sum()) if not without_top.empty else 0.0
            checks.append({
                "check": "remove_top_group",
                "dimension": key_dimension,
                "excluded_group": str(lead_group),
                "base_total": base_total,
                "adjusted_total": without_top_total,
                "status": "stable" if np.sign(base_total) == np.sign(without_top_total) else "unstable",
            })

            if time_col and time_col in dataframe.columns:
                tmp = dataframe[[time_col, metric]].copy()
                tmp[time_col] = pd.to_datetime(tmp[time_col], errors="coerce")
                tmp[metric] = pd.to_numeric(tmp[metric], errors="coerce")
                tmp = tmp.dropna(subset=[time_col, metric]).sort_values(time_col)
                if len(tmp) >= 4:
                    tmp["period"] = tmp[time_col].dt.to_period("M")
                    monthly = tmp.groupby("period")[metric].sum().sort_index()
                    if len(monthly) >= 3:
                        last_change = monthly.iloc[-1] - monthly.iloc[-2]
                        rolling_change = monthly.iloc[-1] - monthly.iloc[-3]
                        checks.append({
                            "check": "alternate_window",
                            "dimension": key_dimension,
                            "last_change": float(last_change),
                            "rolling_change": float(rolling_change),
                            "status": "stable" if np.sign(last_change) == np.sign(rolling_change) else "partial",
                        })

            self.ns.setdefault("explanation_bundle", {})["counterfactual_checks"] = checks
            return checks

        def _generate_recommendation_candidates(findings, drivers):
            """Generate recommendation candidates constrained by evidence strength and robustness."""
            findings = findings or []
            drivers = drivers or []
            recommendations: list[dict] = []
            explanation_bundle = self.ns.setdefault("explanation_bundle", {})
            definition_risk = explanation_bundle.get("definition_risk") or {}
            counterfactual_checks = explanation_bundle.get("counterfactual_checks") or []
            metric_decomposition = explanation_bundle.get("metric_decomposition") or {}

            def _normalize_level(item):
                return str(item.get("evidence_level") or item.get("evidence") or "C").upper()

            stable_checks = [item for item in counterfactual_checks if str(item.get("status") or "").lower() == "stable"]
            unstable_checks = [item for item in counterfactual_checks if str(item.get("status") or "").lower() not in {"", "stable"}]
            broad_based = bool(metric_decomposition.get("broad_based"))
            decomposition_coverage = float(metric_decomposition.get("coverage_ratio") or 0.0)
            exploratory_only = bool(definition_risk.get("exploratory_only"))
            absolute_change = abs(float(metric_decomposition.get("absolute_change") or 0.0))

            for idx, driver in enumerate(drivers, start=1):
                level = _normalize_level(driver)
                based_on = list(driver.get("based_on") or []) or [f"driver_{idx}"]
                target = driver.get("driver") or driver.get("dimension") or driver.get("reason") or "current driver"
                priority = "high" if level == "A" else "medium" if level == "B" else "low"
                score = float(driver.get("score") or 0.0)
                contribution = abs(float(driver.get("contribution") or 0.0))
                contribution_share = (contribution / absolute_change) if absolute_change > 0 else 0.0
                driver_is_concentrated = (
                    not broad_based
                    and decomposition_coverage >= 0.65
                    and score >= 0.75
                    and contribution_share >= 0.35
                )
                robust_enough = bool(stable_checks) and not unstable_checks

                if level == "A" and driver_is_concentrated and robust_enough and not exploratory_only:
                    recommendations.append({
                        "type": "action",
                        "priority": priority,
                        "recommendation": f"prioritize review of {target}",
                        "based_on": based_on,
                        "evidence_level": level,
                    })
                elif level in {"A", "B"}:
                    recommendations.append({
                        "type": "validation",
                        "priority": "medium" if level == "A" else priority,
                        "recommendation": f"validate whether {target} is a stable driver before taking action",
                        "based_on": based_on,
                        "evidence_level": level,
                    })
                else:
                    recommendations.append({
                        "type": "validation",
                        "priority": priority,
                        "recommendation": f"validate whether {target} is a stable driver before taking action",
                        "based_on": based_on,
                        "evidence_level": level,
                    })

            if not recommendations:
                for idx, finding in enumerate(findings, start=1):
                    level = _normalize_level(finding)
                    summary = finding.get("finding") or finding.get("summary") or finding.get("metric") or f"finding_{idx}"
                    recommendation_type = "observe" if level == "A" and not exploratory_only else "validation"
                    recommendations.append({
                        "type": recommendation_type,
                        "priority": "medium" if level in {"A", "B"} else "low",
                        "recommendation": f"continue monitoring {summary}" if recommendation_type == "observe" else f"validate {summary} before operationalizing it",
                        "based_on": [f"finding_{idx}"],
                        "evidence_level": level,
                    })

            explanation_bundle["recommendations"] = recommendations
            return recommendations

        self.ns: dict = {
            "WORKSPACE_DIR": str(_ws),
            "SOURCE_PATH": source_path,
            "Path": Path,
            "save_fig": _save_fig,
            "load_csv": _load_csv,
            "safe_workspace_path": lambda filename: _safe_workspace_path(_ws, filename),
            "fix_chinese": _fix_chinese,
            "profile_dimension": _profile_dimension,
            "compare_segments": _compare_segments,
            "time_trend": _time_trend,
            "detect_anomalies": _detect_anomalies,
            "explain_metric_change": _explain_metric_change,
            "decompose_metric_change": _decompose_metric_change,
            "assess_evidence_level": _assess_evidence_level,
            "rank_driver_candidates": _rank_driver_candidates,
            "check_metric_definition_risk": _check_metric_definition_risk,
            "run_counterfactual_checks": _run_counterfactual_checks,
            "generate_recommendation_candidates": _generate_recommendation_candidates,
            "filter_explanation_dims": _filter_explanation_dims,
            "filter_business_metrics": _filter_business_metrics,
            "support_label": _support_label,
            "explanation_bundle": {},
        }
        self.new_artifacts: List[Dict] = []
        self.known_image_files: set = set()
        self.process_log: List[str] = []
        self.report: Optional[str] = None
        self.pending_report_markdown: Optional[str] = None
        self.findings: List[Finding] = []
        self.metric_definitions: List[MetricDefinition] = []
        self.assumptions: List[AnalysisAssumption] = []
        self.cancel_event: asyncio.Event = asyncio.Event()
        self.pause_event: asyncio.Event = asyncio.Event()
        self.consecutive_python_errors = 0
        self.last_progress_marker = ""
        self.logger = _make_session_logger(self.workspace_dir, session_id or "default")

        # Structured logger for machine-readable logs
        self.structured_logger = StructuredLogger(
            workspace_dir=self.workspace_dir,
            session_id=session_id or "default",
        )

        # Domain-neutral task/execution metadata. The existing session namespace
        # remains the execution compatibility layer during the incremental migration.
        from langgraph_langchain.runtime import AnalysisRuntime
        try:
            self.analysis_runtime = AnalysisRuntime(
                workspace_dir=self.workspace_dir,
                session_id=session_id or "default",
                question=user_question,
                external_context=semantic_context,
                force_new_run=force_new_run,
                parent_run_id=parent_run_id,
                retry_of_step_id=retry_of_step_id,
                plan_first=True,
            )
        except Exception as exc:
            self.logger.warning("analysis_runtime_init_failed session=%s error=%s", session_id, exc)
            if force_new_run:
                raise
            self.analysis_runtime = None
        self.current_runtime_step_id: Optional[str] = None
        self.current_asset_id: Optional[str] = None
        self.ns["semantic_context"] = semantic_context

        # State machine for tracking analysis progress
        from .state_machine import AnalysisStateMachine
        self.state_machine = AnalysisStateMachine()

        # Restore from saved state if provided (resume flow)
        if restore_state:
            self._apply_restore_state(restore_state)
        else:
            self.start_stage("schema_understanding")

    def _apply_restore_state(self, state: Dict) -> None:
        """Rebuild session from a previously saved state dict.

        Restores stage, findings, step count, and state machine progress.
        The namespace is rebuilt fresh — the agent will re-load data via
        ``load_data`` on resume, which populates ``df`` etc.
        """
        from langgraph_langchain.session_persistence import SessionPersistence
        from .state_machine import AnalysisStage as _Stage

        self.user_question = state.get("user_question", self.user_question)
        self.current_stage = state.get("current_stage", self.current_stage)
        self.total_steps = state.get("total_steps", 0)
        self.consecutive_python_errors = state.get("consecutive_python_errors", 0)
        self.last_progress_marker = state.get("last_progress_marker", "")

        # Restore structured data
        if state.get("findings"):
            self.findings = SessionPersistence.deserialize_findings(state["findings"])
        if state.get("metric_definitions"):
            self.metric_definitions = SessionPersistence.deserialize_metric_definitions(
                state["metric_definitions"]
            )
        if state.get("assumptions"):
            self.assumptions = SessionPersistence.deserialize_assumptions(state["assumptions"])

        # Restore known image files
        for p in state.get("known_image_files", []):
            self.known_image_files.add(Path(p))

        # Restore report state (shouldn't be set if we're resuming, but just in case)
        self.report = state.get("report")
        self.pending_report_markdown = state.get("pending_report_markdown")

        # Restore state machine
        sm_state = state.get("state_machine")
        if sm_state:
            sm = self.state_machine
            # Restore current stage
            try:
                sm.current_stage = _Stage(sm_state["current_stage"])
            except (KeyError, ValueError):
                pass
            # Restore stage history
            sm.stage_history = []
            for s in sm_state.get("stage_history", []):
                try:
                    sm.stage_history.append(_Stage(s))
                except ValueError:
                    pass
            # Restore conditions met
            sm.conditions_met = set(sm_state.get("conditions_met", []))
            # Restore tools used
            sm.tools_used = sm_state.get("tools_used", [])
            # Restore stage step counts
            sm.stage_step_count = {}
            for stage_val, count in sm_state.get("stage_step_count", {}).items():
                try:
                    sm.stage_step_count[_Stage(stage_val)] = count
                except ValueError:
                    pass

        self.logger.info(
            "session_restored session=%s stage=%s steps=%d findings=%d",
            self.session_id,
            self.current_stage,
            self.total_steps,
            len(self.findings),
        )

    def cleanup(self) -> None:
        """Release heavy resources after analysis completes or fails.

        Clears the persistent exec namespace (dropping DataFrames) and trims
        artifact metadata so the Session object can be garbage-collected
        promptly after the generator finishes.
        """
        # Drop large objects from namespace — keep only primitives
        heavy_keys = [k for k, v in self.ns.items()
                      if isinstance(v, (pd.DataFrame, np.ndarray))]
        for k in heavy_keys:
            del self.ns[k]

        # Clear the entire namespace to break reference cycles
        self.ns.clear()

        # Trim artifact list — keep only the last N entries for the API response
        if len(self.new_artifacts) > _MAX_RETAINED_ARTIFACTS:
            self.new_artifacts = self.new_artifacts[-_MAX_RETAINED_ARTIFACTS:]

        # Clear process log — already written into the report
        self.process_log.clear()

        self.logger.info(
            "session_cleanup session=%s cleared_namespace_keys=%s artifacts_kept=%d",
            self.session_id, heavy_keys, len(self.new_artifacts),
        )

    def start_stage(self, stage: AnalysisStage) -> None:
        from langgraph_langchain.state_machine import AnalysisStage as StateMachineStage

        target_stage = StateMachineStage(stage)
        if self.state_machine.current_stage != target_stage:
            can_transition, reason = self.state_machine.can_transition_to(target_stage)
            if not can_transition or not self.state_machine.transition_to(target_stage):
                self.logger.warning(
                    "stage_transition_rejected session=%s from=%s to=%s reason=%s",
                    self.session_id,
                    self.state_machine.current_stage.value,
                    target_stage.value,
                    reason,
                )
                self.current_stage = self.state_machine.current_stage
                return
        if self.stage_history and self.stage_history[-1].stage == target_stage and self.stage_history[-1].status == "started":
            return
        self.current_stage = target_stage
        self.stage_history.append(StageResult(stage=target_stage, status="started", started_at=datetime.now(timezone.utc)))
        self.logger.info("stage_started session=%s stage=%s", self.session_id, target_stage.value)
        self.structured_logger.log_stage_start(target_stage)

    def complete_stage(self, stage: AnalysisStage) -> None:
        from langgraph_langchain.state_machine import AnalysisStage as StateMachineStage

        completed_stage = StateMachineStage(stage)
        self.current_stage = self.state_machine.current_stage
        self.stage_history.append(StageResult(stage=completed_stage, status="completed", completed_at=datetime.now(timezone.utc)))
        self.logger.info("stage_completed session=%s stage=%s", self.session_id, completed_stage.value)
        self.structured_logger.log_stage_complete(completed_stage)

    def try_advance_stage(self) -> Optional[str]:
        """
        Try to advance to the next stage based on state machine rules.
        Returns error message if transition is not allowed, None if successful or no transition needed.
        """
        next_stage = self.state_machine.get_next_recommended_stage()
        if next_stage is None:
            return None

        can_transition, reason = self.state_machine.can_transition_to(next_stage)
        if can_transition:
            success = self.state_machine.transition_to(next_stage)
            if success:
                self.start_stage(next_stage)
                self.logger.info(
                    "stage_auto_advanced session=%s from=%s to=%s",
                    self.session_id,
                    self.state_machine.stage_history[-2].value if len(self.state_machine.stage_history) > 1 else "init",
                    next_stage.value
                )
                return None

        return reason

    def fail_stage(
        self,
        stage: AnalysisStage,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        hint: Optional[str] = None,
        recovery_action: Optional[RecoveryAction] = None,
    ) -> FailureInfo:
        self.current_stage = stage
        failure = FailureInfo(
            code=code,
            message=message,
            retryable=retryable,
            hint=hint,
            recovery_action=recovery_action,
            stage=stage,
        )
        self.stage_failures.append(failure)
        self.stage_history.append(StageResult(stage=stage, status="failed", failure=failure, completed_at=datetime.now(timezone.utc)))
        self.logger.warning(
            "stage_failed session=%s stage=%s code=%s retryable=%s message=%s",
            self.session_id,
            stage,
            code,
            retryable,
            message,
        )
        self.structured_logger.log_stage_fail(stage, code, message, retryable=retryable)
        return failure

    # Patterns that indicate dangerous operations in agent-generated code
    _DANGEROUS_PATTERNS = re.compile(
        r"(?:"
        r"\bos\.system\b"
        r"|\bsubprocess\b"
        r"|\bos\.popen\b"
        r"|\bshutil\.rmtree\b"
        r"|\bopen\s*\(\s*['\"]/(?:etc|proc|sys|dev|root)"
        r"|\b__import__\b"
        r"|\beval\s*\("
        r"|\bexec\s*\("
        r"|\bcompile\s*\("
        r"|\bos\.remove\b"
        r"|\bos\.unlink\b"
        r")",
    )

    def _validate_code_safety(self, code: str) -> Optional[str]:
        """Return error message if code contains dangerous patterns."""
        match = self._DANGEROUS_PATTERNS.search(code)
        if match:
            return (
                f"[ERROR] Code contains forbidden operation: '{match.group()}'. "
                "Only data analysis operations (pandas, numpy, matplotlib, seaborn) are allowed."
            )
        return None

    def run_code(self, code: str) -> str:
        """Execute code in a bounded worker process and merge safe state updates."""
        safety_error = self._validate_code_safety(code)
        if safety_error:
            return safety_error

        # Pre-validate syntax before executing — catch SyntaxError with a
        # helpful message so the LLM can self-correct instead of burning
        # through its consecutive-error budget on un-parseable code.
        try:
            compile(code, "<agent>", "exec")
        except SyntaxError as e:
            offending_line = e.text.strip() if e.text else ""
            # Detect common LLM mistakes where operators (> < >= <= == !=)
            # are embedded in dictionary keys or variable names
            # (e.g. 产出>0记录数=...) which Python cannot parse.
            hint = ""
            if e.msg == "invalid decimal literal" or (
                e.msg in ("invalid syntax", "invalid syntax. Perhaps you forgot a comma?")
                and offending_line
                and re.search(r"[><=!]=?", offending_line)
                and re.search(r"[一-鿿]", offending_line)
            ):
                hint = (
                    " This usually means a dictionary key or variable name "
                    "contains an operator like > or < (e.g. `产出>0记录数`). "
                    "Fix: use a plain string key in quotes instead, "
                    "e.g. `{\"产出>0记录数\": (\"产出总数\", lambda x: (x>0).sum())}`."
                )
            error_lines = code.splitlines()
            line_info = ""
            display_message = "invalid decimal literal" if hint else e.msg
            if 1 <= e.lineno <= len(error_lines):
                line_info = f"\n  Line {e.lineno}: {error_lines[e.lineno - 1]}"
            return (
                f"[ERROR] SyntaxError: {display_message}.{hint}\n"
                f"  Offending text: {offending_line}{line_info}\n"
                "Please fix the syntax error and retry."
            )

        from langgraph_langchain.execution import IsolatedPythonExecutor, PythonExecutionRequest

        result = IsolatedPythonExecutor().execute(PythonExecutionRequest(
            code=code,
            workspace_dir=str(self.workspace_dir),
            source_path=self.source_path,
            namespace=self.ns,
            timeout_seconds=_CODE_TIMEOUT,
            max_output_chars=_MAX_OUTPUT_LEN,
            input_asset_ids=[self.current_asset_id] if self.current_asset_id else [],
            allowed_directories=[str(self.workspace_dir)],
        ), cancel_event=self.cancel_event)
        if result.status == "succeeded":
            # The worker never receives closures or service objects. Merge only
            # serializable analysis values, retaining parent-side helper functions.
            self.ns.update(result.namespace_updates)
            return result.output
        if result.status == "timed_out":
            return (
                f"[ERROR] Execution timed out after {_CODE_TIMEOUT}s and the worker was terminated. "
                "Break the code into smaller steps and retry."
            )
        if result.status == "cancelled":
            return "[ERROR] Execution was cancelled and the worker was terminated."
        details = result.output.strip() or result.error_message or "Python worker failed"
        return f"[ERROR]\n{details}"


# ── Tools ─────────────────────────────────────────────────────────────────────
def _make_tools(session: _Session) -> list:
    """Build tools for this session. Delegates to the tools package registry."""
    from langgraph_langchain.tools import get_tools_for_session
    return get_tools_for_session(session)


def _save_session_metadata(session: _Session, total_steps: int, duration_seconds: float) -> None:
    """Save session metadata to workspace for API layer to read."""
    import json
    metadata_path = session.workspace_dir / "session_metadata.json"
    metadata = {
        "session_id": session.session_id,
        "total_steps": total_steps,
        "duration_seconds": duration_seconds,
        "current_stage": session.current_stage,
        "stage_history": [
            {
                "stage": sr.stage,
                "status": sr.status,
                "started_at": sr.started_at.isoformat() if sr.started_at else None,
                "completed_at": sr.completed_at.isoformat() if sr.completed_at else None,
            }
            for sr in session.stage_history
        ],
    }
    try:
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    except Exception:
        pass  # Best effort


# ── Safety-net report synthesis ──────────────────────────────────────────────
def _synthesize_report_from_findings(session: "_Session", reason: str) -> str:
    """Build a fallback markdown report from recorded findings + process log.

    Used when the agent terminates without calling ``finish_report`` itself
    (step budget exhausted / recursion limit / silent termination). The report
    is built ONLY from real recorded findings, so callers must ensure
    ``len(session.findings) >= _SYNTH_REPORT_MIN_FINDINGS`` first.

    Bypasses ``finish_report``'s strict validator on purpose - the goal is to
    guarantee the user gets *some* structured output rather than none. The
    ``reason`` string is surfaced in the report header for transparency.
    """
    lines: List[str] = []
    lines.append("# 数据分析报告(基于已记录发现自动生成)")
    lines.append("")
    lines.append(
        f"> 注:agent 未显式调用 finish_report 即结束,系统基于 "
        f"{len(session.findings)} 条已记录发现自动整理生成本报告。"
    )
    lines.append(f"> 生成原因:{reason}。")
    lines.append("")

    # Summary
    lines.append("## Summary / 摘要")
    lines.append("")
    task = session.user_question or "(未提供分析任务)"
    lines.append(f"**分析任务**:{task}")
    lines.append("")
    lines.append(f"**数据文件**:`{session.source_path}`")
    lines.append("")
    lines.append("**核心结论**:")
    for f in session.findings:
        flag = "(假设,待验证)" if f.hypothesis_flag else ""
        lines.append(f"- {f.statement} {flag}")
    lines.append("")

    # Data Context
    lines.append("## Data Context / 数据说明")
    lines.append("")
    if session.metric_definitions:
        lines.append("**指标定义**:")
        for m in session.metric_definitions:
            bits = []
            if m.time_window:
                bits.append(f"时间窗口:{m.time_window}")
            if m.dedup_rule:
                bits.append(f"去重规则:{m.dedup_rule}")
            tail = (";" + " · ".join(bits)) if bits else ""
            lines.append(f"- **{m.metric_name}**:{m.definition_text}{tail}")
        lines.append("")
    if session.assumptions:
        lines.append("**分析假设**:")
        for a in session.assumptions:
            lines.append(f"- (风险:{a.risk_level}) {a.assumption_text}")
        lines.append("")
    lines.append("")

    # Key Findings
    lines.append("## Key Findings / 关键发现")
    lines.append("")
    for f in session.findings:
        lines.append(f"### {f.finding_id} - {f.statement}")
        lines.append("")
        meta_bits = [f"证据等级:{f.evidence_level}", f"置信度:{f.confidence_level}"]
        if f.category:
            meta_bits.append(f"类别:{f.category}")
        if f.hypothesis_flag:
            meta_bits.append("假设(待验证)")
        lines.append(f"*{' · '.join(meta_bits)}*")
        lines.append("")
        for ev in f.evidence:
            lines.append(f"- {ev.evidence_text}")
            bits = []
            if ev.time_window:
                bits.append(f"时间窗口:{ev.time_window}")
            if ev.group_dimension:
                bits.append(f"分组维度:{ev.group_dimension}")
            if ev.source_fields:
                bits.append(f"字段:{', '.join(ev.source_fields)}")
            if ev.calculation_method:
                bits.append(f"计算方法:{ev.calculation_method}")
            if bits:
                lines.append(f"  - {' · '.join(bits)}")
            if ev.stats:
                try:
                    stats_str = ", ".join(f"{k}={v}" for k, v in ev.stats.items())
                    lines.append(f"  - 统计:{stats_str}")
                except Exception:
                    pass
        lines.append("")

    # Data Quality
    lines.append("## Data Quality / 数据质量")
    lines.append("")
    dq_findings = [
        f for f in session.findings
        if f.category and "quality" in (f.category or "").lower()
    ]
    if dq_findings:
        for f in dq_findings:
            lines.append(f"- {f.statement}")
    else:
        lines.append("- 数据质量相关发现见 EDA 与关键发现部分;未见显著数据质量阻断问题。")
    lines.append("")

    # Analysis - bounded excerpt of the process log
    lines.append("## Analysis / 分析过程")
    lines.append("")
    process_text = "".join(session.process_log).strip()
    if process_text:
        if len(process_text) > 4000:
            process_text = process_text[:4000] + "\n\n...(分析过程记录已截断)"
        lines.append(process_text)
    else:
        lines.append("(无详细过程记录)")
    lines.append("")

    # Recommendations
    lines.append("## Recommendations / 建议")
    lines.append("")
    hyp_findings = [f for f in session.findings if f.hypothesis_flag]
    if hyp_findings:
        lines.append("以下为待验证的假设,建议进一步验证:")
        for f in hyp_findings:
            lines.append(f"- 验证:{f.statement}")
        lines.append("")
    lines.append("- 建议结合业务背景对上述发现进行复核,并在数据口径确认后推进下一步行动。")
    lines.append("")

    return "\n".join(lines)


def _finalize_synthesized_report(
    session: "_Session",
    step: int,
    start_time: float,
    trace_context,
    reason: str,
    log: logging.Logger,
    session_id: str,
):
    """Build + persist a fallback report, end trace, save metadata.

    Returns ``(report_md, artifacts)``. The caller is responsible for yielding
    ``report_md`` (and the artifacts) and returning from the generator.
    """
    report_md = _synthesize_report_from_findings(session, reason)
    session.report = report_md
    artifacts = list(session.new_artifacts)
    session.new_artifacts.clear()
    try:
        report_path = session.workspace_dir / "final_report.md"
        report_path.write_text(report_md, encoding="utf-8")
        rel = report_path.relative_to(session.workspace_dir.parent)
        report_artifact = {
            "name": report_path.name,
            "path": str(report_path),
            "relative_path": str(rel),
            "url": f"/workspace/files/{rel}",
        }
        artifacts = [report_artifact] + [
            a for a in artifacts if a.get("name") != report_path.name
        ]
        log.info("synthesized_report_saved session=%s path=%s", session_id, report_path)
    except Exception:
        log.debug("synthesized_report_save_failed session=%s", session_id)
    _save_session_metadata(session, step, time.monotonic() - start_time)
    _set_runtime_run_status(session, "completed")
    trace_context.end_trace()
    trace_context.save_to_file(session.workspace_dir)
    return report_md, artifacts


def _runtime_step_spec(tool_name: str) -> tuple[str, list[str]]:
    specs = {
        "load_data": ("Load and inspect the source dataset", ["data_asset", "schema_snapshot"]),
        "eda_profile": ("Profile data quality and distributions", ["eda_profile"]),
        "python_repl": ("Execute an analytical computation", ["analysis_output"]),
        "compare_groups": ("Compare a metric across groups", ["group_comparison", "table"]),
        "analyze_time_trend": ("Analyze a metric over time", ["time_trend", "table"]),
        "decompose_contribution": ("Decompose a period change by group", ["contribution_decomposition", "table"]),
        "detect_anomalies": ("Detect numeric anomalies", ["anomaly_result", "table"]),
        "record_finding": ("Record a structured evidence-backed finding", ["finding"]),
        "delegate_analysis": ("Delegate a bounded analysis subtask", ["delegated_result"]),
        "finish_report": ("Validate and produce the final analysis report", ["report"]),
    }
    return specs.get(tool_name, (f"Execute {tool_name}", ["tool_result"]))


def _start_runtime_step(session: "_Session", tool_name: str) -> Optional[str]:
    from langgraph_langchain.runtime.context import AnalysisRuntime

    runtime = getattr(session, "analysis_runtime", None)
    if not isinstance(runtime, AnalysisRuntime):
        return None
    objective, expected_outputs = _runtime_step_spec(tool_name)
    try:
        previous_step_ids = [runtime.run.steps[-1].step_id] if runtime.run.steps else []
        runtime_step = runtime.start_step(
            objective=objective,
            method=tool_name,
            expected_outputs=expected_outputs,
            depends_on=previous_step_ids,
        )
        session.current_runtime_step_id = runtime_step.step_id
        return runtime_step.step_id
    except Exception as exc:
        session.logger.warning("runtime_step_start_failed tool=%s error=%s", tool_name, exc)
        return None


def _complete_runtime_step(
    session: "_Session",
    step_id: Optional[str],
    *,
    status: str,
    error: Optional[str] = None,
) -> None:
    from langgraph_langchain.runtime.context import AnalysisRuntime

    runtime = getattr(session, "analysis_runtime", None)
    if not step_id or not isinstance(runtime, AnalysisRuntime):
        return
    try:
        runtime.complete_step(step_id, status=status, error=error)
    except Exception as exc:
        session.logger.warning("runtime_step_complete_failed step_id=%s error=%s", step_id, exc)
    finally:
        if session.current_runtime_step_id == step_id:
            session.current_runtime_step_id = None


def _bind_runtime_execution_step(
    session: "_Session", tool_name: str, step_id: Optional[str]
) -> None:
    """Backfill lineage when tool execution finishes before its stream event is consumed."""
    runtime = getattr(session, "analysis_runtime", None)
    if not step_id or runtime is None:
        return
    execution = next(
        (
            item
            for item in reversed(getattr(runtime, "executions", []) or [])
            if item.tool_name == tool_name and not item.step_id
        ),
        None,
    )
    if execution is None:
        return
    execution.step_id = step_id
    for artifact_id in execution.output_artifact_ids:
        artifact = (getattr(runtime, "artifacts", {}) or {}).get(artifact_id)
        if artifact is not None and not artifact.step_id:
            artifact.step_id = step_id
    runtime.persist()


def _set_runtime_run_status(
    session: "_Session", status: str, *, error: Optional[str] = None
) -> None:
    from langgraph_langchain.runtime.context import AnalysisRuntime

    runtime = getattr(session, "analysis_runtime", None)
    if not isinstance(runtime, AnalysisRuntime):
        return
    try:
        runtime.set_run_status(status, error=error)
    except Exception as exc:
        session.logger.warning("runtime_run_status_failed status=%s error=%s", status, exc)
    finally:
        session.current_runtime_step_id = None


def _tool_output_status(output: str) -> str:
    normalized = output.lstrip()
    if normalized.startswith(("REPORT REJECTED", "[REPORT REJECTED]")):
        return "needs_revision"
    if normalized.startswith(("[ERROR]", "ERROR:")):
        return "failed"
    if normalized.startswith("{"):
        try:
            parsed = json.loads(normalized)
            if isinstance(parsed, dict) and parsed.get("error"):
                return "failed"
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return "succeeded"


async def _run_runtime_v2_graph_stream(
    session: "_Session",
    *,
    tools: List,
    source_path: str,
    react_agent=None,
) -> AsyncGenerator[tuple[str, list], None]:
    """Try to execute a structured plan through Runtime V2.

    This is an opt-in bridge.  When a plan contains LLM-decided tasks, has
    unknown tools, or fails to produce a report, the caller falls back to the
    legacy ReAct stream.
    """
    from langgraph_langchain.evidence import EvidenceCollector
    from langgraph_langchain.execution.python_task_executor import PythonTaskExecutor
    from langgraph_langchain.execution.react_executor import ReactTaskExecutor
    from langgraph_langchain.execution.structured_executor import StructuredTaskExecutor
    from langgraph_langchain.runtime.context import register_session_artifact
    from langgraph_langchain.runtime.plans import AnalysisPlan
    from langgraph_langchain.runtime.skill_retriever import SkillRetriever
    from langgraph_langchain.verification import verify_execution_result

    session.runtime_v2_fallback = False
    runtime = getattr(session, "analysis_runtime", None)
    if runtime is None or not runtime.run.plan:
        session.runtime_v2_fallback = _RUNTIME_V2_FALLBACK_ENABLED
        yield "\n\nRuntime V2 fallback: no structured plan.\n", []
        return

    try:
        plan = AnalysisPlan.model_validate(runtime.run.plan)
    except Exception as exc:
        session.runtime_v2_fallback = _RUNTIME_V2_FALLBACK_ENABLED
        session.logger.warning("runtime_v2_plan_restore_failed error=%s", exc)
        yield "\n\nRuntime V2 fallback: invalid structured plan.\n", []
        return

    runtime.initialize_plan(
        plan,
        max_running_tasks=1,
        argument_defaults_by_method={
            "load_data": {"file_path": source_path, "sheet_name": ""},
        },
    )

    tool_map = {getattr(tool, "name", ""): tool for tool in tools}
    unsupported = sorted({
        step.method
        for step in plan.steps
        if step.method not in tool_map
        and step.method != "python_repl"
        and not step.method.startswith("react_")
    })
    if unsupported:
        session.runtime_v2_fallback = _RUNTIME_V2_FALLBACK_ENABLED
        yield (
            "\n\nRuntime V2 fallback: unsupported structured tools "
            f"({', '.join(unsupported)}).\n",
            [],
        )
        return

    session.runtime_v2_completed = False

    def on_task_start(task) -> None:
        session.current_runtime_step_id = task.plan_step_id or task.task_id

    def on_task_finish(result) -> None:
        runtime.record_execution_result(result)
        _complete_runtime_step(
            session,
            result.step_id or result.task_id,
            status=result.status,
            error=(
                result.error.get("message")
                if isinstance(result.error, dict) and result.error.get("message")
                else None
            ),
        )
        session.total_steps = getattr(session, "total_steps", 0) + 1
        if result.status == "succeeded":
            if result.tool_name == "load_data":
                session.complete_stage("schema_understanding")
            elif result.tool_name == "eda_profile":
                session.complete_stage("data_quality_check")
            elif result.tool_name in {
                "analyze_time_trend",
                "compare_groups",
                "decompose_contribution",
            }:
                session.state_machine.current_stage = AnalysisStage.DEEP_DIVE
                session.current_stage = AnalysisStage.DEEP_DIVE

    runtime.execution_namespace = session.ns

    def _python_artifact_recorder(raw_artifact: dict) -> dict:
        relative_path = raw_artifact.get("relative_path")
        if not relative_path:
            raise ValueError("Python execution artifact is missing relative_path")
        metadata = register_session_artifact(
            session,
            runtime.workspace_dir / relative_path,
            created_by_tool="python_repl",
            step_id=session.current_runtime_step_id,
        )
        return metadata

    controller = runtime.build_execution_controller(
        structured_executor=StructuredTaskExecutor(
            tool_resolver=lambda name: tool_map.get(name),
        ),
        react_executor=ReactTaskExecutor(
            agent=react_agent,
            prompt_builder=lambda request: (
                f"Task: {request.task.question}\n"
                f"Data file: {source_path}\n"
                "Use the available analysis tools and finish with a concise result."
            ),
        ),
        python_executor=PythonTaskExecutor(
            workspace_dir=runtime.workspace_dir,
            source_path=source_path,
            timeout_seconds=float(_CODE_TIMEOUT),
            artifact_recorder=_python_artifact_recorder,
        ),
        on_task_start=on_task_start,
        on_task_finish=on_task_finish,
        skill_retriever=SkillRetriever(_skills_loader),
        verifier=lambda result: verify_execution_result(
            result,
            artifacts=runtime.artifacts,
            workspace_dir=runtime.workspace_dir,
            context={
                "run_id": result.run_id,
                "task_id": result.task_id,
                "step_id": result.step_id,
            },
        ),
        evidence_factory=lambda result, verifications: EvidenceCollector().collect(
            execution=result,
            verification_results=verifications,
            artifacts=runtime.artifacts,
        ),
    )

    try:
        async for update in controller.graph.astream({}):
            result = update.get("result") if isinstance(update, dict) else None
            if result is not None:
                if result.status == "succeeded":
                    preview = _format_preview_text(result.stdout_preview)
                    if preview:
                        msg = f"\n> `{result.tool_name} done`\n\n{preview}\n\n"
                        session.process_log.append(msg)
                        yield msg, []
                else:
                    message = (
                        result.error.get("message", "")
                        if isinstance(result.error, dict)
                        else ""
                    )
                    msg = f"\n> `{result.tool_name} failed`\n\n{message}\n\n"
                    session.process_log.append(msg)
                    yield msg, []

            if session.new_artifacts:
                artifacts = list(session.new_artifacts)
                session.new_artifacts.clear()
                session.logger.info("runtime_v2_artifacts_flushed count=%d", len(artifacts))
                yield "", artifacts

            if update.get("action") in {"failed", "blocked", "incomplete", "limit_reached"}:
                # A failed task is business-owned by Runtime. It should be retried
                # or replanned later; only scheduling/runtime infrastructure is an
                # emergency fallback boundary.
                infrastructure_failure = update.get("action") in {
                    "blocked",
                    "incomplete",
                    "limit_reached",
                } and update.get("result") is None
                session.runtime_v2_fallback = bool(
                    infrastructure_failure and _RUNTIME_V2_FALLBACK_ENABLED
                )
                _set_runtime_run_status(
                    session,
                    "failed",
                    error=update.get("reason") or "Runtime V2 plan did not complete",
                )
                yield "\n\n**Runtime V2 plan stopped.**\n", []
                return

        all_succeeded = all(task.status == "succeeded" for task in controller.scheduler.tasks)
        if not all_succeeded:
            session.runtime_v2_fallback = False
            yield "\n\nRuntime V2 stopped: plan did not finish successfully.\n", []
            return
        if not session.report:
            session.runtime_v2_fallback = _RUNTIME_V2_FALLBACK_ENABLED
            yield "\n\nRuntime V2 fallback: structured plan completed without a report.\n", []
            return

        session.runtime_v2_completed = True
        yield "\n\n**Runtime V2 structured plan completed.**\n", []
    except Exception as exc:
        session.runtime_v2_fallback = _RUNTIME_V2_FALLBACK_ENABLED
        session.logger.warning("runtime_v2_stream_failed error=%s", exc)
        _set_runtime_run_status(session, "failed", error=str(exc))
        yield "\n\n**Runtime V2 failed.**\n", []


# ── Public async stream ───────────────────────────────────────────────────────
async def run_analysis_stream(
    instruction: str,
    source_path: str,
    workspace_dir: str,
    api_key: str,
    model_id: str,
    api_base: str,
    session_id: str = "",
    cancel_event: Optional[asyncio.Event] = None,
    pause_event: Optional[asyncio.Event] = None,
    restore_state: Optional[Dict] = None,
    semantic_context: Optional[Dict] = None,
    task_question: Optional[str] = None,
    force_new_run: bool = False,
    parent_run_id: Optional[str] = None,
    retry_of_step_id: Optional[str] = None,
) -> AsyncGenerator[tuple[str, list], None]:
    """Async generator yielding (text_chunk, new_artifacts) as the agent runs."""
    # Create trace context for this session
    trace_context = create_trace_context(session_id, instruction)

    session = _Session(
        workspace_dir,
        source_path,
        session_id=session_id,
        user_question=task_question or instruction,
        restore_state=restore_state,
        semantic_context=semantic_context,
        force_new_run=force_new_run,
        parent_run_id=parent_run_id,
        retry_of_step_id=retry_of_step_id,
    )
    session.trace_context = trace_context  # Attach trace context to session
    session.structured_logger.set_trace_context(trace_context)  # Connect logger to trace context
    if cancel_event is not None:
        session.cancel_event = cancel_event
    if pause_event is not None:
        session.pause_event = pause_event
    runtime = getattr(session, "analysis_runtime", None)
    if restore_state and runtime is not None and runtime.run.status == "paused":
        runtime.resume_plan()
    tools = _make_tools(session)

    # Build effective prompt: base sections + skills index + memory snapshot
    memory_snapshot = ""
    if hasattr(session, '_memory_store') and session._memory_store:
        memory_snapshot = session._memory_store.format_for_system_prompt()
    effective_prompt = _prompt_builder.build_with_context(
        skills_index=_skills_loader.build_skills_prompt(),
        memory_snapshot=memory_snapshot,
        semantic_context=format_semantic_context(semantic_context),
    )

    # Build custom httpx clients when extra headers are needed (e.g. internal
    # API gateways that require ucid / Auth-Token).  We inject at the httpx
    # transport layer so the headers are always present on every request, and
    # we avoid the ``default_headers`` parameter which, in newer versions of
    # langchain_openai (≥1.3), leaks into the OpenAI SDK call kwargs and
    # causes ``AsyncCompletions.create() got an unexpected keyword argument
    # 'headers'``.
    _llm_client_kwargs: dict = {}
    if _LLM_EXTRA_HEADERS:
        import httpx as _httpx
        _timeout = _httpx.Timeout(_LLM_REQUEST_TIMEOUT)
        _llm_client_kwargs["http_client"] = _httpx.Client(
            headers=dict(_LLM_EXTRA_HEADERS), timeout=_timeout,
        )
        _llm_client_kwargs["http_async_client"] = _httpx.AsyncClient(
            headers=dict(_LLM_EXTRA_HEADERS), timeout=_timeout,
        )

    _llm_extra_body = dict(_LLM_EXTRA_BODY)
    if _LLM_ENABLE_THINKING is not None:
        template_kwargs = dict(_llm_extra_body.get("chat_template_kwargs") or {})
        template_kwargs["enable_thinking"] = _LLM_ENABLE_THINKING
        _llm_extra_body["chat_template_kwargs"] = template_kwargs
    if _llm_extra_body:
        _llm_client_kwargs["extra_body"] = _llm_extra_body

    llm = ChatOpenAI(
        model=model_id,
        api_key=api_key,
        base_url=api_base,
        temperature=0,
        streaming=True,
        max_retries=_LLM_MAX_RETRIES,
        request_timeout=_LLM_REQUEST_TIMEOUT,
        **_llm_client_kwargs,
    )
    agent = create_react_agent(llm, tools, prompt=effective_prompt)

    # Build the initial message — resume-aware
    if restore_state:
        # Provide context about prior progress so the agent continues intelligently
        prior_findings = restore_state.get("findings", [])
        findings_summary = ""
        if prior_findings:
            findings_summary = "\n\nPrior findings recorded:\n" + "\n".join(
                f"- {f.get('finding_id', '?')}: {f.get('statement', '')}"
                for f in prior_findings[:10]
            )
        pending_plan = []
        if runtime is not None:
            pending_plan = [
                step for step in runtime.run.steps if step.status == "pending"
            ]
        plan_summary = ""
        if pending_plan:
            plan_summary = "\nConfirmed remaining plan:\n" + "\n".join(
                f"{index}. [{step.method}] {step.objective}"
                for index, step in enumerate(pending_plan, start=1)
            )
        user_msg = HumanMessage(content=(
            f"[RESUME] This is a resumed analysis session.\n"
            f"Task: {instruction}\n"
            f"Data file: {source_path}\n"
            f"Previous stage: {restore_state.get('current_stage', 'unknown')}\n"
            f"Steps completed: {restore_state.get('total_steps', 0)}\n"
            f"Current consecutive errors: {restore_state.get('consecutive_python_errors', 0)}\n"
            f"{findings_summary}{plan_summary}\n\n"
            "Please start by calling load_data to re-load the dataset, "
            "then continue the analysis from where it left off. Follow the confirmed "
            "remaining plan in order unless a step is impossible; disclose any deviation."
        ))
    else:
        user_msg = HumanMessage(content=(
            f"Task: {instruction}\n"
            f"Data file: {source_path}\n"
            "Please start by calling load_data to understand the dataset."
        ))

    config = {"recursion_limit": 60}
    step = restore_state.get("total_steps", 0) if restore_state else 0
    error_occurred = False
    start_time = time.monotonic()
    log = session.logger
    log.info(
        "agent_start session=%s file=%s resumed=%s prior_steps=%d",
        session_id, source_path, bool(restore_state), step,
    )
    active_runtime_steps: Dict[str, tuple[str, str]] = {}
    if _RUNTIME_V2_ENABLED:
        async for chunk, artifacts in _run_runtime_v2_graph_stream(
            session,
            tools=tools,
            source_path=source_path,
            react_agent=agent,
        ):
            yield chunk, artifacts
        if getattr(session, "runtime_v2_completed", False):
            if session.report:
                report_artifacts = list(session.new_artifacts)
                session.new_artifacts.clear()
                try:
                    report_path = session.workspace_dir / "final_report.md"
                    report_path.write_text(session.report, encoding="utf-8")
                    rel = report_path.relative_to(session.workspace_dir.parent)
                    report_artifacts.append({
                        "name": report_path.name,
                        "path": str(report_path),
                        "relative_path": str(rel),
                        "url": f"/workspace/files/{rel}",
                    })
                except Exception as exc:
                    session.logger.warning(
                        "runtime_v2_report_artifact_failed error=%s", exc
                    )
                yield session.report, report_artifacts
            _set_runtime_run_status(session, "completed")
            trace_context.end_trace()
            trace_context.save_to_file(session.workspace_dir)
            session.structured_logger.save_run_metrics(
                final_status="completed",
                total_steps=session.total_steps,
                failure_code=None,
            )
            remove_trace_context(session_id)
            session.cleanup()
            return
        if not getattr(session, "runtime_v2_fallback", False):
            log = session.logger
            log.info("runtime_v2_stopped_without_fallback session=%s", session_id)
            yield "\n\n**Runtime V2 stopped without legacy fallback.**\n", []
            remove_trace_context(session_id)
            session.cleanup()
            return
        log = session.logger
        log.info("runtime_v2_fallback session=%s", session_id)
    try:
        async for event in agent.astream_events({"messages": [user_msg]}, config=config, version="v2"):
            if session.cancel_event.is_set():
                session.fail_stage(
                    session.current_stage,
                    "cancelled",
                    "analysis cancelled",
                    retryable=True,
                    hint="Restart the analysis when ready.",
                    recovery_action="retry_same_scope",
                )
                log.info("agent_cancelled session=%s after_step=%d", session_id, step)
                _set_runtime_run_status(session, "cancelled", error="analysis cancelled")
                yield "\n\n**Analysis cancelled.**\n", []
                return

            if session.pause_event.is_set() and not active_runtime_steps:
                try:
                    from langgraph_langchain.session_persistence import get_session_persistence
                    get_session_persistence().save(session)
                except Exception:
                    log.debug("session_state_save_failed_on_pause step=%d", step)
                if runtime is not None:
                    runtime.pause_plan(
                        getattr(session.pause_event, "reason", "Paused by user"),
                        require_confirmation=bool(
                            getattr(session.pause_event, "require_confirmation", False)
                        ),
                    )
                log.info("agent_paused session=%s after_step=%d", session_id, step)
                yield "\n\n**Analysis paused.** Confirm or revise the plan before continuing.\n", []
                return

            kind = event["event"]
            name = event.get("name", "")

            if kind == "on_chat_model_stream":
                chunk = event["data"]["chunk"].content
                if chunk:
                    session.process_log.append(chunk)
                    yield chunk, []

            elif kind == "on_tool_start":
                step += 1
                session.total_steps = step  # Update session step count
                if step > _MAX_AGENT_STEPS:
                    # P1: if the agent did real analysis (>= min findings), synthesize
                    # a report from recorded findings instead of failing bare. This
                    # guarantees the user gets output when the agent doesn't converge.
                    if len(session.findings) >= _SYNTH_REPORT_MIN_FINDINGS:
                        report_md, artifacts = _finalize_synthesized_report(
                            session, step, start_time, trace_context,
                            f"已达最大步数上限({_MAX_AGENT_STEPS}步)",
                            log, session_id,
                        )
                        log.warning(
                            "agent_stopped max_steps_synthesized session=%s steps=%d findings=%d",
                            session_id, step, len(session.findings),
                        )
                        yield (
                            f"\n\n**已达分析步数上限**({step}/{_MAX_AGENT_STEPS}步),"
                            f"基于已记录的 {len(session.findings)} 个发现生成最终报告。\n\n",
                            [],
                        )
                        yield report_md, artifacts
                        return
                    # Not enough findings to build a report -> fail with max_steps_exceeded
                    session.fail_stage(
                        session.current_stage,
                        "max_steps_exceeded",
                        "exceeded the maximum tool-step budget",
                        retryable=True,
                        hint="Summarize the strongest validated findings so far in fewer steps.",
                        recovery_action="retry_narrower_scope",
                    )
                    log.warning(
                        "agent_stopped max_steps_exceeded session=%s steps=%d findings=%d",
                        session_id, step, len(session.findings),
                    )
                    _set_runtime_run_status(
                        session, "failed", error="exceeded the maximum tool-step budget"
                    )
                    yield (
                        "\n\n**Analysis stopped:** exceeded the maximum tool-step budget. "
                        "Summarize the strongest validated findings so far in fewer steps.\n",
                        [],
                    )
                    return
                args = event["data"].get("input", {})
                runtime_step_id = _start_runtime_step(session, name)
                if runtime_step_id:
                    runtime_event_key = str(event.get("run_id") or f"{name}:{step}")
                    active_runtime_steps[runtime_event_key] = (name, runtime_step_id)
                log.info("tool_start step=%d tool=%s", step, name)
                if name == "python_repl":
                    code_preview = _format_preview_text(args.get("code", ""))
                    if code_preview:
                        msg = (
                            f"\n\n> **Step {step}** `python_repl`\n\n"
                            f"```python\n{code_preview}\n```\n\n"
                        )
                    else:
                        msg = f"\n\n> **Step {step}** `python_repl`\n\n"
                    session.process_log.append(msg)
                    yield msg, []
                elif name == "load_data":
                    session.start_stage("schema_understanding")
                    msg = f"\n\n> **Step {step}** `load_data({args.get('file_path', '')})`\n\n"
                    session.process_log.append(msg)
                    yield msg, []
                elif name == "eda_profile":
                    session.start_stage("data_quality_check")
                    msg = f"\n\n> **Step {step}** `eda_profile` - running auto EDA (missing values, correlations, distributions, trends)...\n\n"
                    session.process_log.append(msg)
                    yield msg, []
                elif name == "finish_report":
                    pending_markdown = args.get("markdown")
                    if isinstance(pending_markdown, str) and pending_markdown.strip():
                        session.pending_report_markdown = pending_markdown.strip()
                    session.process_log.append("\n\n---\n\n")
                    yield "\n\n---\n\n", []
                else:
                    # Tools without a legacy progress message still emit their
                    # structured running state to workbench-aware SSE clients.
                    yield "", []

            elif kind == "on_tool_end":
                elapsed = time.monotonic() - start_time
                raw_tool_output = event["data"].get("output", "") or ""
                runtime_output = _extract_event_output_text(raw_tool_output)
                runtime_event_key = str(event.get("run_id") or "")
                runtime_entry = active_runtime_steps.pop(runtime_event_key, None)
                if runtime_entry is None:
                    matching_key = next(
                        (
                            key
                            for key in reversed(active_runtime_steps)
                            if active_runtime_steps[key][0] == name
                        ),
                        None,
                    )
                    if matching_key is not None:
                        runtime_entry = active_runtime_steps.pop(matching_key)
                runtime_step_id = runtime_entry[1] if runtime_entry else session.current_runtime_step_id
                runtime_status = _tool_output_status(runtime_output)
                _bind_runtime_execution_step(session, name, runtime_step_id)
                _complete_runtime_step(
                    session,
                    runtime_step_id,
                    status=runtime_status,
                    error=runtime_output if runtime_status != "succeeded" else None,
                )
                # Emit an empty compatibility chunk so structured SSE clients can
                # observe the terminal step state even when a tool has no text output.
                yield "", []
                # Show brief output summary so user sees what happened
                if name == "load_data":
                    raw = event["data"].get("output", "") or ""
                    output_str = _extract_event_output_text(raw)
                    if not output_str.startswith("ERROR:"):
                        session.complete_stage("schema_understanding")

                if name == "eda_profile":
                    raw = event["data"].get("output", "") or ""
                    output_str = _extract_event_output_text(raw)
                    if not output_str.startswith("ERROR:"):
                        session.complete_stage("data_quality_check")
                    preview = _format_preview_text(output_str)
                    log.info("tool_end tool=eda_profile elapsed=%.1fs", elapsed)
                    if preview:
                        msg = f"\n> `eda_profile done`\n\n{preview}\n\n"
                        session.process_log.append(msg)
                        yield msg, []

                if name == "python_repl":
                    raw = event["data"].get("output", "") or ""
                    output_str = _extract_event_output_text(raw)
                    preview = _format_preview_text(output_str)
                    is_error = output_str.startswith("[ERROR]")
                    if is_error:
                        session.consecutive_python_errors += 1
                    else:
                        session.consecutive_python_errors = 0
                        session.complete_stage("deep_dive")
                        session.start_stage("conclusion_synthesis")
                        if preview:
                            session.last_progress_marker = preview[:400]
                    log.log(logging.WARNING if is_error else logging.INFO,
                            "tool_end tool=python_repl elapsed=%.1fs error=%s consecutive_errors=%d", elapsed, is_error, session.consecutive_python_errors)
                    if preview:
                        label = "ERROR" if is_error else "out"
                        msg = f"\n> `{label}`\n\n{preview}\n\n"
                        session.process_log.append(msg)
                        yield msg, []
                    if session.consecutive_python_errors >= _MAX_CONSECUTIVE_PYTHON_ERRORS:
                        session.fail_stage(
                            "deep_dive",
                            "python_execution_error",
                            "repeated python_repl errors without recovery",
                            retryable=True,
                            hint="Use smaller validated steps before retrying.",
                            recovery_action="retry_narrower_scope",
                        )
                        log.warning("agent_stopped repeated_python_errors session=%s errors=%d", session_id, session.consecutive_python_errors)
                        _set_runtime_run_status(
                            session,
                            "failed",
                            error="repeated python_repl errors without recovery",
                        )
                        yield (
                            "\n\n**Analysis stopped:** repeated python_repl errors without recovery. "
                            "Use smaller validated steps before retrying.\n",
                            [],
                        )
                        return
                    # Register any new image files saved by the code
                    image_exts = {".png", ".jpg", ".jpeg", ".svg"}
                    for p in sorted(session.workspace_dir.iterdir()):
                        if p.suffix.lower() in image_exts and p not in session.known_image_files:
                            session.known_image_files.add(p)
                            rel = p.relative_to(session.workspace_dir.parent)
                            session.new_artifacts.append({
                                "name": p.name,
                                "path": str(p),
                                "relative_path": str(rel),
                                "url": f"/workspace/files/{rel}",
                            })

                if name == "finish_report":
                    raw = event["data"].get("output", "") or ""
                    output_str = _extract_event_output_text(raw)
                    accepted = not (
                        output_str.lstrip().startswith("REPORT REJECTED")
                        or output_str.lstrip().startswith("[REPORT REJECTED]")
                        or output_str.lstrip().startswith("Report already submitted.")
                    )
                    if accepted and not session.report and session.pending_report_markdown:
                        session.report = session.pending_report_markdown.strip()
                        session.pending_report_markdown = None
                    log.info("tool_end tool=finish_report accepted=%s elapsed=%.1fs", accepted, elapsed)
                    if session.report:
                        artifacts = list(session.new_artifacts)
                        session.new_artifacts.clear()
                        # Persist the final report as a downloadable .md file so
                        # the frontend's "download results" list contains the
                        # report alongside the chart images (not PNGs only).
                        try:
                            report_path = session.workspace_dir / "final_report.md"
                            report_path.write_text(session.report, encoding="utf-8")
                            rel = report_path.relative_to(session.workspace_dir.parent)
                            report_artifact = {
                                "name": report_path.name,
                                "path": str(report_path),
                                "relative_path": str(rel),
                                "url": f"/workspace/files/{rel}",
                            }
                            # Report first, drop any stale same-name entry
                            artifacts = [report_artifact] + [
                                a for a in artifacts if a.get("name") != report_path.name
                            ]
                            log.info("report_saved session=%s path=%s", session_id, report_path)
                        except Exception:
                            log.debug("report_save_failed session=%s", session_id)
                        if artifacts:
                            log.info("artifacts_flushed count=%d", len(artifacts))
                        log.info("agent_done session=%s steps=%d elapsed=%.1fs", session_id, step, time.monotonic() - start_time)

                        # Save session metadata for API layer
                        _save_session_metadata(session, step, time.monotonic() - start_time)

                        # End trace and save
                        trace_context.end_trace()
                        trace_context.save_to_file(session.workspace_dir)

                        _set_runtime_run_status(session, "completed")
                        yield session.report, artifacts
                        return

                # Flush new artifacts
                if session.new_artifacts:
                    artifacts = list(session.new_artifacts)
                    session.new_artifacts.clear()
                    log.info("artifacts_flushed count=%d", len(artifacts))
                    yield "", artifacts

                # ── Persist session state after each tool call ──
                # Enables resume if the backend restarts or analysis is interrupted.
                try:
                    from langgraph_langchain.session_persistence import get_session_persistence
                    get_session_persistence().save(session)
                except Exception:
                    log.debug("session_state_save_failed step=%d", step)

        # ── P2: silent-termination safety net ────────────────────────────────
        # The astream_events loop exited without the agent calling finish_report
        # and without raising. This is the observed "agent stops mid-run without
        # finishing" case. If real analysis was done (>= min findings), synthesize
        # a report so the user still gets output; otherwise surface a clear message
        # and log loudly so the divergence is visible (not silently swallowed).
        if not session.report:
            log.warning(
                "agent_ended_without_report session=%s steps=%d findings=%d last_marker=%r",
                session_id, step, len(session.findings),
                (session.last_progress_marker or "")[:120],
            )
            if len(session.findings) >= _SYNTH_REPORT_MIN_FINDINGS:
                report_md, artifacts = _finalize_synthesized_report(
                    session, step, start_time, trace_context,
                    "agent 未显式调用 finish_report 即结束",
                    log, session_id,
                )
                yield (
                    f"\n\n**分析已结束但未生成报告**,基于已记录的 "
                    f"{len(session.findings)} 个发现自动整理最终报告。\n\n",
                    [],
                )
                yield report_md, artifacts
                return
            # No findings to build a report from - end trace and surface a message.
            trace_context.end_trace()
            trace_context.save_to_file(session.workspace_dir)
            _set_runtime_run_status(
                session,
                "failed",
                error="agent ended without a report or sufficient findings",
            )
            yield (
                "\n\n**分析结束但未生成报告**:agent 在未调用 finish_report 前结束,"
                "且未记录足够的发现。建议重新运行或缩小分析范围。\n",
                [],
            )

    except GraphRecursionError:
        # Recursion limit reached before the agent converged. Treat like
        # max_steps: synthesize from findings if possible, else surface a
        # max_steps-style failure so the API layer can attempt recovery.
        log.warning(
            "agent_stopped recursion_limit session=%s steps=%d findings=%d",
            session_id, step, len(session.findings),
        )
        if len(session.findings) >= _SYNTH_REPORT_MIN_FINDINGS:
            report_md, artifacts = _finalize_synthesized_report(
                session, step, start_time, trace_context,
                "已达递归上限(recursion_limit)",
                log, session_id,
            )
            yield (
                f"\n\n**已达递归上限**,基于已记录的 {len(session.findings)} 个发现生成最终报告。\n\n",
                [],
            )
            yield report_md, artifacts
            return
        session.fail_stage(
            session.current_stage,
            "max_steps_exceeded",
            "recursion limit reached before finish_report",
            retryable=True,
            hint="Narrow the analysis scope and finish in fewer steps.",
            recovery_action="retry_narrower_scope",
        )
        trace_context.end_trace()
        trace_context.save_to_file(session.workspace_dir)
        _set_runtime_run_status(
            session, "failed", error="recursion limit reached before finish_report"
        )
        yield (
            "\n\n**Analysis stopped:** exceeded the maximum tool-step budget. "
            "Narrow the analysis scope and finish in fewer steps.\n",
            [],
        )

    except asyncio.CancelledError:
        _set_runtime_run_status(session, "cancelled", error="analysis stream cancelled")
        raise

    except Exception as exc:
        error_occurred = True

        # Classify the API error for smart recovery
        from langgraph_langchain.error_classifier import classify_api_error
        classified = classify_api_error(exc)

        log.error(
            "agent_error session=%s steps=%d error_type=%s reason=%s error=%s",
            session_id, step, type(exc).__name__, classified.reason.value, exc,
            exc_info=True,
        )

        if classified.is_retryable and classified.reason.value in ("rate_limit", "overloaded", "server_error", "timeout"):
            # For transient API errors, provide a user-friendly retry hint
            from langgraph_langchain.retry_utils import jittered_backoff
            delay = jittered_backoff(1, base_delay=3.0, max_delay=30.0)
            log.info("api_retry_recommended session=%s reason=%s delay=%.1fs", session_id, classified.reason.value, delay)
            yield (
                f"\n\n**API 临时错误** ({classified.reason.value})，"
                f"建议等待至少 {delay:.0f} 秒后重试。\n",
                [],
            )

        # End trace on error
        trace_context.end_trace()
        trace_context.save_to_file(session.workspace_dir)
        # Save run metrics
        session.structured_logger.save_run_metrics(
            final_status="failed",
            total_steps=step,
            failure_code=classified.reason.value,
        )
        _set_runtime_run_status(session, "failed", error=str(exc))

        # User-facing error with classified context
        if classified.reason.value in ("auth", "billing", "missing_api_key"):
            err_msg = f"\n\n**API 认证错误**: 请检查 DEEPSEEK_API_KEY 配置。\n"
        elif classified.reason.value in ("rate_limit", "overloaded"):
            err_msg = f"\n\n**API 限流/过载**: 服务暂时不可用，请稍后重试。\n"
        elif classified.reason.value == "context_overflow":
            err_msg = "\n\n**分析内容过长**: 分析步骤过多，请缩小分析范围后重试。\n"
        else:
            err_msg = f"\n\n**Agent 错误** ({classified.reason.value}): {exc}\n"
        yield err_msg, []
        # Still emit report if one was already submitted before the error
        if session.report:
            yield session.report, []
    finally:
        # Save run metrics on normal completion
        if not error_occurred and session.report:
            _set_runtime_run_status(session, "completed")
            session.structured_logger.save_run_metrics(
                final_status="completed",
                total_steps=step,
                failure_code=None
            )
        # Clean up trace context
        remove_trace_context(session_id)
        # Release heavy resources (DataFrames, large namespace objects)
        session.cleanup()
