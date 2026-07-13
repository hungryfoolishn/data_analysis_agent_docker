---
name: funnel-analysis
description: >
  用户行为漏斗分析：定义漏斗步骤、计算逐步转化率、定位流失环节、
  按维度拆分分析流失原因。适用于有用户ID+事件类型+时间的数据。
version: 1.0.0
metadata:
  tags: [漏斗, 转化率, 流失, funnel, conversion, drop-off]
  category: data-analysis
  trigger_keywords:
    - 转化
    - 漏斗
    - 流失
    - 每步转化
    - 转化率
    - 转化流程
    - funnel
    - conversion
    - drop-off
  data_patterns:
    has_user_id: true
    has_event_type: true
    has_timestamp: true
  related_skills:
    - cohort-analysis
    - attribution-analysis
---

# 漏斗分析

## 适用场景

当用户问题涉及"转化流程"、"每步转化率"、"哪个环节流失最多"时，使用此技能。

适用于包含以下列的数据：
- **用户标识列**：user_id / customer_id / uid 等
- **事件/状态列**：event_type / action / status / step 等
- **时间列**：timestamp / datetime / date 等

## 分析工作流

### Step 1: 确认漏斗步骤

```python
# 步骤目标: 理解数据中的步骤/事件分布，定义漏斗

# 方法:
# 1. 查看事件/状态列的唯一值
# 2. 按时间排序，确定步骤顺序
# 3. 定义漏斗步骤（业务逻辑排序，不能跳步）

print("步骤目标: 确认漏斗步骤")
print("方法: 查看事件列分布，定义步骤顺序")

# 示例：查看事件分布
print(df[event_col].value_counts())
```

### Step 2: 计算逐步转化率

```python
# 步骤目标: 计算每一步的用户数和转化率
# 方法: 按步骤筛选唯一用户，计算 step-to-step 和 overall 转化率

import pandas as pd

funnel_steps = ["浏览", "加购", "下单", "支付"]  # 替换为实际步骤
funnel_data = []
for i, step in enumerate(funnel_steps):
    users = set(df[df[event_col] == step][user_col].unique())
    funnel_data.append({"step": step, "users": len(users)})
    if i > 0:
        prev_users = funnel_data[i-1]["users"]
        rate = users / prev_users if prev_users else 0
        funnel_data[i]["step_rate"] = rate
        funnel_data[i]["overall_rate"] = len(users) / funnel_data[0]["users"]

funnel_df = pd.DataFrame(funnel_data)
print(funnel_df)

# 保存漏斗图
save_fig("funnel_chart.png")
```

**参考基准（电商行业）：**
- 整体转化率：2-5%
- 加购→下单：10-30%
- 下单→支付：60-80%

### Step 3: 定位流失环节

```python
# 步骤目标: 找出流失率最高的步骤，按维度拆分
# 方法: 识别最大流失点，按维度（设备/渠道/新老用户）拆分分析

# 找出流失最大的步骤
max_drop_idx = funnel_df["step_rate"].idxmin() if "step_rate" in funnel_df else 0
print(f"最大流失环节: {funnel_df.loc[max_drop_idx, 'step']}")

# 按维度拆分（替换为实际维度列）
for dim in available_dims:
    print(f"\n按 {dim} 拆分:")
    # 按维度分组统计每步用户数
    print("按维度拆分分析完成")

record_finding(...)
```

### Step 4: 生成建议

```python
# 步骤目标: 用 record_finding 记录关键发现
# 方法: 总结最大流失环节、维度差异、改进建议

print("关键结果: 漏斗分析完成")
print("建议下一步: 生成完整报告")
```

## 注意事项

- 漏斗步骤必须有时间先后顺序
- 同一用户在同一步骤只计算一次（去重）
- 大促/节假日期间数据需单独分析，不能与日常混合
- 如果用户路径非线性（可跳步），需调整计算逻辑
