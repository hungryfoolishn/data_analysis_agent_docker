# LangGraph 解释性分析改造建议

## 目标

把系统从“自动发现现象并写报告”，升级为“先做结构化解释与证据分级，再生成可复核、可行动、但不过度因果化的分析报告”。

---

## 一、`langgraph_agent.py` 建议新增的 helper

### 1. `decompose_metric_change()`
用途：先回答指标变化了多少、是谁贡献的。

建议签名：

```python
def decompose_metric_change(
    df: pd.DataFrame,
    metric: str,
    time_col: str,
    dims: list[str],
    compare: str = "mom",
    top_k: int = 5,
) -> dict:
    ...
```

建议输出：

```python
{
    "metric": "revenue",
    "current_period": "2025-08",
    "previous_period": "2025-07",
    "absolute_change": -12500.0,
    "relative_change": -0.12,
    "top_positive_contributors": [
        {"dimension": "region", "group": "North", "contribution": 3200.0}
    ],
    "top_negative_contributors": [
        {"dimension": "channel", "group": "Paid Search", "contribution": -9800.0}
    ],
    "broad_based": False,
    "coverage_ratio": 0.81,
}
```

### 2. `rank_driver_candidates()`
用途：对候选驱动因素排序，而不是让模型拍脑袋。

建议签名：

```python
def rank_driver_candidates(
    df: pd.DataFrame,
    metric: str,
    time_col: str,
    dims: list[str],
    compare: str = "mom",
) -> list[dict]:
    ...
```

输出示例：

```python
[
    {
        "driver": "channel mix shift",
        "dimension": "channel",
        "evidence_level": "B",
        "score": 0.82,
        "reason": "top negative groups explain 68% of decline across two adjacent periods"
    },
    {
        "driver": "order volume decline in East",
        "dimension": "region",
        "evidence_level": "B",
        "score": 0.64,
        "reason": "consistent in MoM and rolling 3-period view"
    }
]
```

建议打分维度：
- 解释力
- 多窗口一致性
- 覆盖度
- 时间先后关系
- 数据质量可信度

### 3. `assess_evidence_level()`
用途：统一给结论打证据等级。

建议签名：

```python
def assess_evidence_level(
    *,
    has_quantitative_support: bool,
    has_group_breakdown: bool,
    has_time_window: bool,
    cross_slice_consistent: bool,
    relies_on_unobserved_assumption: bool,
) -> str:
    ...
```

建议规则：
- `A`：直接事实 + 数字证据 + 时间/分组清晰
- `B`：多切片一致的相关线索，但仍非因果
- `C`：合理假设，待验证

### 4. `generate_recommendation_candidates()`
用途：把建议先结构化，再决定是行动建议还是验证建议。

建议签名：

```python
def generate_recommendation_candidates(
    findings: list[dict],
    drivers: list[dict],
) -> list[dict]:
    ...
```

输出示例：

```python
[
    {
        "type": "action",
        "priority": "high",
        "recommendation": "review Paid Search budget allocation in East region",
        "based_on": ["finding_2", "driver_1"],
        "evidence_level": "B"
    },
    {
        "type": "validation",
        "priority": "medium",
        "recommendation": "check whether August decline is driven by campaign timing or sample-size noise",
        "based_on": ["finding_3"],
        "evidence_level": "C"
    }
]
```

规则建议：
- `A/B` 才允许 `action`
- `C` 只能 `validation` / `observe`

### 5. `check_metric_definition_risk()`
用途：在解释前先判断口径风险。

建议签名：

```python
def check_metric_definition_risk(
    df: pd.DataFrame,
    metric: str,
    time_col: str | None,
) -> dict:
    ...
```

建议输出：
- 是否存在退款/取消/补录相关字段
- 时间粒度是否混杂
- 是否可能重复记录
- 当前解释是否应降级为 exploratory

### 6. `run_counterfactual_checks()`
用途：补最轻量的“准因果意识”。

建议签名：

```python
def run_counterfactual_checks(
    df: pd.DataFrame,
    metric: str,
    key_dimension: str,
    time_col: str | None = None,
) -> list[dict]:
    ...
```

做法建议：
- 去掉头部 group 后，结论是否还成立
- 分新老用户/主要次要区域后，方向是否一致
- 换窗口后趋势是否还成立

---

## 二、主链路接入建议

建议在现有分析链路中增加一个“解释阶段”，固定执行：

1. `decompose_metric_change`
2. `rank_driver_candidates`
3. `assess_evidence_level`
4. `generate_recommendation_candidates`
5. `run_counterfactual_checks`

并将结果写入 session state，例如：

```python
session.ns["explanation_bundle"] = {
    "metric_decomposition": ...,
    "driver_ranking": ...,
    "counterfactual_checks": ...,
    "recommendations": ...,
}
```

这样最终报告不是凭语言能力写出来，而是引用结构化结果生成。

---

## 三、`finish_report` 建议增加的校验

### 1. 驱动类表述必须带证据等级
如果报告出现：
- 原因
- 驱动
- 导致
- 根因
- because
- driver
- caused by
- due to

