# Smoke Check Follow-up (2026-04-01)

## Scope
针对 LangGraph 后端做了一次真实数据 API smoke check，重点验证：

- `/health`
- `/workspace/upload`
- `/v1/chat/completions`
- agent 运行过程日志
- artifact/report 产出
- 解释性分析与最终收敛行为

## Smoke Input
- 上传文件：`temp_uploads/test.xlsx`
- session：`f03d739d-4a87-44a1-aaf7-c6803c195b7d`
- 请求要求：先检查数据质量，再总结关键指标、主要分组差异、时间趋势与需要进一步验证的风险；结论必须保留证据边界，不把相关性直接写成因果。

## What worked
1. 后端成功启动并通过 `/health`。
2. 文件上传成功。
3. `/v1/chat/completions` 成功返回分析结果。
4. workspace 下生成了图表和 `data_analysis_report.md`。
5. agent 实际运行了：
   - `load_data`
   - `eda_profile`
   - 多次 `python_repl`
   - `finish_report`
6. 本轮 recommendation evidence gating 已在真实流程中生效，没有明显把弱证据直接包装成强 action recommendation。

## Bugs found during smoke
### 1. `finish_report` could be accepted twice in one run
日志中出现：
- step 11 `finish_report` accepted=True
- step 12 `finish_report` accepted=True

这说明运行时在首次成功提交报告后，没有立即终止后续事件消费，导致同一轮分析里出现第二次成功的 `finish_report` 记录。

## Fix applied
已在 `langgraph_langchain/langgraph_agent.py` 修复：

1. `run_analysis_stream` 在收到首个成功 `finish_report` 后立即：
   - flush artifacts
   - yield 最终 report
   - `return`
2. 对流式事件补充 `pending_report_markdown` 兜底，确保在 mocked / delayed tool-end 场景下仍能正确识别首个成功报告。
3. 将 `Report already submitted. Do not call finish_report again.` 视为非 accepted，避免日志再次记成 accepted=True。

## Regression coverage added
`langgraph_langchain/test_reliability.py` 新增了两类回归保护：

1. `finish_report` 工具层：
   - 首次提交成功
   - 第二次提交返回 `Report already submitted...`
2. `run_analysis_stream` 流层：
   - 一旦首个 `finish_report` 成功，流立即结束
   - 不再继续消费后续重复 `finish_report` 事件

## Current reliability status
已重新跑：
- `pytest /python/pragrams/data_analysis_agent/langgraph_langchain/test_reliability.py -q`

结果：
- `81 passed`

## Remaining analysis-quality issues exposed by smoke
这次 smoke 仍暴露出“像分析不等于真分析”的剩余短板：

1. toy / small dataset 上仍可能把 `id` 一类字段当作可分析数值信号。
2. 小样本波动仍容易被包装成看起来较完整的业务结论。
3. 某些文本表述虽然加入了 evidence boundary，但仍偏“分析口吻完整”，不够强调样本脆弱性与定义风险。

## Next recommended backend work
继续按 `.claude/project_critical_evaluation.md` 的方向推进：

1. 收紧 id-like numeric column 的业务指标候选筛选。
2. 在 small-sample / thin-support 场景下进一步强制降级分析语气。
3. 强化最终报告对 sample size、coverage、definition risk 的显式引用要求。
4. 继续做真实数据 smoke，而不只依赖 deterministic tests。
