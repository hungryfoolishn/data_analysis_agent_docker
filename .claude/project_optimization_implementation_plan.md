# 项目优化实施计划（按模块 / 文件层拆解）

## 文档目的

基于：
- `.claude/project_critical_evaluation.md`
- `.claude/project_optimization_roadmap.md`

把优化路线进一步落到当前代码结构上，回答三个问题：

1. 该优化什么模块
2. 大致改哪些文件
3. 每一层优化如何分批推进

当前推荐后端主实现位于：
- `langgraph_langchain/api_server_langgraph.py`
- `langgraph_langchain/langgraph_agent.py`
- `langgraph_langchain/schemas.py`
- `langgraph_langchain/test_reliability.py`

其中：
- `api_server_langgraph.py` 主要负责 API、session、workspace、流式响应、生命周期管理
- `langgraph_agent.py` 主要负责 agent prompt、工具、运行时 guardrails、报告质量门控、分析执行链路
- `schemas.py` 当前承载结构化数据模型，但还比较薄
- `test_reliability.py` 是现有后端可靠性测试主阵地

---

## 一、现有模块职责判断

## 1. `langgraph_langchain/langgraph_agent.py`

这是当前后端最关键的核心文件。

从代码结构看，这个文件同时承担了：
- agent system prompt
- python_repl guardrails
- finish_report 质量门控
- session 级执行上下文
- 内置分析 helper
- LangGraph tools 定义
- 主分析流 `run_analysis_stream`

也就是说，它现在是：

**“分析方法 + 运行控制 + 工具层 + 输出质量控制” 的超级核心文件。**

这一层将直接决定：
- 分析可信度
- agent 收敛性
- 结论质量
- 产物生成方式

所以 Phase 1 和 Phase 2 的改动，绝大部分都会先落在这个文件或从这个文件拆分出去。

---

## 2. `langgraph_langchain/api_server_langgraph.py`

这个文件当前承担：
- FastAPI 接口层
- session 持久化与 TTL 清理
- 文件上传/下载
- workspace 生命周期
- 并发控制
- 调用 `run_analysis_stream`
- SSE 输出封装

它更偏：

**“服务编排 + 资源生命周期 + API 适配层”**

Phase 3 的很多优化会落在这里，例如：
- trace id
- 错误分类
- session/run 生命周期治理
- artifact lineage 暴露
- 更明确的 API 层错误结构

---

## 3. `langgraph_langchain/schemas.py`

当前只有：
- `PlanStep`
- `Plan`
- `CodeGen`
- `StepExecutionSummary`
- `FinalReport`

这说明：

**当前结构化 schema 还没有承载“可信度治理”所需的数据模型。**

未来如果要落地：
- finding
- evidence
- assumption
- metric definition
- risk / confidence
- artifact lineage

优先应该把这部分结构化类型沉淀到这里，避免继续把所有语义都塞进自由 markdown。

---

## 4. `langgraph_langchain/test_reliability.py`

当前测试重点已经覆盖：
- session 持久化
- 并发控制
- finish_report 质量门控
- 日志文件生成
- 取消机制

这很有价值，但仍偏：

**“工程可靠性测试”**

后面还需要补：
- 分析可信度回归测试
- 证据绑定测试
- 口径声明测试
- 失败分类与恢复策略测试
- benchmark 数据集回归测试

因此这个文件短期内仍可扩展，但中期更适合按主题拆分测试文件。

---

## 二、实施分层与模块落点

# Phase 1：先做分析可信度底座

## 目标
先解决“报告很像分析，但不够可信”的问题。

## 优先模块
- 最高优先：`langgraph_langchain/langgraph_agent.py`
- 同步补充：`langgraph_langchain/schemas.py`
- 验证回归：`langgraph_langchain/test_reliability.py`

---

## Phase 1.1 结论-证据绑定

### 要做什么
把最终输出从“自由报告”升级成：
- 结构化 findings
- findings 驱动最终 report

### 主要改动文件

