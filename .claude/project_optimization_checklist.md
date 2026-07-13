# 项目优化实施 Checklist

## 文档目的

这份文档用于把：
- `.claude/project_optimization_roadmap.md`
- `.claude/project_optimization_implementation_plan.md`

进一步压缩成可直接执行、可勾选推进的 checklist。

适用方式：
- 可按阶段推进
- 可按模块推进
- 可作为迭代周计划 / 里程碑检查单

---

# 一、总优先级 Checklist

## P0：先做分析可信度底座

- [ ] 为 findings / evidence / assumption / metric definition 建立结构化 schema
- [ ] 让最终报告改为“基于 findings 生成”，而不是自由总结
- [ ] 增加“结论必须绑定证据”的质量门控
- [ ] 增加“趋势结论必须带时间窗口”的质量门控
- [ ] 增加“分组对比结论必须带分组维度”的质量门控
- [ ] 增加“因果化表达必须降级或标注假设”的质量门控
- [ ] 增加“业务口径声明块”强制输出
- [ ] 增加“字段语义不确定时必须声明假设”的约束
- [ ] 为可信度门控补测试

## P1：再做 Agent 收敛稳定性

- [ ] 为分析流程引入明确 stage/state
- [ ] 限制 finish_report 只能在最终阶段触发
- [ ] 建立 failure taxonomy
- [ ] 建立 recovery strategy matrix
- [ ] 对 python 执行错误、步数超限、report rejected 做统一 failure_code
- [ ] 建立 benchmark regression 样例集
- [ ] 为状态机和失败恢复补测试

## P2：再补可复核与工程治理

- [ ] 建立 trace / run / artifact lineage 结构
- [ ] 让 artifact 能关联到 step / finding / final report
- [ ] 增加 structured logs 字段
- [ ] 增加 request_id / run_id / session_id 贯通
- [ ] 细化 workspace / session 生命周期治理
- [ ] 细化用户错误提示分类
- [ ] 为 observability / lifecycle 增加测试

## P3：最后做产品化和行业化增强

- [ ] 明确单一主目标用户
- [ ] 选择一个行业场景优先模板化
- [ ] 沉淀行业指标口径和分析模板
- [ ] 收敛最终输出视图为“结论 + 证据 + 建议 + 风险”

---

# 二、按文件拆解 Checklist

## 1. `langgraph_langchain/schemas.py`

### Phase 1：可信度结构化
- [ ] 新增 `EvidenceItem` schema
- [ ] 新增 `Finding` schema
- [ ] 新增 `AnalysisAssumption` schema
- [ ] 新增 `MetricDefinition` schema
- [ ] 新增 report quality / credibility gate 结果 schema
- [ ] 为 finding 增加 confidence / evidence_level / hypothesis_flag 字段
- [ ] 为 evidence 增加 fields / artifacts / filters / time_window 字段

### Phase 2：状态机与失败治理
- [ ] 新增 `AnalysisStage` schema
- [ ] 新增 `StageResult` schema
- [ ] 新增 `FailureInfo` / `FailureCode` schema
- [ ] 新增 `RecoveryHint` schema

### Phase 3：trace 与 lineage
- [ ] 新增 `ArtifactRef` schema
- [ ] 新增 `ConclusionTrace` schema
- [ ] 新增 `RunTrace` schema
- [ ] 新增 artifact lineage 结构

---

## 2. `langgraph_langchain/langgraph_agent.py`

### Phase 1.1：报告从自由文本升级为 evidence-driven
- [ ] 调整 `_SYSTEM_PROMPT`，要求先形成 findings，再形成 report
- [ ] 明确 findings 至少包含 statement / evidence / assumptions / confidence
- [ ] 禁止没有 evidence 的核心结论直接进入最终报告
- [ ] 要求趋势类结论必须带 time window
- [ ] 要求分组类结论必须带 dimension / group
- [ ] 要求 driver claims 带 evidence level 或假设边界

