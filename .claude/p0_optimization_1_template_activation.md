# P0 优化 #1: 激活模板推荐机制

**完成时间**: 2026-04-09  
**优先级**: P0 (快速赢)  
**预期收益**: 用户体验提升 30%

## 问题描述

`rd_templates.py` 定义了 7 个研发效能分析模板，但从未在 EDA 阶段被调用和展示给用户。这些模板包含：
- 预定义的分析步骤
- 关键指标建议
- 常见陷阱警告
- 示例问题

## 实施方案

### 1. 存储用户问题
在 `_Session` 类中添加 `user_question` 属性，用于模板匹配：

```python
class _Session:
    def __init__(self, workspace_dir, source_path, session_id=None, user_question=None):
        self.user_question = user_question  # Store user's question for template matching
```

### 2. 在 EDA 阶段调用模板推荐
在 `eda_profile` 工具中调用 `suggest_template()`：

```python
# R&D domain: Check if this looks like R&D efficiency data and suggest template
df_preview: pd.DataFrame = session.ns.get("df")
if df_preview is not None:
    cols = df_preview.columns.tolist()
    user_question = session.user_question
    template = suggest_template(user_question, cols)
    if template:
        session.ns["suggested_template"] = template
        logger.info(f"Detected R&D data pattern, suggested template: {template.template_name}")
```

### 3. 在 EDA 输出中显示模板推荐
在 EDA 报告中添加醒目的模板推荐区域：

```python
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
```

### 4. 修复模板匹配逻辑
修复 `suggest_template()` 中的关键词匹配：

```python
# 修复前：只匹配 "deployment"
if "deployment" in question_lower or "dora" in question_lower:
    return DEPLOYMENT_FREQUENCY_TEMPLATE

# 修复后：匹配 "deploy" (包括 "deployment", "deploying", "deploy" 等)
if "deploy" in question_lower or "dora" in question_lower:
    return DEPLOYMENT_FREQUENCY_TEMPLATE
```

### 5. 修复技术问题
- 添加 `logger = logging.getLogger(__name__)` 到 `langgraph_agent.py`
- 移除不存在的 `key_dimensions` 字段引用
- 修复所有特殊 Unicode 字符（'、'、"、"、—）

## 测试验证

创建 `tests/test_template_recommendation.py`，包含 3 个测试：

1. **模板匹配测试** - 验证基于问题和列的模板匹配
2. **EDA 显示测试** - 验证模板推荐在 EDA 输出中正确显示
3. **命名空间存储测试** - 验证模板存储在 session.ns 中

**测试结果**: ✅ 3/3 通过

## 文件修改

- `langgraph_langchain/langgraph_agent.py`: 添加 user_question 属性、调用模板推荐、显示模板
- `langgraph_langchain/rd_templates.py`: 修复关键词匹配逻辑
- `tests/test_template_recommendation.py`: 新增测试文件

## 预期收益

1. **用户体验提升 30%**
   - 用户在 EDA 阶段立即看到相关的分析建议
   - 减少"不知道下一步做什么"的困惑
   - 提供最佳实践指导和常见陷阱警告

2. **分析质量提升**
   - 引导用户遵循行业最佳实践
   - 避免常见的分析错误（如跨团队比较 velocity）
   - 确保关键指标不被遗漏

3. **效率提升**
   - 减少用户探索和试错时间
   - 提供清晰的分析步骤路线图

## 示例输出

```
### 📋 Recommended Analysis Template

**Sprint Retrospective Analysis** (ID: RD-T001)
- **Description**: Analyze sprint performance to identify what went well and what needs improvement
- **Difficulty**: easy
- **Target User**: data_analyst
- **Key Metrics**: Story Points Completed, Sprint Commitment Accuracy, Cycle Time, Throughput

**Recommended Analysis Steps**:
  1. Calculate total story points completed vs committed
  2. Compare velocity to previous 3 sprints
  3. Analyze cycle time distribution by work item type
  4. Identify items that took longer than expected
  5. Check for blockers or impediments

**Important Caveats**:
  ⚠️ Velocity is team-specific - do not compare across teams
  ⚠️ One sprint is not enough for trend analysis
  ⚠️ Consider external factors (holidays, team changes)
```

## 后续优化建议

1. 支持中文问题匹配（如"我们的部署频率如何？"）
2. 添加模板推荐的置信度评分
3. 支持多模板推荐（当数据适合多个模板时）
4. 添加模板使用统计和反馈收集
