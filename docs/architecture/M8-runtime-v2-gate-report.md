# B8 / M8: Runtime V2 Golden E2E Gate Report

## 基线

- 分支：`codex/phase1-m2.4-sqlite-schema`
- B3 基线：`f692feb`
- B8 新增：文件 + SQLite Golden E2E

## Gate 结果摘要

| Gate | 结果 | 说明 |
| --- | --- | --- |
| 文件 Golden：CSV / Parquet / Excel | PASS | 文件资产统一 Resolver 注入 Python executor |
| SQLite Golden：SQL Tool -> Asset -> Snapshot -> Resolver -> Executor | PASS | 删除源 SQLite 后仍能执行 |
| SQLite Golden：参数化 CTE / 聚合 / JOIN | PASS | 只读 SQL Tool 成功注册结果资产 |
| SQLite Golden：truncation | PASS | Evidence 保留 truncated，报告产生 warning |
| SQL 安全 | PASS | DML / 多语句拒绝，无资产 / Evidence 生成 |
| Evidence / Report | PASS | 成功报告必须 verified evidence；失败报告不能伪造 evidence |
| Runtime 重启 | PASS | B2 已覆盖；B8 SQLite 链路继续依赖 SnapshotStore |
| Default Runtime switch | HOLD | 产品默认仍为 `RUNTIME_V2_ENABLED=false` |
| Fallback gate | PASS | `RUNTIME_V2_FALLBACK_ENABLED=false` 且 strict mode 默认开启 |

## 覆盖的失败路径

- 文件缺失 / 修改 / 不支持格式（B3 tests）。
- SQLite 快照缺失、hash mismatch、无效 reference、metadata 损坏（B2 tests）。
- SQL DML / 多语句拒绝（B6 tests）。
- SQL 成功但 asset registration 失败时不留伪资产（B6 tests）。
- 报告缺少 Evidence / 引用未知 Evidence（B7/B8 tests）。
- 截断数据未声明限制时报告 warning（B7/B8 tests）。

## 切换决策

**允许 CI / 测试 / 开发环境显式启用 Runtime V2。**

```bash
RUNTIME_V2_ENABLED=true
```

**暂不开启产品默认 Runtime V2。**

原因：

1. 当前 Golden E2E 是通用最小集合，尚未覆盖生产规模数据、长时间任务、并发、
   UI/API 全路径和真实故障注入。
2. Legacy V1 仍需要保留为显式兼容路径。
3. 默认切换还需要对照运行和错误率观测。

保持：

```bash
RUNTIME_V2_ENABLED=false
RUNTIME_V2_FALLBACK_ENABLED=false
RUNTIME_V2_STRICT_MODE=true
```

## 后续进入产品默认的条件

1. 生产代表性文件 Golden 全部通过。
2. 生产代表性 SQLite Golden 全部通过。
3. API / UI / job runner 路由一致性测试通过。
4. 对照运行记录 V1/V2 错误率和执行成功率。
5. 显式批准 fallback 策略和观测告警。
6. Runtime V2 文档、配置和实际行为一致。
