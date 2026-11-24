# 08 — 遗留风险与 R0a-1B 交接（Remaining Risks & Handoff）

## 遗留风险

### 已知限制（本阶段接受，有文档与测试）

| ID | 限制 | 影响 | 收敛条件 |
|---|---|---|---|
| K-01 | `WORKER_BACKEND` 未配置时，产品不可信代码执行被拒绝（python_repl/delegate/V2 python 任务均返回可区分错误） | 默认配置下分析功能不可用，需操作者显式配置 | R0a-1B：生产部署固定 `WORKER_BACKEND=docker` |
| K-02 | `WORKER_BACKEND=process` + `ALLOW_UNTRUSTED_PROCESS_EXECUTION=1` 时，不可信代码在受限子进程（非容器）执行 | 非沙箱：进程可访问 workspace 外读、无网络隔离 | R0a-1B 收敛后生产移除 opt-in |
| K-03 | Docker 后端被选择时，python_repl/delegate 的真实容器端到端行为未验证 | 不声称 Docker 主链路已完成 | R0a-1B 全链路接入与 E2E |
| K-04 | 测试环境 conftest 显式使用 process+opt-in | 仅测试环境 | 随 R0a-1B 收敛 |

### P1/P2（范围外，已记录）

| ID | 风险 | 归属 |
|---|---|---|
| P-01 | Session ID 无格式校验、无 owner 归属（路径穿越/跨会话访问） | R0b（Blueprint V1.1 §6 R0b） |
| P-02 | Evidence legacy 默认 verified 旁路（`evidence/operations.py:27`、`evidence/sqlite.py:34`） | R0a-1C |
| P-03 | SQL 工具边界（注入/越权，Bounded Reads） | R0b / 后续安全任务 |
| P-04 | Acquisition 网络策略 / SSRF | R4 |
| P-05 | `_DANGEROUS_PATTERNS` 黑名单可被绕过（黑名单非边界，纵深防御） | 已由执行边界替代其安全职责；保留作提示 |

## R0a-1B 交接清单

1. Docker Worker Backend 真实产品主链路接入：生产 compose / .env 固定
   `WORKER_BACKEND=docker`，移除 `ALLOW_UNTRUSTED_PROCESS_EXECUTION`。
2. 验证 docker 后端下 python_repl / delegate / V2 python 任务端到端
   （namespace 更新、df 传递、save_fig 工件、超时、取消、异常退出、清理）。
3. 容器资源限制与安全策略验证（network none、cap-drop、read-only rootfs
   的组合生效证据）。
4. Worker 执行结果与 Evidence 衔接（Evidence collector 输入）。
5. 收敛 K-01/K-02：opt-in 保留仅限开发文档说明，生产不可用。
6. 回归 `tests/test_execution_policy.py` 与
   `tests/test_execution_entry_hardening.py`（本阶段建立的安全门禁）。

## 回滚方案

本工作包为独立分支/提交。回滚 = `git revert` 本提交（或分支级丢弃）。
回滚后恢复到 `4a9b254` 行为：delegate_analysis 恢复宿主进程内联执行
（即恢复 P0-2 风险），其余入口回到硬编码 process 后端。无数据迁移，
无 schema 变更，无配置文件破坏性变更（本阶段未改动 compose/.env）。
