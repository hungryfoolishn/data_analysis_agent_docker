# 数据分析 Agent 项目优化总结与路线图

**文档日期**：2026-04-09  
**项目路径**：`/python/pragrams/data_analysis_agent`  
**当前后端**：LangGraph + LangChain (`langgraph_langchain/`)

---

## 一、项目概览

### 当前架构

```text
用户 / WebUI (Streamlit)
        ↓
langgraph_langchain/api_server_langgraph.py (FastAPI + SSE)
        ↓
langgraph_langchain/langgraph_agent.py (LangGraph ReAct Agent)
        ↓
工具: load_data / eda_profile / python_repl / finish_report
        ↓
workspace/<session_id>/ 中的图表、日志、报告等产物
```

### 核心能力

1. **LangGraph ReAct 数据分析 Agent**：基于 `create_react_agent` 执行分析流程
2. **分析师式数据分析流程**：先计划、再 EDA、再分步深入分析、最后生成结构化报告
3. **运行时 guardrails**：`python_repl` 强制小步执行、限制单步体量、要求中英双语步骤标记
4. **实时流式输出**：SSE 持续返回分析过程，前端实时展示
5. **会话级工作空间**：上传文件、分析产物、日志按 `session_id` 隔离
6. **自动产物追踪**：图表、报告等文件自动加入下载列表
7. **中文分析与中文图表支持**：报告默认中文输出，matplotlib 已补齐中文字体处理

---

## 二、已完成的优化

### 1. LangGraph 后端重写 ✅

**时间**：项目早期  
**内容**：
- 从 smolagents 切换到 LangGraph + LangChain
- 使用 `create_react_agent` 驱动 ReAct 流程
- 工具链：`load_data` / `python_repl` / `finish_report`
- 异步流式输出：`astream_events`
- 执行器改用 `exec() + 持久 namespace`

**位置**：`langgraph_langchain/`

---

### 2. 前端体验优化 ✅

**时间**：约 10 天前  
**内容**：
1. 生成文件下载去重
2. 实时输出限制在固定滚动框内
3. matplotlib 中文字体修复
4. 流式文本保留换行结构

**位置**：
- `webui/app.py:81-101` (去重逻辑)
- `webui/app.py:255-289` (换行保留)
- `webui/app.py:676` (固定滚动容器)
- `langgraph_langchain/langgraph_agent.py:303-349` (中文字体)

---

### 3. 分析能力增强 ✅

**时间**：约 9 天前  
**内容**：
- 强化系统提示词，要求分析师式流程
- `eda_profile()` 增加 `Analysis Signals`
- `_Session.ns` 预置高价值 helper：
  - `profile_dimension` - 分组画像
  - `compare_segments` - 分群对比
  - `time_trend` - 时间趋势
  - `detect_anomalies` - 异常检测
  - `explain_metric_change` - 变化解释
  - `decompose_metric_change` - 指标变化拆解
  - `rank_driver_candidates` - 驱动因素排序
  - `check_metric_definition_risk` - 指标定义风险检查
  - `run_counterfactual_checks` - 反事实检查
- `finish_report()` 要求结构化、证据化、专业中文表达

**位置**：`langgraph_langchain/langgraph_agent.py:160-269`

---

### 4. 运行时 guardrails ✅

**时间**：约 9 天前  
**内容**：
- `python_repl` 小步执行约束
- 拒绝空步骤
- 限制单步体量（50 行）
- 中英双语步骤标记支持：
  - 英文：`step objective / method / key results / suggested next step`
  - 中文：`步骤目标 / 方法 / 关键结果 / 建议下一步`
- `finish_report` 一次性保护

**位置**：
- `langgraph_langchain/langgraph_agent.py:34-39` (双语标记)
- `langgraph_langchain/langgraph_agent.py:65-92` (validator)
- `langgraph_langchain/langgraph_agent.py:31` (行数限制)

---

### 5. E2E 验证通过 ✅

**时间**：约 9 天前  
**结果**：3/3 通过
- `grouped_sales`
- `time_series_anomaly`
- `quality_issues`

**关键修复**：
- 双语 marker 支持（之前只认英文导致误判）
- 单步上限从 40 调到 50 行
- prompt 要求最终综合拆小步

---

### 6. 后端 API 完善 ✅

**内容**：
- Session 管理与持久化
- 文件上传与 workspace 隔离
- 流式 SSE 响应
- 取消任务支持
- 结构化错误处理与失败策略矩阵
- Session TTL 与自动清理

**位置**：`langgraph_langchain/api_server_langgraph.py`

