---
name: rfm-segmentation
description: >
  RFM 用户分群分析：基于最近一次消费(Recency)、消费频率(Frequency)、
  消费金额(Monetary)对用户进行分层。适用于有用户ID+时间+金额的数据。
version: 1.0.0
metadata:
  tags: [RFM, 分群, 分层, 用户价值, segmentation, customer-value]
  category: data-analysis
  trigger_keywords:
    - 分群
    - 分层
    - RFM
    - 客户价值
    - 用户分层
    - segmentation
    - 高价值用户
    - 用户画像
  data_patterns:
    has_user_id: true
    has_timestamp: true
    has_amount: true
  related_skills:
    - cohort-analysis
    - attribution-analysis
---

# RFM 用户分群

## 适用场景

当用户问题涉及"用户分群"、"客户价值分层"、"RFM分析"、"高价值用户"时使用。

适用于包含：
- **用户标识列**：user_id / customer_id 等
- **时间列**：order_date / transaction_time 等
- **金额列**：amount / revenue / price 等

## 分析工作流

### Step 1: 计算 RFM 指标

```python
# 步骤目标: 计算每个用户的 R/F/M 值
# 方法: 按用户聚合，计算最近购买时间、购买次数、总金额

import pandas as pd
from datetime import datetime

print("步骤目标: 计算 RFM 指标")
print("方法: 按用户聚合计算 R/F/M")

# 确定分析截止日期
snapshot_date = pd.Timestamp(df[time_col].max()) + pd.Timedelta(days=1)

rfm = df.groupby(user_col).agg(
    Recency=lambda x: (snapshot_date - pd.to_datetime(x).max()).days,
    Frequency=(amount_col, "count"),
    Monetary=(amount_col, "sum"),
).rename(columns={amount_col: "Monetary"})

print(rfm.describe())
```

### Step 2: RFM 评分

```python
# 步骤目标: 将 R/F/M 转化为 1-5 分评分
# 方法: 按分位数评分，R 越小越好，F/M 越大越好

# Recency: 越小越好（反向）
rfm["R_score"] = pd.qcut(rfm["Recency"], 5, labels=[5, 4, 3, 2, 1]).astype(int)
# Frequency: 越大越好（正向）
rfm["F_score"] = pd.qcut(rfm["Frequency"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5]).astype(int)
# Monetary: 越大越好（正向）
rfm["M_score"] = pd.qcut(rfm["Monetary"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5]).astype(int)

rfm["RFM_score"] = rfm["R_score"] + rfm["F_score"] + rfm["M_score"]
print(rfm[["R_score", "F_score", "M_score", "RFM_score"]].describe())
```

### Step 3: 用户分层

```python
# 步骤目标: 根据 RFM 评分划分用户层级
# 方法: 按总分分层，命名每个层级

def rfm_label(row):
    if row["RFM_score"] >= 13:
        return " champions"  # 最优客户
    elif row["RFM_score"] >= 10:
        return "loyal"       # 忠诚客户
    elif row["RFM_score"] >= 7:
        return "potential"   # 潜力客户
    elif row["RFM_score"] >= 4:
        return "at_risk"     # 流失风险
    else:
        return "lost"        # 流失客户

rfm["segment"] = rfm.apply(rfm_label, axis=1)
print(rfm["segment"].value_counts())

save_fig("rfm_segments.png")
```

### Step 4: 分析各层级特征

```python
# 步骤目标: 对比各层级的消费行为差异
# 方法: 按层级汇总统计，记录关键发现

segment_summary = rfm.groupby("segment").agg(
    user_count=("segment", "size"),
    avg_recency=("Recency", "mean"),
    avg_frequency=("Frequency", "mean"),
    avg_monetary=("Monetary", "mean"),
).round(1)
print(segment_summary)

record_finding(...)
```

## 注意事项

- RFM 评分的分位数划分可能因数据分布不均导致某些分组用户数差异大
- Frequency 和 Monetary 常有长尾分布，考虑使用 rank(method="first") 处理
- 分层阈值需要根据业务调整，不能机械套用
- 分析时间窗口的选择影响结果：太短会低估 F/M，太长会掩盖趋势变化
