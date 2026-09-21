# Runtime V7 Trusted Analysis Acceptance

## 1. Goal

建立从 Execution 到 Report 的可信分析闭环：

```text
Execution
→ Verification
→ Evidence
→ Finding
→ Report
```

Runtime V7 不新增控制面基础设施，而是固化质量门与证据溯源。

---

## 2. Implemented Contracts

### 2.1 Verification Policy

位置：`langgraph_langchain/runtime/verification_policy.py`

- 按 `task_type` 选择确定性检查。
- 支持：
  - `numeric_consistency`
  - `aggregation_consistency`
  - `time_consistency`
  - `group_consistency`
  - `schema_consistency`
  - `artifact_existence`
- 显式契约来自：

```python
task.constraints["verification_contract"]
```

- `TaskRunner` 将验证失败任务标记为 `failed`。
- Verification 失败时不会生成 Evidence，也不会进入 Finding。

### 2.2 Evidence Contract

位置：`langgraph_langchain/evidence/collector.py`

Evidence 必须满足：

- Execution 为 `succeeded`。
- 至少一条相关 `VerificationResult` 通过。
- 没有相关 Verification 失败。
- 引用的 Artifact 必须存在且可定位。
- Evidence 记录 `verification_result_id`、`source_execution_ids`、`source_step_ids` 和 `source_asset_ids`。

### 2.3 Finding Provenance

位置：`langgraph_langchain/runtime/finding_builder.py`

Finding 只能由 Runtime 已记录的 verified Evidence 支持：

```text
Finding
→ Evidence
→ VerificationResult
→ ExecutionResult
→ AnalysisTask
→ DataAsset
```

强校验入口：

```python
AnalysisRuntime.record_verified_finding(finding)
```

`record_finding()` 保留为旧快照/重放兼容入口；生产工具链使用强校验入口。

### 2.4 Report Integrity

位置：`langgraph_langchain/runtime/report_validator.py`

`finish_report` 会对 Key Findings 区块执行：

- Finding 必须有 `supported_by`。
- Evidence 必须 `verified`。
- Evidence 必须有 `verification_result_id`。
- 报告中的数字必须来自 verified Evidence。
- 符号化指标允许叙述性等价：Evidence 中 `-10500` 可支持报告中的 `declined by 10500`。

---

## 3. P0 Acceptance

| ID | Requirement | Status |
|---|---|---|
| V7-P0-001 | Runtime execution | PASS |
| V7-P0-002 | Executor routing | PASS |
| V7-P0-003 | Verification | PASS |
| V7-P0-004 | Verification gate | PASS |
| V7-P0-005 | Evidence | PASS |
| V7-P0-006 | Evidence status | PASS |
| V7-P0-007 | Provenance | PASS |
| V7-P0-008 | Report only consumes trusted findings | PASS |
| V7-P0-009 | Numeric integrity | PASS |
| V7-P0-010 | Failure handling | PASS |
| V7-P0-011 | Trusted-analysis E2E | PASS |
| V7-P0-012 | Persistence | PASS |

Runtime V7 专项测试：

```text
tests/runtime_v2/test_runtime_v7_trusted_analysis.py
```

核心覆盖：

1. Numeric verification failure blocks Task。
2. Verification failure 阻断 Evidence 与 Finding。
3. Evidence generation failure blocks Task。
4. FindingBuilder 拒绝无 Evidence 或 unverified Evidence。
5. Finding → Evidence → Verification → Execution → Task → Asset 可回溯。
6. Runtime 状态恢复后 lineage 仍完整。
7. Report Validator 拒绝未验证数字和 unverified Evidence。

Runtime V6 E2E 继续覆盖完整销售分析链路，并验证：

- Task 状态
- Execution
- Artifact
- Verification
- Evidence
- Finding
- Report
- persistence

---

## 4. Failure Matrix

| ID | Scenario | Expected | Result |
|---|---|---|---|
| F-001 | Verification failure | Task FAILED；Evidence/Finding NOT CREATED | PASS |
| F-002 | Evidence generation failure | Task FAILED；Finding NOT CREATED | PASS |
| F-003 | Finding without verified evidence | Runtime REJECT | PASS |
| F-004 | Unsupported report number | Report REJECTED | PASS |

---

## 5. Definition of Done

- P0 全部 PASS。
- 不因 Verification/Evidence/Finding 失败伪造报告。
- 报告中的定量结论可追溯到 verified Evidence。
- Runtime 状态快照可恢复完整 lineage。

## 6. Current Result

```text
Runtime V7 Trusted Analysis = READY
```