### Phase 1.2：finish_report 可信度门控升级
- [ ] 梳理现有 finish_report 校验逻辑
- [ ] 从“文本长度/标题/数字存在性”升级到“结论可信度校验”
- [ ] 对无证据结论直接 reject
- [ ] 对强因果表达无证据时 reject 或降级
- [ ] 对口径声明缺失时 reject 或打高风险标记
- [ ] 对字段语义明显不确定但未声明时 reject

### Phase 1.3：schema 理解前置
- [ ] 强化 `load_data` 输出字段理解信息
- [ ] 标注主键候选 / 时间字段候选 / ID-like 字段
- [ ] 标注高基数字段风险
- [ ] 标注业务 metric 候选与排除项
- [ ] 评估是否拆出 `schema_profile` 类工具

### Phase 2.1：状态机化
- [ ] 在 `_Session` 增加当前阶段状态
- [ ] 在 `_Session` 记录阶段切换历史
- [ ] 在 `_Session` 记录阶段失败次数
- [ ] 在 `run_analysis_stream` 输出阶段变化
- [ ] 限制某些 tool 只能在特定阶段后调用
- [ ] 限制 finish_report 只能在 synthesis / final_report 阶段调用

### Phase 2.2：失败分类与恢复策略
- [ ] 为 repeated python errors 定义统一 failure_code
- [ ] 为 max steps exceeded 定义统一 failure_code
- [ ] 为 report rejected 定义统一 failure_code
- [ ] 为 schema uncertainty 定义统一 failure_code
- [ ] 输出 recovery_hint，而不是只给一句失败提示
- [ ] 区分 retryable / non-retryable

### Phase 3.1：trace / lineage
- [ ] 为每个 python step 记录 step_id
- [ ] 为每个 artifact 记录生成 step_id
- [ ] 为 finding 记录引用的 artifact / evidence
- [ ] 为最终 report 记录引用的 findings
- [ ] 建立 session 内 evidence → finding → report 的映射

### Phase 3.2：结构化日志
- [ ] 扩展 `_make_session_logger` 输出字段
- [ ] 记录 stage
- [ ] 记录 failure_code
- [ ] 记录 retry_count
- [ ] 记录 report gate rejection reason
- [ ] 若可行，记录 token / cost / latency

---

## 3. `langgraph_langchain/api_server_langgraph.py`

### Phase 2：结构化失败与 API 层治理
- [ ] 统一错误 detail 结构
- [ ] 为常见失败定义 type / code / message / retryability / hint
- [ ] `chat_completions` 返回结构化失败信息
- [ ] `_run_analysis` 保留更明确的终止原因
- [ ] 区分 cancelled / timeout / rejected / expired / missing_data_file

### Phase 3：trace 与生命周期治理
- [ ] 引入 request_id
- [ ] 引入 run_id
- [ ] 让 session_id / run_id / request_id 在日志中贯通
- [ ] `_serialize_session` 补更多治理字段
- [ ] 细化 stale session 清理后的状态处理
- [ ] 细化 cancel 后的 session / artifact 保留策略
- [ ] 细化 workspace 文件分类（输入/中间/最终产物）

### 可观测性
- [ ] 记录请求开始/结束日志
- [ ] 记录 endpoint latency
- [ ] 记录 session prune 数量
- [ ] 记录 cancel 来源和结果
- [ ] 记录 analysis run outcome

---

## 4. `langgraph_langchain/test_reliability.py`

### 当前测试补强
- [ ] 为 findings/evidence gate 增加测试
- [ ] 为时间窗口约束增加测试
- [ ] 为分组结论约束增加测试
- [ ] 为因果表达降级增加测试
- [ ] 为口径声明缺失增加测试

### 状态机测试
- [ ] 测试 finish_report 只能在最终阶段提交
- [ ] 测试未完成前置阶段时不能直接结束
- [ ] 测试 repeated python errors 的统一 failure_code
- [ ] 测试 max steps exceeded 的统一 failure_code

### 生命周期与 API 层测试
- [ ] 测试 cancel 后状态一致性
- [ ] 测试 stale session 清理后 artifact 状态一致性
- [ ] 测试 structured error detail 返回
- [ ] 测试 trace / run / lineage 字段落盘或返回