#### 1. `langgraph_langchain/schemas.py`
建议新增结构，例如：
- `EvidenceItem`
- `Finding`
- `MetricDefinition`
- `AnalysisAssumption`
- `ReportQualityGateResult`

建议至少表达：
- statement
- evidence_text
- source_fields
- source_artifacts
- filters / time_window
- confidence_level
- evidence_level
- hypothesis_flag

#### 2. `langgraph_langchain/langgraph_agent.py`
重点位置：
- `_SYSTEM_PROMPT`：要求先形成 findings，再形成最终报告
- `finish_report` 所在逻辑：增加对“是否存在无证据结论”的校验
- `_contains_evidence_marker` / `_contains_quantitative_evidence` / `_has_time_window_reference` / `_has_group_reference`
  这一组逻辑可从“文本启发式检查”升级为“结构化 findings 校验 + 文本兜底校验”

### 建议改造方向
当前 `finish_report` 更像在做 markdown 质量门控。
下一步要把它升级成：

**报告质量门控 + 可信度门控**

即不仅检查：
- 报告够不够长
- 是否有标题
- 是否有数字

还要检查：
- 核心结论是否有证据
- 证据是否带时间窗口 / 分组 / 样本信息
- 是否把假设说成事实

### 配套测试
在 `langgraph_langchain/test_reliability.py` 中增加：
- 无 evidence 的 finding 被拒绝
- 没有时间窗口的趋势结论被拒绝
- 没有分组信息的对比结论被拒绝
- 明显因果化表达但无证据时被拒绝

---

## Phase 1.2 业务口径显式声明

### 要做什么
把业务口径从隐式推理中抽出来，强制显式表达。

### 主要改动文件

#### 1. `langgraph_langchain/schemas.py`
建议新增：
- `MetricScope`
- `DefinitionRisk`
- `AnalysisContext`

至少表达：
- metric_name
- definition_text
- numerator / denominator
- dedup_rule
- time_window
- missing_value_policy
- semantic_uncertainty

#### 2. `langgraph_langchain/langgraph_agent.py`
重点位置：
- `_SYSTEM_PROMPT` 中“analysis plan”要求进一步强化：
  - 明确指标定义
  - 明确时间窗口
  - 明确去重口径
  - 明确字段语义假设
- `eda_profile` 输出中已经有基础数据质量和字段信息，可继续增强为“分析前口径声明输入”
- `finish_report` 增加“口径声明块缺失则拒绝或降级”的门控

### 建议改造方向
不是让模型自己默默理解 metric，而是要求输出：
- 我按什么口径算
- 这个口径哪里可能不稳
- 哪些字段语义我不能完全确认

### 配套测试
新增回归测试：
- 指标分析报告中缺少 time window 时拒绝
- 有 ratio / share / conversion 等结论但无分母定义时拒绝
- 字段语义明显不确定但报告未声明假设时拒绝

---

## Phase 1.3 schema 理解前置化

### 要做什么
让真正的深度分析之前，先经过一层数据理解。

### 主要改动文件

#### 1. `langgraph_langchain/langgraph_agent.py`
重点位置：
- `load_data`
- `eda_profile`
- `_SYSTEM_PROMPT`
- `_Session` 中可新增结构化分析上下文缓存

### 建议改造方向
当前 `load_data` 返回：
- shape
- dtypes
- nulls
- preview

下一步建议再补：
- 主键候选
- 时间字段候选
- 金额 / 度量字段候选
- 类别字段候选
- 可能的字段风险提示
- 高基数字段识别
- ID-like 字段识别

也可以考虑从 `eda_profile` 中拆出一个更前置的：
- `schema_profile` / `data_contract_check`

先让 agent 形成“我理解的数据结构是什么”，再进入深挖。

### 配套测试
新增测试：
- 对 id-like numeric 字段不应当作业务 metric 主结论
- 对高基数字段不应直接当作主要分组维度
- 对自动识别出的时间列，趋势分析必须引用时间窗口

---

# Phase 2：再做 Agent 收敛稳定性