---

## 三、已完成的优化（Week 1-3）

### Week 1: 分析可信度底座 ✅

**完成时间**: 2026-04-09  
**状态**: ✅ 已完成并测试通过

**核心改进**:
1. **结构化 findings 收集机制**
   - 新增 `record_finding` 工具，记录结构化结论
   - 新增 `declare_metric` 工具，显式声明指标定义
   - 新增 `declare_assumption` 工具，显式声明分析假设
   - 强制要求至少 2 个 findings 才能提交报告

2. **业务口径显式声明**
   - 强制要求报告包含 "Data Context" 章节
   - 必须声明：时间范围、指标定义、去重规则、分母来源、关键假设
   - Validator 检查 Data Context 完整性

3. **结构化输出**
   - 自动生成 `analysis_findings.json` 文件
   - 包含所有 findings、metric_definitions、assumptions

**详细文档**: `.claude/week1_implementation_2026-04-09.md`

---

### Week 2: Agent 收敛稳定性 ✅

**完成时间**: 2026-04-09  
**状态**: ✅ 已完成并测试通过

**核心改进**:
1. **扩展失败分类体系**
   - 从 8 种扩展到 15 种失败类型
   - 每种失败类型定义了恢复策略

2. **分析状态机**
   - 8 阶段固定流程：init → schema_understanding → data_quality_check → basic_eda → deep_dive → conclusion_synthesis → report_generation → completed/failed
   - 入口/出口条件验证
   - 步数限制防止无限循环

3. **自动恢复策略**
   - RecoveryExecutor 自动执行恢复
   - 三种策略：retry_same_scope、retry_narrower_scope、user_action_required
   - 最多重试 2-3 次

4. **稳定性指标跟踪**
   - 会话级别和聚合级别指标
   - 5 个新 API 端点：/metrics/stability、/metrics/failures 等

5. **Benchmark 框架**
   - 10 个测试用例，覆盖 easy/medium/hard 三个难度
   - 数据生成脚本

**详细文档**: `.claude/week2_implementation_2026-04-09.md`

---

### Week 3: 可复核治理 ✅

**完成时间**: 2026-04-09  
**状态**: ✅ 已完成并测试通过（17/17 tests passed）

**核心改进**:
1. **增强证据-结论绑定**
   - Finding 结构新增：finding_id、stats、calculation_method、category
   - EvidenceItem 新增：stats、calculation_method
   - 每个结论可追溯到具体统计数据和计算方法

2. **证据等级分类系统**
   - Level A (Facts): 纯事实描述，无关系声明
   - Level B (Correlations): 观察到的模式、趋势、相关性
   - Level C (Causal): 因果声明，需要强证据（时序、对照组、机制、排除替代解释）
   - 新增 `evidence_validator.py` 模块验证证据等级

3. **因果推断约束**
   - 禁止使用因果语言（"caused by", "driven by", "导致", "驱动"）除非 evidence_level="C"
   - Level C 要求至少 2 个证据项
   - 在 finish_report 中自动验证

4. **建议-证据绑定**
   - 三种建议类型：immediate_action、validation、observation
   - 建议类型基于证据强度自动分类
   - 新增 `recommendation_validator.py` 模块验证建议与证据匹配
   - 强行动建议需要高置信度 + Level B/C + 多个证据

5. **Agent Prompt 更新**
   - 在 workflow 中添加证据等级指南
   - 在 reasoning rules 中添加证据等级纪律要求
   - 强制使用 decompose_metric_change 和 rank_driver_candidates 评估证据强度

**测试覆盖**:
- 9 个证据验证器测试
- 8 个建议验证器测试
- 100% 通过率

**详细文档**: 
- `.claude/week3_implementation_2026-04-09.md`
- `.claude/week3_test_report_2026-04-09.md`

---

## 四、待优化方向（按优先级分层）

### 🔴 P0：分析可信度（最高优先级）✅ 已完成

Week 1-3 已完成 P0 优化的核心内容：
- ✅ 结论-证据绑定机制
- ✅ 业务口径显式声明
- ✅ 证据等级分层（A/B/C）
- ✅ 建议与证据绑定
- ⚠️ 从"描述"升级到"归因候选"（部分完成，helper 已存在但需进一步集成）

#### 待完善项

##### 3. 从"描述"升级到"归因候选"

**目标**：不只说"发现了什么"，还要说"是谁贡献了变化"

**实现建议**：
对每个重要发现强制走这套模板：
```text
现象
→ 影响范围
→ 贡献拆解
→ 候选驱动因素
→ 证据强度
→ 待验证假设
→ 可执行建议
```