### benchmark 回归
- [ ] 增加干净单表样例
- [ ] 增加脏数据样例
- [ ] 增加多表样例
- [ ] 增加时间序列样例
- [ ] 增加字段歧义样例

---

# 三、按阶段推进 Checklist

## 阶段 1：可信度底座

### 里程碑定义
完成后应达到：
- 核心结论不能无证据通过
- 报告必须显式声明关键口径
- 趋势/分组/driver 结论更克制、更可解释

### Checklist
- [ ] 设计 findings / evidence / assumption schema
- [ ] 更新 prompt 要求 evidence-driven 输出
- [ ] 升级 finish_report 可信度门控
- [ ] 加入口径声明块
- [ ] 补充可信度相关测试
- [ ] 选 3~5 个真实样例回归验证效果

---

## 阶段 2：收敛稳定性

### 里程碑定义
完成后应达到：
- agent 路径更稳定
- 常见失败可分类
- 失败后能给出更合理恢复提示

### Checklist
- [ ] 设计 stage model
- [ ] 在 session 中落状态机字段
- [ ] 在 run loop 中接入阶段切换
- [ ] 设计 failure taxonomy
- [ ] 设计 recovery hint 结构
- [ ] 对典型失败路径补测试
- [ ] 建第一版 benchmark regression 集

---

## 阶段 3：可复核与治理

### 里程碑定义
完成后应达到：
- 结论能追到证据
- 产物能追到步骤
- 问题能快速定位
- workspace 可持续维护

### Checklist
- [ ] 建立 evidence → finding → report 映射
- [ ] 建立 artifact → step 映射
- [ ] 建立 run / request / session trace 字段
- [ ] 补 structured logs
- [ ] 补 workspace 生命周期策略
- [ ] 补错误提示分类
- [ ] 为治理能力补测试

---

## 阶段 4：产品化收敛

### 里程碑定义
完成后应达到：
- 项目不再是泛化 demo
- 至少在一个目标场景上开始形成模板化优势

### Checklist
- [ ] 明确主目标用户
- [ ] 选择单一优先行业
- [ ] 提炼领域指标口径
- [ ] 提炼领域分析模板
- [ ] 收敛最终输出结构

---

# 四、推荐执行顺序（最实用版）

## 第一批立刻开工
- [ ] 先改 `schemas.py`，补 findings/evidence 结构
- [ ] 再改 `langgraph_agent.py` 的 prompt 和 finish_report gate
- [ ] 再补 `test_reliability.py` 的可信度测试

## 第二批马上跟进
- [ ] 在 `langgraph_agent.py` 接 stage state
- [ ] 在 `api_server_langgraph.py` 接 failure taxonomy
- [ ] 补状态机和失败恢复测试

## 第三批随后推进
- [ ] 做 trace / lineage
- [ ] 做 structured logs
- [ ] 做 lifecycle 治理细化

## 第四批最后推进
- [ ] 选行业模板
- [ ] 收敛输出层
- [ ] 再考虑 UI 层配合优化

---

# 五、验收 Checklist

## 可信度验收
- [ ] 报告中的关键结论都能指出对应证据
- [ ] 趋势结论都带时间窗口
- [ ] 分组结论都带分组维度与具体组名
- [ ] 因果表达都带限制语或被降级
- [ ] 口径不明确时系统会声明不确定性

## 稳定性验收
- [ ] 同类任务多次执行路径更一致
- [ ] 重复 python 错误能被统一识别
- [ ] 超步数失败能被统一识别
- [ ] report rejected 能结构化返回

## 可治理性验收
- [ ] 能知道某个 artifact 来自哪个 step
- [ ] 能知道某个 finding 引用了哪些 artifact / evidence
- [ ] 能知道一次分析为何结束
- [ ] stale / cancel / delete 后 session 状态一致

## 产品方向验收
- [ ] 已明确一个主目标用户
- [ ] 已明确一个优先行业场景
- [ ] 输出结构更适合业务阅读，而不是技术调试

---

# 六、最关键的一句话

如果执行过程中只能始终盯住一条主线，请反复检查：

**是不是在提升“分析可信度和可复核性”，而不是只是在提升“报告看起来更完整”。**

这条线不偏，项目方向就不会偏。