## 目标
让系统不依赖偶然 prompt 平衡，而是具备更可控的执行路径。

## 优先模块
- 核心：`langgraph_langchain/langgraph_agent.py`
- 服务协同：`langgraph_langchain/api_server_langgraph.py`
- 回归验证：`langgraph_langchain/test_reliability.py`

---

## Phase 2.1 状态机化分析流程

### 要做什么
把当前 agent 流程从“ReAct 自由探索”进一步收束为明确阶段。

### 主要改动文件

#### 1. `langgraph_langchain/schemas.py`
建议新增：
- `AnalysisStage`
- `StageResult`
- `RunStatus`

例如阶段：
- schema_understanding
- data_quality_check
- eda
- deep_dive
- synthesis
- final_report

#### 2. `langgraph_langchain/langgraph_agent.py`
重点位置：
- `_Session`：保存当前阶段、阶段结果、失败次数、阶段切换记录
- `_make_tools`：限制某些 tool 在某阶段之前不可调用
- `run_analysis_stream`：记录 stage transition，并在流式输出里反映阶段状态
- `_SYSTEM_PROMPT`：要求 agent 按阶段推进，而不是跳跃式总结

### 建议改造方向
当前已经有隐式阶段，但没有明确状态机。
下一步应把隐式顺序变成显式状态，以便：
- 更稳地控制 finish_report 触发时机
- 更容易发现偏航
- 更方便失败恢复

### 配套测试
新增：
- 未经过 load_data / eda_profile 不允许进入 finish_report
- deep_dive 阶段失败时可回退，但不能直接跳 final_report
- 超过最大步骤时应返回阶段化失败信息

---

## Phase 2.2 失败分类与恢复策略

### 要做什么
把“失败”从一个笼统状态拆成可恢复的类型。

### 主要改动文件

#### 1. `langgraph_langchain/api_server_langgraph.py`
重点位置：
- `_run_analysis`
- `chat_completions`
- HTTPException detail 结构

建议统一错误类型，例如：
- `missing_data_file`
- `schema_uncertain`
- `python_execution_error`
- `analysis_not_converged`
- `report_rejected`
- `session_expired`
- `cancelled`
- `timeout`

#### 2. `langgraph_langchain/langgraph_agent.py`
重点位置：
- `python_repl` error handling
- `finish_report` rejection logic
- `run_analysis_stream` 对 repeated python errors / max steps 的终止分支

### 建议改造方向
现在已经有：
- repeated python errors 停止
- max steps 停止
- report rejected

下一步应统一成为：
- failure_code
- failure_reason
- recovery_hint
- retryability

### 配套测试
新增：
- python 错误达到阈值后返回统一 failure_code
- max step exceed 返回统一 failure_code
- report rejected 时 API 层 detail 不只是文本，而是结构化错误

---

## Phase 2.3 benchmark 与稳定性回归

### 要做什么
建立固定样例集，不再只靠个别 E2E 成功样例判断质量。

### 主要改动文件
- `langgraph_langchain/test_reliability.py`
- 建议后续拆出新测试文件，例如：
  - `test_analysis_quality.py`
  - `test_state_machine.py`
  - `test_error_taxonomy.py`

### 建议改造方向
测试样本分层：
- 干净单表
- 脏数据
- 多表关系
- 时间序列
- 字段歧义
- 行业数据样例

衡量项：
- 是否收敛
- 是否生成 evidence-based findings
- 是否保留口径声明
- 是否错误降级而不是胡乱结论

---

# Phase 3：可复核与工程治理

## 目标
让结果能追踪、问题能排查、workspace 能治理。

## 优先模块
- 最高优先：`langgraph_langchain/api_server_langgraph.py`
- 配合：`langgraph_langchain/langgraph_agent.py`
- schema 支撑：`langgraph_langchain/schemas.py`

---

## Phase 3.1 trace / lineage

### 要做什么
让每个结论都能追到来源。

### 主要改动文件