**已有基础**：
- `decompose_metric_change()` - 已实现在 `langgraph_agent.py:545-636`
- `rank_driver_candidates()` - 已实现在 `langgraph_agent.py:654-702`
- `check_metric_definition_risk()` - 已实现在 `langgraph_agent.py:704-754`
- `run_counterfactual_checks()` - 已实现在 `langgraph_agent.py:756-799`

**待完善**：
- 这些 helper 已存在，但可能还没有完全集成到主流程和 prompt 中
- 需要在 `_SYSTEM_PROMPT` 中强制要求使用这些 helper
- 需要在 `finish_report` validator 中检查是否使用了这些 helper

---

##### 4. 证据等级分层

**目标**：区分事实、线索、假设

**实现建议**：
强制区分三类表述：

**A 级：事实描述**
- 例如：North 区域 Q3 revenue 占比 42%，高于 South 的 27%。

**B 级：相关线索**
- 例如：Revenue 下滑与 orders 同期下降同时出现，二者方向一致，可作为线索。

**C 级：接近因果的判断**
- 只有满足更强条件时才允许：
  - 时间先后明确
  - 分组对照存在
  - 机制上说得通
  - 替代解释被部分排除
- 否则必须使用："可能"、"疑似"、"需要进一步验证"

**实现方式**：
- 在 `finish_report` validator 中检查
- 如果报告里出现"原因 / 驱动 / 导致 / 根因"等词，必须至少满足之一：
  - 有贡献拆解结果
  - 有多窗口一致性
  - 有分层验证
  - 有明确不确定性说明

---

##### 5. 建议与证据绑定

**目标**：没有满足证据门槛，就不给强行动建议

**实现建议**：
建议分三类输出：

**A. 立即行动建议**
仅当：
- 影响大
- 证据强（A 级或 B 级）
- 风险明确
- 可操作对象清楚

**B. 验证型建议**
当证据中等时，输出：
- 还应看什么切片
- 还应补什么数据
- 还应做什么对照分析

**C. 观察型建议**
当只是弱信号时，输出：
- 持续监测
- 建立预警阈值
- 下一周期复核

**预期收益**：降低"伪洞察"概率，提升分析可信度

---

### 🟠 P1：Agent 收敛稳定性

#### 核心问题

当前稳定性更多是由 prompt 约束、validator、guardrails "硬控"出来的。一旦模型版本、任务复杂度变化，容易退化。

#### 优化方向

##### 1. 分析状态机化

**目标**：减少 agent 自由跳转和无效往返

**实现建议**：
固化阶段：
1. schema 理解
2. 数据质量检查
3. 基础 EDA
4. 定向深挖
5. 结论整理
6. 报告生成

**实现方式**：
- 在 `_Session` 中增加状态机逻辑
- 每个阶段有明确的入口条件和出口条件
- 不允许跳过关键阶段

---

##### 2. 失败分类体系

**目标**：让失败更可解释

**实现建议**：
对失败做明确 taxonomy：
- `schema_understanding_failed` - 数据理解失败
- `field_semantic_unclear` - 字段语义不明确
- `tool_execution_failed` - 工具执行失败
- `python_execution_error` - 代码运行失败
- `reasoning_drift` - 推理偏航
- `report_generation_failed` - 报告生成失败
- `timeout` - 超时
- `session_interrupted` - 长 session 中断

**已有基础**：
- `api_server_langgraph.py:72-115` 已有部分失败策略矩阵

**待完善**：
- 扩展失败分类
- 每种失败类型对应明确的恢复策略

---

##### 3. 分类型恢复策略

**目标**：不是统一重试，而是针对性恢复

**实现建议**：
- 代码报错 → 修复后重试当前步骤
- 字段语义不清 → 输出待确认，不强推断
- 数据质量差 → 自动降级分析范围
- 结果不收敛 → 提前结束并报告局限性

**实现方式**：
- 在 `run_analysis_stream` 中增加恢复逻辑
- 根据 `failure_code` 选择恢复策略

---

##### 4. 稳定性 benchmark 集

**目标**：建立固定回归数据集

**实现建议**：
至少覆盖：
- 单表干净数据
- 单表脏数据（缺失值、异常值、重复值）
- 多表关联数据
- 时间序列数据
- 半结构化字段
- 行业场景样例（电商、SaaS、销售等）

**实现方式**：
- 在 `langgraph_langchain/test_reliability.py` 中扩展测试集
- 每次优化后跑回归测试

---

##### 5. 核心稳定性指标

