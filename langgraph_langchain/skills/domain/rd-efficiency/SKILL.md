---
name: rd-efficiency-analysis
description: >
  R&D 团队效率分析：Sprint 回顾、速率趋势、代码质量趋势、缺陷根因分析。
  适用于 Sprint/PR/部署数据，包含完整的指标体系、计算方法和行业基准。
version: 1.0.0
metadata:
  tags: [R&D, 效率, Sprint, 速率, 缺陷, 代码质量, velocity, delivery]
  category: domain
  trigger_keywords:
    - R&D
    - 效率
    - Sprint
    - 速率
    - 交付
    - 缺陷
    - 代码质量
    - velocity
    - cycle time
    - deployment
    - 研发
  data_patterns:
    has_timestamp: true
  related_skills:
    - trend-forecast
    - anomaly-diagnosis
---

# R&D 团队效率分析

## 适用场景

分析 R&D 团队的交付效率、质量趋势和协作瓶颈。适用于包含 Sprint、PR、部署、缺陷等数据的场景。

## 核心指标体系

### 速率指标 (Velocity)
| 指标 | 定义 | 推荐聚合 | 行业参考 |
|------|------|---------|---------|
| Sprint Velocity | Sprint 完成的 Story Points | 中位数 | 每Sprint 20-40 SP/团队 |
| Throughput | 完成的任务/需求数量 | 求和 | 因团队而异 |
| Code Churn | 新增/修改/删除代码行比 | 比率 | < 25% 为健康 |

### 质量指标 (Quality)
| 指标 | 定义 | 推荐聚合 | 行业参考 |
|------|------|---------|---------|
| Defect Rate | 缺陷数/总需求数 | 比率 | < 15% |
| Escaped Defect Rate | 上线后发现的缺陷/总缺陷 | 比率 | < 20% |
| Test Coverage | 测试覆盖代码行比例 | 比率 | > 80% |

### 交付指标 (Delivery)
| 指标 | 定义 | 推荐聚合 | 行业参考 |
|------|------|---------|---------|
| Cycle Time | 从开始到完成的时间 | 中位数 | 1-5 天 |
| Lead Time | 从提出到上线的时间 | 中位数 | 5-15 天 |
| Deployment Frequency | 单位时间内部署次数 | 求和 | > 1次/周 |

## 分析工作流

### Step 1: 数据质量评估

```python
print("步骤目标: 评估 R&D 数据质量")
print("方法: 检查关键列、缺失值、异常值")

# 检查关键列是否存在
required_cols = {"sprint", "status", "story_points"}
missing = required_cols - set(df.columns.str.lower())
if missing:
    print(f"缺失关键列: {missing}")

# 缺失值检查
print(df.isnull().sum()[df.isnull().sum() > 0])
```

### Step 2: 速率趋势分析

```python
print("步骤目标: 分析 Sprint 速率趋势")
print("方法: 按 Sprint 统计完成量，计算趋势")

# Sprint 速率统计
velocity = df[df["status"] == "Done"].groupby("sprint")["story_points"].sum()
print(velocity)

# 计算移动平均
velocity_ma = velocity.rolling(3, min_periods=1).median()
print(f"速率中位数: {velocity.median():.1f}")

save_fig("velocity_trend.png")
```

### Step 3: 质量与交付分析

```python
print("步骤目标: 分析质量指标和交付效率")
print("方法: 计算缺陷率、周期时间等指标")

# 按Sprint计算缺陷率
# 按维度（团队/模块）拆分分析
record_finding(...)
```

### Step 4: 综合评估与建议

```python
print("关键结果: R&D 效率分析完成")
print("建议下一步: 生成完整报告")

record_finding(...)
```

## 注意事项

- **Velocity 不要跨团队比较**：不同团队的估算标准不同
- **Cycle Time 用中位数而非均值**：长尾分布会拉高均值
- **缺陷率需区分严重程度**：P0/P1 缺陷比总缺陷数更有意义
- **部署频率需结合变更失败率一起看**：频繁部署但高失败率是反模式
