# 11 - 完整运行流程全景图

> 本文以一次完整的数据分析为例，展示从用户上传文件到生成报告的全过程，标注每个环节涉及的代码位置。

---

## 11.1 前提：用户上传数据文件

```
用户在 Streamlit 界面点击"上传文件"，选择 sales.csv
    │
    │  POST /workspace/upload
    │  {session_id: "auto", file: sales.csv}
    ▼
FastAPI: get_or_create_session()
    │
    ├── 生成 session_id = "abc-123"
    ├── 创建 workspace/abc-123/
    ├── 保存文件到 workspace/abc-123/sales.csv
    ├── 注册到 SESSIONS["abc-123"] = {files: [{path: "...", name: "sales.csv"}]}
    └── 持久化到 workspace/.sessions.json
```

---

## 11.2 完整分析流程

### 阶段 0：INIT → SCHEMA_UNDERSTANDING

```
用户输入: "分析这份销售数据的区域差异"
    │
    │  POST /v1/chat/completions
    │  {session_id: "abc-123", message: "分析...", stream: true}
    ▼
FastAPI: _run_analysis()
    │
    ├── 获取 session → SESSIONS["abc-123"]
    ├── 解析数据文件 → /workspace/abc-123/sales.csv
    ├── 创建 cancel_event
    ├── 获取 Semaphore(3) 并发许可
    │
    │  调用 run_analysis_stream(instruction, source_path, ...)
    ▼
run_analysis_stream()
    │
    ├── 创建 TraceContext(session_id="abc-123")
    ├── 创建 _Session(workspace_dir, source_path, session_id, instruction)
    │     session.current_stage = "schema_understanding"
    │     session.ns = {WORKSPACE_DIR, SOURCE_PATH, Path, save_fig, ...}
    │     session.state_machine = AnalysisStateMachine()  # INIT
    │
    ├── 创建 tools = _make_tools(session)
    │     返回 [load_data, python_repl, eda_profile, record_finding, ...]
    │
    ├── 创建 LLM = ChatOpenAI(model="deepseek-chat", streaming=True)
    │
    ├── 创建 agent = create_react_agent(LLM, tools, prompt=_SYSTEM_PROMPT)
    │     内部构建: agent → tools → agent → ... → END
    │
    └── 构建初始消息
          messages = [
              SystemMessage("你是数据分析专家..."),
              HumanMessage("Task: 分析...数据\nData file: sales.csv\n...")
          ]

════════════════════════════════════════════════════════════════════
ReAct 循环开始
════════════════════════════════════════════════════════════════════
```

### 阶段 1：加载和理解数据

```
第 1 轮: Agent 节点 → LLM 推理
    │  LLM 看到用户消息，决定先加载数据
    │  AIMessage(tool_calls=[{name:"load_data", args:{file_path:"..."}}])
    ▼
事件: on_tool_start  name=load_data
    │  step = 1
    │  状态机: INIT → SCHEMA_UNDERSTANDING
    │  SSE 推送: "> Step 1 load_data(sales.csv)"
    ▼
load_data() 执行
    │  _validate_tool_stage("load_data") → ✅ INIT 阶段允许
    │  自动编码检测: utf-8 → gbk → latin-1
    │  df = pd.read_csv("sales.csv", encoding="utf-8")
    │  session.ns["df"] = df  ← 注入命名空间
    │  状态机: add_condition("schema_documented")
    │  状态机: try_advance_stage() → DATA_QUALITY_CHECK
    │  返回: "File: sales.csv\nShape: 200×8\nColumns: ..."
    ▼
事件: on_tool_end  name=load_data
    │  session.complete_stage("schema_understanding")
    │  SSE 推送: 文件信息
```

### 阶段 2：自动 EDA

```
第 2 轮: Agent 节点 → LLM 推理
    │  LLM 看到 schema 信息，决定运行 EDA
    │  AIMessage(tool_calls=[{name:"eda_profile", args:{}}])
    ▼
事件: on_tool_start  name=eda_profile
    │  step = 2
    │  SSE 推送: "> Step 2 eda_profile - running auto EDA..."
    ▼
eda_profile() 执行
    │  自动检测: 5 个数值列, 3 个类别列, 1 个时间列
    │  生成图表:
    │    ├── eda_missing_values.png
    │    ├── eda_outliers.png
    │    ├── eda_correlation_heatmap.png
    │    ├── eda_distributions.png
    │    └── eda_time_trend.png
    │  生成分析信号:
    │    ├── "revenue 缺失率 1.5%"
    │    ├── "revenue 偏度 2.35，可能长尾"
    │    ├── "revenue ~ quantity r=0.85"
    │    └── "建议按 region 分组分析 revenue"
    │  状态机: add_condition("quality_assessed")
    │  返回: EDA Profile 完整报告
    ▼
事件: on_tool_end  name=eda_profile
    │  session.complete_stage("data_quality_check")
    │  SSE 推送: EDA 结果 + 图表 artifacts
    │  状态机: → BASIC_EDA
```

### 阶段 3-4：深入分析