**目标**：持续跟踪系统表现

**实现建议**：
需要持续跟踪：
- 任务成功率
- 中断率
- 平均步数
- 平均耗时
- 重试率
- 空结论率
- 高风险结论率

**预期收益**：从"偶尔跑通"走向"多数情况下都能稳定跑通"

---

### 🟡 P2：可复核与工程治理

#### 核心问题

系统离"放心交付"还差底座能力：结论追踪、可观测性、错误分类、资源治理。

#### 优化方向

##### 1. 全链路可追踪

**目标**：能回答"这个结论是怎么得出的"

**实现建议**：
建立 request / session / run / artifact 的关联链路，能回答：
- 用户问了什么
- 系统分析了哪些数据
- 中间执行了哪些步骤
- 生成了哪些图和文件
- 最终哪个结论对应哪些证据

**实现方式**：
- 在 `_Session` 中增加 `trace_id`
- 每个 finding 记录 `trace_id`
- 每个 artifact 记录 `trace_id`

---

##### 2. 结构化日志与 tracing

**目标**：提升运维和调试效率

**实现建议**：
统一记录：
- session_id / run_id / trace_id
- 阶段名
- 耗时
- 工具调用
- 失败码
- token / 成本估计

**已有基础**：
- `langgraph_agent.py:143-158` 已有 session logger

**待完善**：
- 增加结构化日志格式
- 增加 tracing 支持

---

##### 3. 文件与 workspace 生命周期治理

**目标**：workspace 不再越跑越脏

**实现建议**：
- 产物命名规范
- TTL（已有：`api_server_langgraph.py:53-54`）
- 清理机制
- 取消任务后的回收策略

**待完善**：
- 增加定期清理任务
- 增加 workspace 大小监控

---

##### 4. 用户可理解的错误系统

**目标**：用户更能理解失败原因

**实现建议**：
避免统一"分析失败"，而改成：
- 数据格式不符合要求
- 字段含义不明确
- 分析未收敛
- 运行超时
- 结果可信度不足
- 文件已失效

**已有基础**：
- `api_server_langgraph.py:72-150` 已有结构化错误处理

**待完善**：
- 前端展示优化
- 错误提示文案优化

---

##### 5. 成本与吞吐治理

**目标**：控制成本和资源消耗

**实现建议**：
- 轻重任务分级
- 不同阶段模型路径优化
- 无效循环限制
- 重复分析缓存 / 避免重复计算

**预期收益**：运维和调试效率提升，用户更能理解失败原因

---

### 🟢 P3：产品化与行业化

#### 核心问题

当前系统容易落入中间态：对普通用户偏复杂，对高级分析师又不够可信。

#### 优化方向

##### 1. 明确目标用户

**实现建议**：
先选一个主用户群：
- 数据分析师
- 业务运营
- 内部技术团队
- 垂直行业客户

---

##### 2. 行业分析模板

**实现建议**：
沉淀行业知识：
- 电商：流量 → 转化 → 客单价 → 履约
- SaaS：线索 → 激活 → 留存 → 扩张
- 内容平台：曝光 → 点击 → 停留 → 转化
- 销售：线索 → 商机 → 赢单率 → 客单价

---

##### 3. 交互收敛

**实现建议**：
对非技术用户隐藏：
- session_id
- workspace
- 工具细节
- 中间运行机制

强化：
- 结论
- 证据
- 建议行动
- 风险提示

---

##### 4. 接入真实业务数据流

**实现建议**：
逐步从"上传文件分析"扩展到：
- 数据库
- BI / 报表源
- 内部数据接口
- 周期化分析任务

**预期收益**：形成"稳定 + 行业化"的差异化优势

---

## 四、建议的执行顺序（4 周计划）

### Week 1：分析可信度底座

**目标**：把"没有证据的漂亮结论"先压下去

**本周重点**：
1. 定义 findings 结构
2. 改造报告生成链路（基于 findings）
3. 增加口径声明块
4. 增加高风险结论 validator

**预期结果**：
- 系统报告更"克制"
- 幻觉型结论下降
- 用户更容易知道结论从哪来

---

### Week 2：Agent 收敛稳定性

**目标**：让执行过程更稳定，不靠运气

**本周重点**：
1. 固化状态机
2. 失败分类落地
3. 分类型恢复策略
4. 建第一版 benchmark 集

**预期结果**：
- 同类任务跑法更一致
- 失败更可解释
- 回归验证开始具备基准

---

### Week 3：可复核与可观测性

**目标**：让"怎么得出这个结论的"可以被追踪

