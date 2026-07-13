# LangGraph analysis guardrails and E2E tuning

本轮 `langgraph_langchain` 后端已完成一批面向“分析师式数据分析”能力的核心改动，并已把 3 类代表性数据集的端到端通过率提升到 3/3。

## 为什么做
用户明确把重点从前端样式转到后端分析能力，要求验证真实 E2E 效果，并在必要时把“小步推进”从 prompt 约束升级为运行时约束。

## 本轮已落地的关键后端改动

### 1. `langgraph_langchain/langgraph_agent.py`
- 强化 `_SYSTEM_PROMPT`：要求先做分析计划，再按数据质量、指标定义、分组对比、趋势、异常解释、驱动分析、最终综合的框架推进。
- 扩充 `eda_profile()`：除基础 EDA 外，增加 `Analysis Signals` 与更贴近业务分析的提示。
- 在 `_Session.ns` 预置高价值 helper：如分组画像、分群对比、时间趋势、异常检测、变化解释等。
- 强化 `finish_report()`：要求结构化章节、证据化结论、图表说明、中文专业表达与不确定性标注。
- 新增 `python_repl` 运行时小步校验：要求步骤标记、限制单步体量、拒绝空步骤。
- 步骤标记已支持中英双语：`step objective/method/key results/suggested next step` 与 `步骤目标/方法/关键结果/建议下一步`。
- 单步上限已从 40 行放宽到 50 行，并在 prompt 中明确“最终综合”必须拆成更小步骤，避免结尾大脚本卡死。

### 2. `langgraph_langchain/test_reliability.py`
- 已补齐运行时 guardrail 测试：缺少 marker 会拒绝、超长步骤会拒绝、结构化小步会通过、中文 marker 会通过。
- 当前可靠性测试状态：`45 passed`。

## 本轮 E2E 验证结论
使用真实模型配置（DeepSeek OpenAI-compatible）对 3 类数据集做过端到端验证：
- `grouped_sales`：最终通过，生成报告
- `time_series_anomaly`：最终通过，生成报告
- `quality_issues`：最终通过，生成报告

结果文件：
- `/tmp/e2e_langgraph_checks/results_after_bilingual_guard.json`
- `/tmp/e2e_langgraph_checks/results_after_line_limit_tuning.json`

## 关键排障结论
- 首个主要根因不是分析逻辑，而是运行时校验与模型输出语言不匹配：模型常输出中文步骤标记，导致英文-only validator 误判失败。
- 修复双语 marker 后，主要剩余问题变成结尾综合步骤过长。
- 将单步上限从 40 提到 50，并在 prompt 中强制最终综合拆小步后，E2E 从 1/3 提升到 3/3。

## 下次最值得继续的点
- `quality_issues` 的运行日志里出现过重复 `finish_report` 调用；虽然最终没有阻塞，但下一步最值得加的是 `finish_report` 一次性保护，防止报告提交成功后再次调用。