#### 1. `langgraph_langchain/schemas.py`
建议新增：
- `ArtifactRef`
- `LineageNode`
- `RunTrace`
- `ConclusionTrace`

#### 2. `langgraph_langchain/langgraph_agent.py`
重点位置：
- `_Session`：记录每个 finding 的 artifact / step / output 来源
- `python_repl`：记录新产物由哪个 step 生成
- `run_analysis_stream`：把阶段、step、artifact 关联起来

#### 3. `langgraph_langchain/api_server_langgraph.py`
重点位置：
- `_serialize_session`
- workspace file list API
- chat response payload

### 建议改造方向
现在 artifacts 已经能收集，但 lineage 还不完整。
下一步建议补：
- artifact 是哪一步生成的
- 哪个 finding 用到了它
- 最终报告用了哪些 artifact

---

## Phase 3.2 结构化日志与可观测性

### 要做什么
从“有日志”提升到“能分析日志”。

### 主要改动文件

#### 1. `langgraph_langchain/langgraph_agent.py`
重点位置：
- `_make_session_logger`
- `run_analysis_stream`

当前已有日志：
- agent_start
- tool_start
- tool_end
- artifacts_flushed
- agent_done
- agent_error

下一步建议补：
- stage
- failure_code
- retry_count
- token/cost（若可取到）
- report gate rejection reason
- evidence quality score

#### 2. `langgraph_langchain/api_server_langgraph.py`
重点位置：
- request 入口
- session lifecycle
- upload / download / delete / cancel 接口

建议补：
- request_id
- run_id
- session_id 贯通
- endpoint latency
- cancel source
- stale cleanup count

### 配套测试
新增：
- 关键路径日志字段齐全性测试
- session 结束后日志文件存在且包含 run outcome

---

## Phase 3.3 workspace / session 生命周期治理

### 要做什么
让 workspace 不只是“能用”，还要“可长期维护”。

### 主要改动文件

#### 1. `langgraph_langchain/api_server_langgraph.py`
重点位置：
- `_prune_stale_sessions`
- `_load_sessions`
- `_ensure_session_consistency`
- `delete_session`
- `cancel_session`

### 建议改造方向
当前已经有 TTL 和清理，但还可以继续细化：
- 任务取消后的产物处理策略
- session 结束后的保留策略
- artifact 类型区分（输入文件 / 中间文件 / 最终报告 / 图表）
- 针对长 session 的定期清扫策略

#### 2. `langgraph_langchain/langgraph_agent.py`
重点位置：
- `python_repl` 生成文件收集逻辑
- `_Session` 已知产物管理

### 配套测试
新增：
- cancel 后 session 保留哪些产物
- stale session 清理时 lineage / manifest 是否同步清理
- workspace 中非预期大文件是否能被识别

---

# Phase 4：最后做产品化收敛

## 目标
建立更清晰的对外产品形态，但不抢前面底座工作的优先级。

## 优先模块
- 后端主逻辑仍在 `langgraph_langchain/langgraph_agent.py`
- API 暴露在 `langgraph_langchain/api_server_langgraph.py`
- 如果之后扩 UI，再联动 `webui/`

---

## Phase 4.1 行业模板化

### 主要改动文件
优先仍建议落在：
- `langgraph_langchain/langgraph_agent.py`
- 后续可考虑把行业 prompt / 模板拆到单独模块

### 建议方向
先不要做大而全，而是先试一个领域：
- 电商运营
- 销售漏斗
- 留存/转化
- 财务分析

做法：
- 行业指标模板
- 常见异常解释模板
- 常见建议动作模板

---

## Phase 4.2 输出层收敛

### 主要改动文件
- `langgraph_langchain/api_server_langgraph.py`
- 后续可能会影响 `webui/`

### 建议方向
对外暴露：
- 关键发现
- 证据
- 建议动作
- 风险提示

尽量弱化：
- session/workspace/tool 细节
- 技术执行过程噪音

---

## 三、建议的代码拆分方向

当前最大的结构风险之一，是 `langgraph_agent.py` 体量已经很大。