**本周重点**：
1. 建立 conclusion → evidence → artifact 链路
2. 补 structured logs
3. 建 workspace 生命周期策略
4. 改错误提示文案

**预期结果**：
- 运维和调试效率明显提升
- 用户更能理解失败原因
- 结果可信链条开始成型

---

### Week 4：行业化增强 ✅ 已完成

**完成时间**: 2026-04-10  
**状态**: ✅ 已完成

**目标**：在底层稳定之后，开始做"可落地场景"

**本周重点**：
1. ✅ 选一个主用户画像：数据分析师
2. ✅ 选一个行业做模板化：研发管理效能提升
3. ✅ 沉淀领域口径：20+ 研发效能指标定义
4. ✅ 收敛交互表达：7 个分析模板

**实施内容**：
1. **领域知识集成** (`rd_efficiency_domain.py`)
   - 20+ 研发效能指标定义（velocity, quality, collaboration, delivery, planning）
   - 4 个常见分析模式
   - 业务假设和风险点

2. **指标计算和验证库** (`rd_metric_library.py`)
   - 8 个计算函数（velocity, throughput, cycle time, defect rate, etc.）
   - 4 个验证函数
   - 4 个解释助手（interpret_velocity_trend, interpret_cycle_time, etc.）
   - 4 个数据质量检查

3. **领域特定验证器** (`rd_validators.py`)
   - 指标定义验证
   - Finding 验证（检测 anti-patterns）
   - 分析完整性验证
   - 数据质量验证（sprint, PR, deployment）

4. **分析模板** (`rd_templates.py`)
   - 7 个模板：Sprint Retrospective, Velocity Trend, Code Quality Trend, Escaped Defects RCA, Cycle Time, Deployment Frequency, PR Review
   - 模板匹配逻辑（基于用户问题和数据列）

5. **Agent 集成**
   - 系统 prompt 增强：研发效能领域专业知识
   - eda_profile：自动检测研发数据并推荐模板
   - record_finding：集成研发效能验证
   - declare_metric：关联研发指标库
   - finish_report：验证研发分析完整性

**预期结果**：
- ✅ 开始出现可复用的场景模板（7 个模板）
- ✅ 项目方向更聚焦（数据分析师 + 研发效能）
- ✅ 逐步形成差异化能力（领域专业知识 + anti-pattern 检测）

**详细文档**: `.claude/week4_implementation_2026-04-09.md`

---

## 五、优先级总结

### P0（立即开始）
- 结论绑定证据
- 口径显式声明
- 高风险结论 validator
- schema 理解前置

### P1（紧接着做）
- agent 状态机
- failure taxonomy
- 恢复策略矩阵
- benchmark 数据集

### P2（稳定后补齐）
- trace / lineage
- structured logging
- workspace 生命周期治理
- 错误反馈分层
- 成本治理

### P3（底座形成后推进）
- 目标用户聚焦
- 行业模板
- 数据源接入
- 非技术化交互收敛

---

## 六、关键原则

### 原则 1：先修"错得很像对"的问题

当前最危险的不是失败，而是：

**系统给出了流畅、完整、像样的结论，但这些结论并不一定足够可信。**

因此第一优先级必须是降低"伪洞察"的概率。

### 原则 2：先补底座，再加功能

不优先做：
- UI 包装
- 表层交互增强
- 额外展示型能力堆叠

优先做：
- 证据绑定
- 口径声明
- validator
- 收敛状态机
- tracing / observability

### 原则 3：把"能跑"升级成"可证明地稳定"

优化目标不应是"多做几个 demo 能跑通"，而应是：
- 更高成功率
- 更低错误结论率
- 更清晰失败类型
- 更稳定恢复策略
- 更强跨数据集泛化能力

---

## 七、最终建议

当前阶段的正确顺序应该是：

**先可信度，后稳定性；先底座，后包装；先证明可靠，再放大体验。**

具体顺序：

1. 先做分析可信度底座
2. 然后做 agent 收敛稳定性
3. 再做可复核与工程治理
4. 最后做产品化与行业化增强

这是当前最稳、也最符合项目阶段的优化路径。

---

## 参考文档

- `.claude/explanatory_analysis_optimization.md` - 解释性分析优化方案
- `.claude/project_optimization_roadmap.md` - 项目优化路线图
- `.claude/langgraph_analysis_guardrails_and_e2e_tuning.md` - 本轮优化记录
- `README.md` - 项目说明
- `API_README.md` - API 接口说明
- `langgraph_langchain/README.md` - LangGraph 后端说明
