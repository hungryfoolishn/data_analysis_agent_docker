# Agent 不显式调用 finish_report 的根因排查与解决方案

> 本方案经**实运行验证**(在容器内 monkey-patch `create_react_agent`,捕获 `on_chat_model_end` 的 `finish_reason`/`tool_calls`/`usage` 与 `pre_model_hook` 的 messages token 数,跑缺陷数据实测)。实测结果**颠覆了初版方案的假设**,本文为修正版。

> 现状:兜底机制(`_build_fallback_report`)**已回撤**。agent 不调 `finish_report` 时无报告,必须从源头解决。

## 一、实测发现(颠覆初版假设)

在容器内跑缺陷问题明细表.xlsx(100 行 × 13 列),monkey-patch 捕获每次 LLM 调用的 `finish_reason`、`tool_calls`、`usage`(token 数),以及 `pre_model_hook` 里 state messages 的字符数:

| 指标 | 实测值 | 初版假设 | 结论 |
|---|---|---|---|
| `finish_reason`(每次) | **`tool_calls`**(20/20 次) | "LLM 停止生成 tool call" | ❌ 假设错误,LLM 一直在生成 tool call |
| input_tokens 峰值 | **16969**(17K) | "接近 64K 上下文满" | ❌ 假设错误,上下文远没满 |
| `tool_calls` 内容 | 全是 `python_repl`(分析新维度) | "调不动 finish_report" | ❌ LLM 没试 finish_report,而是一直 python_repl |
| agent 结束 | step 24 后 LLM 输出 python_repl tool_call,但 agent 没执行 tool,直接 cleanup(无 max_steps、无 GraphRecursionError、无 finish_report) | "LLM 停止 → 自然结束" | ❌ 结束原因待确认(见 2.4) |

**核心结论**:agent 不调 `finish_report` 的根因**不是**上下文累积、**不是** LLM 停止生成 tool call,而是 **agent 不收敛** -- LLM 一直生成 `python_repl` tool call(核心指标 → 部门 → 应用 → 时间 → 关联 → 交叉 → 无效缺陷 → 可视化 → ...),每次都"建议下一步:记录关键发现/继续分析",但**从不发出 `finish_report` tool call**。

## 二、根因分析(基于实测)

### 2.1 直接原因:agent 不收敛,LLM 一直 python_repl

实测 `tool_calls` 序列(verify_out2):
- step 1-2:load_data, eda_profile
- step 3-24:**全是 python_repl**(核心指标、部门、应用、时间趋势、关联分析、交叉分析、无效缺陷、可视化...)
- 每次 python_repl 的"建议下一步"都是"继续分析"或"记录关键发现",但 LLM 从未发出 `finish_report`

LLM 行为:分析完一个维度,继续下一个维度,不收敛到报告生成。`finish_reason=tool_calls`(不是 `stop`),即 LLM 没有"决定停止",而是一直要调 tool。

### 2.2 排除:上下文累积(初版假设,实测证伪)

- input_tokens 峰值 16969(17K),远低于 DeepSeek `deepseek-chat` 的 64K 上下文
- 字符数峰值 16197(16K 字符)
- **上下文没满**,`trim_messages`(初版 P0.2)不是必要方案

### 2.3 排除:LLM 停止生成 tool call(初版假设,实测证伪)

- `finish_reason=tool_calls`(20/20 次),LLM 一直在生成 tool call
- 初版"LLM 上下文满后停止生成 tool call"的假设错误
- 提示"请立即 finish_report"(初版 P2)对这种场景无效 -- 不是"调不动",是"没想调"

### 2.4 agent 结束原因(待确认)

实测:step 24 的 python_repl 完成后,LLM 输出 step 25 的 `tool_calls`(python_repl 可视化,output 1354 tokens),但 **agent 没执行 step 25 的 tool,直接 `session_cleanup` 结束**。无 `max_steps`(24<48)、无 `GraphRecursionError` 日志、无 `finish_report`。

排查:
- `recursion_limit=60`:langgraph 1.2.8 源码确认 `_apregel_stream_v3` 在 `out_of_steps` 时 `raise GraphRecursionError`(非静默)。实测无 `agent_error` 日志 → recursion_limit **没触发**。
- astream_events 在 LLM 输出 tool_calls 后没 yield `on_tool_start`,graph 似乎到 END -- **原因待确认**(可能是 langgraph 1.2.8 + DeepSeek 的 tool_calls 流式解析边界,或 astream_events 在某条件提前结束)。

**这个结束原因不是核心**(即使 agent 不结束,它也不会 finish_report,而是一直 python_repl)。核心是 **2.1 agent 不收敛**。

### 2.5 加剧因素

- **prompt 没有强制收敛约束**:`workflow.md` 只说"分析完成调 finish_report",但没定义"什么叫分析完成"。LLM 一直发现新分析维度,认为"还没完成"。
- **python_repl 太自由**:每次 python_repl 都能分析新维度,LLM 有无限多的分析方向(部门、应用、时间、级别、价值、交叉、无效、可视化...),不收敛。
- **`record_finding` 没触发 finish**:即使 record 了 finding,LLM 也没转向 finish_report(实测 session_1783662749 record 8 次仍没 finish)。

## 三、解决方案(基于实测,从源头)

初版 P0.2(trim_messages)**降级为长期优化**(上下文没满,不急需)。核心是**让 agent 收敛到 finish_report**。

### P0:prompt 强制收敛(治本)