它现在同时承载：
- prompt
- helper
- tool
- quality gate
- execution session
- streaming run loop

短期内可以继续在这个文件上迭代，但如果进入下一轮优化，建议逐步拆分。

## 建议拆分方向

### 1. `schemas.py`
继续扩成真正的结构化语义层，承载：
- findings
- evidence
- assumptions
- metric definitions
- trace / lineage
- stage / failure taxonomy

### 2. 可考虑新拆模块
例如：
- `report_quality.py`：报告质量门控 / evidence gate
- `analysis_context.py`：schema 理解、口径声明、上下文对象
- `agent_runtime.py`：状态机、失败恢复、run loop
- `artifacts.py`：artifact manifest / lineage 管理

注意：这不是要求现在立刻重构，而是说 **Phase 1/2 做到一半时，应该开始为拆分做准备**。

---

## 四、建议的实施顺序（代码层）

## 第一批：立刻做

### 文件优先级
1. `langgraph_langchain/schemas.py`
2. `langgraph_langchain/langgraph_agent.py`
3. `langgraph_langchain/test_reliability.py`

### 目标
先把“可信度治理”的结构化基础打出来。

### 具体动作
- 在 `schemas.py` 增加 finding / evidence / assumption / metric definition 相关 schema
- 在 `langgraph_agent.py` 中把 finish_report 从“文本质量检查”升级为“可信度门控”
- 在测试里补 evidence gate / 口径声明 / 假设边界类回归

---

## 第二批：紧接着做

### 文件优先级
1. `langgraph_langchain/langgraph_agent.py`
2. `langgraph_langchain/api_server_langgraph.py`
3. 测试文件

### 目标
把执行路径变成阶段化、可恢复、可解释。

### 具体动作
- 引入 stage state
- 引入 failure taxonomy
- 引入 structured error detail
- 加 benchmark regression

---

## 第三批：然后做

### 文件优先级
1. `langgraph_langchain/api_server_langgraph.py`
2. `langgraph_langchain/schemas.py`
3. `langgraph_langchain/langgraph_agent.py`

### 目标
补 trace、lineage、observability、lifecycle 治理。

### 具体动作
- request_id / run_id / session_id 贯通
- artifact lineage 结构化
- workspace 清理策略细化
- 结构化日志字段补齐

---

## 第四批：最后做

### 文件优先级
1. `langgraph_langchain/langgraph_agent.py`
2. 未来可能新增模板模块
3. `webui/`（如果需要）

### 目标
逐步形成场景化产品能力。

---

## 五、如果只看“先改哪几个点”

如果现在只允许你先抓最关键的一小批代码点，我建议顺序是：

### Top 1
`langgraph_langchain/langgraph_agent.py` 中的：
- `_SYSTEM_PROMPT`
- `finish_report` 质量门控逻辑
- `python_repl` / 结构化 findings 的衔接

### Top 2
`langgraph_langchain/schemas.py`
- 增加 findings/evidence/assumption/metric-definition schema

### Top 3
`langgraph_langchain/test_reliability.py`
- 增加“可信度回归测试”而不只是“能跑通测试”

### Top 4
`langgraph_langchain/api_server_langgraph.py`
- 增加 failure taxonomy、trace id、结构化错误返回

---

## 六、最终结论

当前项目如果要继续往“真实可用的智能数据分析系统”推进，代码层的第一优先级不是加新接口，也不是做 UI，而是：

**围绕 `langgraph_agent.py` 建立可信度治理核心，再由 `schemas.py` 结构化承接，最后由 `api_server_langgraph.py` 把运行与治理能力补齐。**

也就是说，代码层最正确的推进顺序应该是：

1. 先改 `schemas.py` + `langgraph_agent.py`，补可信度模型
2. 再改 `langgraph_agent.py` + `api_server_langgraph.py`，补状态机和失败治理
3. 再补 tracing / lineage / lifecycle
4. 最后再做行业模板和产品化输出收敛

这是当前代码结构下，最稳也最值得投入的实施路径。