则必须同时满足至少一项：
- 出现 evidence level / 证据等级
- 出现 contribution / contributor / 贡献拆解
- 出现 hypothesis / 需验证 / 可能 / 疑似 等边界词

否则拒绝。

### 2. 行动建议必须绑定证据
如果 Recommendations 里出现强动作词：
- 立即调整
- should
- must
- prioritize
- increase
- cut
- stop
- launch

则必须有：
- 数字证据
- 时间窗口
- 分组对象或影响对象
- 对应 findings 引用

否则拒绝。

### 3. 解释性结论必须包含贡献对象
如果说“主要由 X 驱动”，必须能看到：
- 哪个维度
- 哪个 group
- 贡献方向
- 至少部分量化

不允许只写抽象表述，例如：
- the decline was mainly driven by regional factors

### 4. 相关与因果必须分层表达
如果报告出现因果味很重的词：
- 根因
- direct cause
- led to
- caused by
- 明确由…导致

则必须同时出现更强约束之一：
- 对照 / 分层 / 多窗口一致
- 明示“接近因果但未完全证实”
- 或降级成 hypothesis

否则拒绝。

### 5. 解释结论必须经过稳定性 / 反事实提醒
如果 driver 结论成立，但没有提到以下任一内容，则建议拒绝：
- 去掉头部组后是否仍成立
- 换窗口是否仍成立
- 分层后是否仍成立

### 6. 业务口径不清时必须降级措辞
如果报告中出现很强的解释性结论，但没有：
- metric definition note
- data quality / business definition caveat
- uncertainty boundary

则拒绝。

### 建议新增 rejection type
- `missing_driver_evidence_level`
- `recommendation_without_support`
- `causal_claim_without_boundary`
- `driver_claim_without_contribution_breakdown`
- `missing_definition_risk_note`
- `missing_stability_check_for_explanation`

---

## 四、`test_reliability.py` 建议补充的测试

### A. helper 层测试

#### 1. `decompose_metric_change` 基础测试
验证：
- 正确识别 period change
- 输出 top positive / negative contributors
- coverage ratio 合理

#### 2. `rank_driver_candidates` 排序测试
构造数据让某个 channel 明显贡献最大，确认它排第一。

#### 3. `assess_evidence_level` 分级测试
覆盖：
- A：数字 + 时间 + 分组齐全
- B：有趋势和一致性，但无强因果
- C：仅假设

#### 4. `generate_recommendation_candidates` 降级测试
验证：
- `C` 级证据不能产出 action recommendation
- 只能产出 validation / observe

#### 5. `run_counterfactual_checks` 稳定性测试
验证：
- 排除 top group 后结论变化
- 换窗口后结论失稳时输出 unstable / partial

### B. `finish_report` 门控测试

#### 1. `test_rejects_driver_claim_without_evidence_level`
报告里写“主驱动是 X”，但没证据等级、没边界词，应该拒绝。

#### 2. `test_rejects_action_recommendation_without_supporting_numbers`
建议很强，但全文无具体数字支撑，应该拒绝。

#### 3. `test_rejects_causal_claim_without_uncertainty_or_controls`
写“由某因素直接导致”，但没对照 / 分层 / 不确定性说明，应该拒绝。

#### 4. `test_rejects_driver_statement_without_group_contribution`
写“由渠道变化驱动”，但没说哪个渠道、贡献多少，应该拒绝。

#### 5. `test_rejects_explanatory_report_without_definition_caveat_when_metric_is_ambiguous`
解释性结论很强，但缺少口径风险提醒时，应该拒绝。

#### 6. `test_accepts_explanatory_report_with_evidence_level_and_validation_recommendation`
有证据等级、有边界、有验证型建议时，应该通过。

### C. 分析链路 / 提示产物测试

#### 1. `test_analysis_hints_require_contribution_breakdown_for_metric_change`
当检测到明显总指标变化时，系统 hints 应要求做贡献拆解。

#### 2. `test_analysis_hints_require_non_causal_language_for_driver_claims`
当出现 driver 类任务时，系统 hints 应明确“相关不等于因果”。

#### 3. `test_analysis_hints_require_validation_recommendation_when_evidence_is_weak`
弱证据场景下，系统应引导生成 validation recommendation，而不是 action recommendation。

---

## 五、建议实施顺序

### 第一步
先补 helper：
- `decompose_metric_change`
- `rank_driver_candidates`
- `assess_evidence_level`

### 第二步
把 helper 结果接入 session state 和分析提示。

### 第三步
升级 `finish_report`，让解释性结论必须引用这些结构化结果。

### 第四步
补 deterministic tests，先保证规则稳定。

### 第五步
再考虑更进阶的：
- cohort
- before/after
- simple regression
- 分层控制分析

---

## 六、最小可落地版本

如果要先小步快跑，第一版最建议只做这 3 件事：

1. `decompose_metric_change`
2. `assess_evidence_level`
3. `finish_report` 增加：
   - driver claim 必须有证据等级
   - action recommendation 必须有数字支撑
   - causal claim 必须有边界词或验证说明

这版改完后，系统的解释性分析可信度会明显上一个台阶，而且实现成本相对可控。