```
第 3 轮: python_repl — 按 region 分析
    │  code = """
    │  # 步骤目标: 按 region 分析 revenue 差异
    │  # 方法: groupby 聚合
    │  fix_chinese()
    │  summary = df.groupby('region').agg(
    │      total=('revenue','sum'), avg=('revenue','mean')
    │  ).sort_values('total', ascending=False)
    │  print(summary)
    │  # 关键结果: 华东最高 420K (42%)
    │  # 建议下一步: 画区域对比图
    │  """
    ▼
    │  _validate_python_repl_step(code) → ✅ 4 个标记都有
    │  session.run_code(code) → exec(code, session.ns)
    │  输出: 华东 420000 7000 / 华南 280000 5600 / ...
    │  状态机: → DEEP_DIVE

第 4 轮: python_repl — 画图
    │  画区域 revenue 对比柱状图
    │  save_fig("revenue_by_region.png")
    │  SSE 推送: 图表 artifact

第 5 轮: record_finding — 记录发现 F001
    │  statement = "华东区 revenue 占总量 42%"
    │  evidence_level = "A"  ← 事实描述
    │  source_fields = ["region", "revenue"]
    │  stats = {"north": 420000, "total": 1000000}
    │  → Finding F001 recorded
    │  状态机: add_condition("findings_recorded")

第 6 轮: python_repl — 时间趋势分析
    │  按月聚合 revenue
    │  发现 Q3 上升 15%

第 7 轮: record_finding — 记录发现 F002
    │  statement = "Q3 revenue 环比上升 15%"
    │  evidence_level = "B"  ← 相关线索
    │  time_window = "2025-Q2 → 2025-Q3"

第 8 轮: python_repl — 异常检测
    │  发现 8 月异常峰值

第 9 轮: record_finding — 记录发现 F003
    │  statement = "8 月 revenue 出现异常峰值"
    │  evidence_level = "B"
    │  状态机: add_condition("min_findings_count")  ← 3 条了
    │  状态机: → CONCLUSION_SYNTHESIS
```

### 阶段 5：结论综合

```
第 10-11 轮: 整理发现
    │  LLM 综合所有 finding
    │  确保 evidence_level 和因果语言一致
    │  状态机: → REPORT_GENERATION
```

### 阶段 6：报告生成

```
第 12 轮: finish_report
    │  markdown = """
    │  ## 摘要
    │  华东区是 revenue 的主要贡献者...
    │
    │  ## 数据说明
    │  时间范围: 2025-Q1 ~ 2025-Q3
    │  指标定义: revenue = SUM(order_amount)
    │  去重规则: 按 order_id
    │
    │  ## 关键发现
    │  - 华东区 revenue 占总量 42% (420K/1M)
    │  - Q3 revenue 环比上升 15%
    │  - 8 月出现异常峰值
    │
    │  ## 数据质量
    │  revenue 缺失 1.5%，3 条记录
    │
    │  ## 分析
    │  ...
    │
    │  ## 建议
    │  - 关注华东区的成功经验
    │  - 调查 8 月峰值的原因
    │  """
    ▼
finish_report() 验证链
    │
    ├── ✅ 阶段验证: REPORT_GENERATION 阶段允许
    ├── ✅ 一次性检查: report is None
    ├── ✅ Finding 数量: 3 ≥ 2
    ├── ✅ 报告长度: 1200 ≥ 400
    ├── ✅ Markdown 标题: 有
    ├── ✅ Data Context 章节: 有（时间范围、指标定义、去重规则）
    ├── ✅ 核心章节: Summary ✅ Key Findings ✅ Data Quality ✅ Analysis ✅
    ├── ✅ 证据标记: 关键发现中有数字和百分比
    ├── ✅ 指标声明: declare_metric 已调用
    ├── ✅ evidence_level 一致性: 通过
    └── ✅ 建议与证据匹配: 通过
    │
    ▼ 报告通过验证！

    保存文件:
    ├── workspace/abc-123/data_analysis_report.md     ← 完整报告
    ├── workspace/abc-123/analysis_findings.json       ← 结构化发现
    ├── workspace/abc-123/lineage_graph.json           ← 数据血缘
    └── workspace/abc-123/agent_abc-123.log            ← 会话日志

    状态机: REPORT_GENERATION → COMPLETED
```

### 最终输出

```
第 13 轮: Agent 节点 → LLM 最终回复
    │  AIMessage(content="分析完成。主要发现：\n1. 华东区...")
    │  无 tool_calls → END
    ▼
SSE 推送: data: [DONE]
    │
    ▼
Streamlit 渲染完整报告 + 图表
```

---

## 11.3 会话文件产出

分析完成后，`workspace/abc-123/` 目录内容：

```
workspace/abc-123/
├── sales.csv                       ← 用户上传的原始数据
├── eda_missing_values.png          ← EDA: 缺失值图
├── eda_outliers.png                ← EDA: 异常值箱线图
├── eda_correlation_heatmap.png     ← EDA: 相关性热力图
├── eda_distributions.png           ← EDA: 分布直方图
├── eda_time_trend.png              ← EDA: 时间趋势图
├── revenue_by_region.png           ← 分析: 区域对比柱状图
├── time_trend_revenue.png          ← 分析: revenue 月度趋势
├── data_analysis_report.md         ← 最终分析报告
├── analysis_findings.json          ← 结构化发现数据
├── lineage_graph.json              ← 数据血缘图
├── agent_abc-123.log               ← Agent 运行日志
└── session_metadata.json           ← 会话元数据
```

---

> **下一步**：阅读 [12-learning-path.md](12-learning-path.md) 获取学习路径建议和 API 速查。