**P0.1 定义"分析完成"的明确条件**(workflow.md / analysis_rules.md):
- 明确"分析 N 个维度后必须 finish_report"。例如:"完成 load_data + eda_profile + 3-5 个 python_repl 维度分析 + record_finding 记录 3-5 个发现后,必须立即调用 finish_report,不要再开新维度"
- 列出"必做维度"(如:数据概览、核心指标、分组对比、趋势)+ "可选维度"(可视化、交叉),完成必做即 finish
- 明确"不要分析每个可能的维度,聚焦回答用户问题"

**P0.2 在 python_repl 的返回里引导收敛**(非强制提示,而是状态反馈):
- 当 `record_finding` 已记录 ≥3 个 finding,python_repl 返回追加:"已记录 N 个发现,建议整理并 finish_report"
- 当 python_repl 步数 ≥阈值,返回:"已分析 N 个维度,建议 finish_report"
- 注意:上一轮加过类似提示(step>=30),但**阈值太晚**(agent 在 24 步就结束),且**措辞是"立即 finish"而非"已分析够了"**。改进:更早(如 record 3 个 finding 后)、更温和("已足够,建议整理报告")。

### P1:max_steps 强制 finish(兜底,治标)

当前 `step > _MAX_AGENT_STEPS`(48)时 `fail_stage("max_steps_exceeded")` 并 return(没报告)。改为:用已记录 findings + process_log 生成报告,走 `finish_report` 保存逻辑(写 final_report.md)。

这样即使 agent 不收敛(跑到 max_steps),也有报告。

### P2:确认 agent 结束原因(2.4)

实测 agent 在 step 24 LLM 输出 tool_calls 后没执行 tool 就结束,原因待确认:
- 跑一个会触发 recursion_limit 的(小 limit,如 10),确认 `GraphRecursionError` 是否被 `except` 捕获并 log
- 检查 astream_events 在 LLM 输出 tool_calls 后是否 yield on_tool_start(确认 graph 是否执行 tool)
- 若是 langgraph 1.2.8 + DeepSeek 的流式 tool_calls 解析问题,考虑改用 `agent.ainvoke` 或 `astream(stream_mode="values")` 替代 `astream_events`

### P3:trim_messages(长期优化,降级)

上下文没满(17K),trim_messages 不是当前必要方案。但作为长期优化(更大数据集、更多步数时预防),仍可实施 `pre_model_hook` + `trim_messages`(见初版 P0.2 代码)。

## 四、实施计划

| 阶段 | 任务 | 依据 | 优先级 |
|---|---|---|---|
| 1 | P0.1 prompt 强制收敛(定义"分析完成"条件) | 实测:agent 不收敛 | **高**(治本) |
| 2 | P1 max_steps 强制 finish(用 findings 生成报告) | 兜底,保证有报告 | **高** |
| 3 | P0.2 python_repl 返回引导(更早、更温和) | 辅助收敛 | 中 |
| 4 | P2 确认 agent 结束原因 | 实测异常现象 | 中 |
| 5 | P3 trim_messages(长期) | 预防大数据集 | 低 |

**建议顺序**:1(prompt 收敛)+ 2(max_steps finish)先做,验证 agent 是否调 finish_report;若仍不收敛,加 3;4 是排查框架行为;5 长期。

## 五、验证方法

1. **正常 case 不回归**:简单数据(5 行 sales)仍 `finish_report` 20 步左右 completed
2. **缺陷数据 case**(原问题场景):跑缺陷问题明细表.xlsx,确认:
   - agent 主动调 `finish_report`(不再一直 python_repl)
   - `final_status=completed`,`finish_report accepted=True`
   - step 数 < 30(收敛,不跑到 max_steps)
3. **max_steps 兜底**:若 agent 仍不收敛跑到 48 步,确认有报告(findings 生成)
4. **监控**:跑时捕获 `finish_reason` + `tool_calls`,确认 LLM 发出 `finish_report` tool call

## 六、初版假设被证伪的教训

初版方案(基于日志推断)假设"上下文累积 → LLM 停止生成 tool call",但**没实测**。实运行验证发现:
- 上下文没满(17K << 64K)
- LLM 没停止(finish_reason=tool_calls)
- 真正原因是 agent 不收敛(LLM 一直 python_repl)

**教训**:根因分析必须实运行验证(捕获 finish_reason/token/tool_calls),不能只从日志推断。初版方案基于推断设计了 trim_messages(治上下文),但实际问题是收敛(治行为),方向错了。

## 七、关联

- 回撤的兜底:`_build_fallback_report` + for 外 `if not session.report`(已移除)
- `astream_events` 循环 + `recursion_limit=60`:[langgraph_agent.py `run_analysis_stream`](../langgraph_langchain/langgraph_agent.py)
- `_MAX_AGENT_STEPS=48`:[config.py](../langgraph_langchain/config.py)
- prompt:[workflow.md](../langgraph_langchain/prompts/sections/workflow.md)、[python_repl_rules.md](../langgraph_langchain/prompts/sections/python_repl_rules.md)
- 实测脚本:`/tmp/verify_fb.py`(monkey-patch create_react_agent,捕获 finish_reason/tool_calls/usage)

## 八、框架版本(实测)

- langgraph 1.2.8:`create_react_agent` 有 `pre_model_hook`(用于 message trimming),无 `messages_modifier`;`recursion_limit` 达到时 `raise GraphRecursionError`(`_apregel_stream_v3` 的 `out_of_steps` 分支)
- langchain 1.3.12:`trim_messages` 可用
- DeepSeek `deepseek-chat`:上下文 64K token;实测 24 步 input_tokens 峰值 17K(远未满)
