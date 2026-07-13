---
name: cohort-analysis
description: >
  同期群分析：按用户首次出现时间分组，追踪各群组的留存率、回访率、
  生命周期价值变化。适用于有用户ID+时间的数据。
version: 1.0.0
metadata:
  tags: [同期群, 留存, 回访, cohort, retention, LTV]
  category: data-analysis
  trigger_keywords:
    - 留存
    - 回访
    - 同期群
    - cohort
    - retention
    - 用户生命周期
    - LTV
    - 复购
  data_patterns:
    has_user_id: true
    has_timestamp: true
  related_skills:
    - funnel-analysis
    - rfm-segmentation
---

# 同期群分析

## 适用场景

当用户问题涉及"用户留存"、"回访率"、"同期群"、"不同时期用户的留存差异"时使用。

适用于包含：
- **用户标识列**：user_id / customer_id 等
- **时间列**：created_at / order_date / visit_time 等

## 分析工作流

### Step 1: 确定同期群分组

```python
# 步骤目标: 按用户首次出现时间分组（月/周）
# 方法: 找到每个用户的首次活跃时间，按月/周归组

print("步骤目标: 确定同期群分组")
print("方法: 计算每个用户的首次活跃时间")

# 找到每个用户的首次出现时间
df[time_col] = pd.to_datetime(df[time_col])
first_active = df.groupby(user_col)[time_col].min().reset_index()
first_active.columns = [user_col, "first_active"]
first_active["cohort"] = first_active["first_active"].dt.to_period("M")

# 合并回主表
df = df.merge(first_active, on=user_col)
print(f"同期群数量: {df['cohort'].nunique()}")
print(df.groupby("cohort")[user_col].nunique())
```

### Step 2: 计算留存率矩阵

```python
# 步骤目标: 计算每个群组在第 N 期的留存率
# 方法: 按群组×活跃周期构建矩阵

# 计算每个用户在首次活跃后的第几个月还活跃
df["period"] = df[time_col].dt.to_period("M")
df["cohort_age"] = (df["period"] - df["cohort"]).astype(int)

# 构建留存矩阵
cohort_matrix = df.groupby(["cohort", "cohort_age"])[user_col].nunique().unstack()
cohort_size = cohort_matrix.iloc[:, 0]
retention = cohort_matrix.div(cohort_size, axis=0)
print(retention.round(3))

save_fig("cohort_retention.png")
```

### Step 3: 分析留存趋势

```python
# 步骤目标: 识别留存趋势和异常群组
# 方法: 对比不同群组的留存曲线，找出变化点

# 对比前5个群组的留存曲线
print("关键结果: 留存矩阵计算完成")

# 识别留存异常（突然下降或上升的群组）
record_finding(...)
```

### Step 4: 输出建议

```python
# 步骤目标: 给出留存改进建议
# 方法: 根据留存数据判断用户健康度

print("建议下一步: 生成完整报告")
```

## 注意事项

- 同期群粒度选择：数据量小时用周，数据量大时用月
- 新群组的留存率不稳定（样本少），避免过早下结论
- 留存定义需要明确：是"有行为"还是"有交易"
- 注意区分"新用户留存"和"活跃用户留存"
