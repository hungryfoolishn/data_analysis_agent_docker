# Runtime V8 Evaluation & Learning Acceptance

## 1. Goal

在不改变 Skills 文件、不启用 GEPA/Skill Evolution 的前提下，先把 Runtime V7 的可信执行链转换为可持续学习信号：

```text
Task
→ Execution
→ Verification
→ Evidence
→ Finding / Report
→ TaskEvaluation
→ FailureCase
→ SkillQuality
→ SkillRetriever feedback
```

## 2. Implemented Contracts

### 2.1 Task Evaluation

位置：`langgraph_langchain/runtime/learning.py`

每个 Runtime Task 会生成一条 `TaskEvaluation`，包含：

- `run_id`、`task_id`、`execution_id`
- `task_type`、`executor_type`、`method`
- `skill_name`、`skill_fallback`
- `status`
- `verification_status`、`verification_count`、`verification_failure_count`
- `artifact_count`、`evidence_count`、`verified_evidence_count`
- `finding_count`、`report_generated`
- `passed`、`failure_kind`、`failure_reason`
- `duration_ms`、`created_at`、lineage metadata

失败类型包括：

| Kind | Meaning |
|---|---|
| `execution` | 工具/Worker 执行失败 |
| `verification` | Verification Policy 或质量检查失败 |
| `evidence` | Artifact 存在但未形成 verified Evidence |
| `finding` | `record_finding` 未产生 Runtime Finding |
| `report` | Report 校验或提交失败 |
| `cancelled` | 用户/系统取消 |

### 2.2 Failure Memory

`FailureCase` 通过确定性 fingerprint 聚合重复失败：

- 相同 task type / executor / method / skill / failure kind / normalized reason 会聚合同一 case。
- 记录 `occurrences`、`first_seen_at`、`last_seen_at`。
- 每类失败提供确定性 recovery recommendation。
- Runtime 快照与 `.analysis_learning.json` 均可恢复失败记忆。

### 2.3 Skill Quality

`SkillQuality` 按 skill / task type / executor type 聚合：

- attempts
- successes / failures
- verification / evidence / finding / report failures
- success rate
- quality score
- `neutral`、`reliable`、`needs_review` recommendation

当前阶段只评估和反馈 Skill 质量，不自动修改 Skill 文件。

### 2.4 SkillRetriever Feedback

`SkillRetriever` 可接收 `learning_memory`：

- `reliable` skill：lexical score 加 `+0.10`
- `needs_review` skill：lexical score 减 `-0.30`
- fallback / neutral skill：不做额外调整

这只会影响检索排序，不会绕过显式 `task.constraints["skill_name"]`。

---

## 3. Runtime Integration

`AnalysisRuntime` 新增：

```python
self.evaluations: list[TaskEvaluation]
self.learning_memory: LearningMemoryStore
```

并新增：

```python
record_task_evaluation(result)
learning_summary()
```

`record_execution_result()` 会在 Execution → Verification → Evidence → Finding 链路写入后自动评估任务。

Runtime snapshot 新增 `evaluations`，因此 Run 恢复后评估记录仍存在。

生产 Agent 中的 `SkillRetriever` 使用 Runtime-owned `learning_memory`。

---

## 4. P0 Acceptance

| ID | Requirement | Status |
|---|---|---|
| V8-P0-001 | Runtime evaluates every task result | PASS |
| V8-P0-002 | Evaluation includes Verification / Evidence / Finding lineage | PASS |
| V8-P0-003 | Failure cases are classified and persisted | PASS |
| V8-P0-004 | Skill quality aggregates attempts and outcomes | PASS |
| V8-P0-005 | SkillRetriever consumes learning feedback | PASS |
| V8-P0-006 | Learning snapshot survives Runtime restore | PASS |
| V8-P0-007 | Repeated task evaluation is idempotent | PASS |
| V8-P0-008 | No automatic Skill mutation / GEPA in V8 | PASS |

---

## 5. Tests

```text
tests/runtime_v2/test_runtime_v8_learning.py
```

覆盖：

1. Successful task evaluation。
2. Verification failure 进入 failure memory。
3. Runtime restore 后 evaluation / memory 可恢复。
4. Cross-run failure case aggregation。
5. Skill quality recommendation。
6. SkillRetriever 根据可靠/待复核 Skill 调整排序。
7. Evidence / Finding lineage count。
8. Repeated evaluation idempotency。

---

## 6. Explicit Non-Goals

Runtime V8 不做：

- Skill 文件自动改写
- GEPA prompt evolution
- vector database
- multi-agent orchestration
- parallel DAG replanning
- LLM-based quality judgment

这些留给 Runtime V9 / V10。
