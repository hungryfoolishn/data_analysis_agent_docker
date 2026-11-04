# B6 / M6: 只读 SQLite SQL Tool 与 Evidence

## 范围

B6 将 M2.5 的只读 SQL 执行、M2.6/M2.7 的 QueryResultAsset 与 SnapshotStore
串联起来，并生成可追溯 SQL Evidence。不实现任意数据库、写入操作或财务 SQL 模板。

## SQL Tool

新增：

```text
langgraph_langchain/tools/sqlite_sql_tool.py
```

`SQLiteQueryTool.execute()`：

1. 使用 `SQLiteDataSource.execute_bounded_query()` 执行 SQL。
2. 复用已有只读、单语句、CTE、参数绑定、超时、行数和结果大小限制。
3. 成功后调用 `AnalysisRuntime.register_sqlite_result()`。
4. 生成 QueryResultAsset 和 SnapshotStore 快照。
5. 返回 `SQLiteQueryToolResult`。

输出包含：

- `execution_id`
- `status`
- `output_asset_id`
- `row_count`
- `column_count`
- `truncated`
- `query_fingerprint`
- `query_type`
- `selected_columns`
- `duration_seconds`
- `requested_row_limit`

SQL 执行失败不会注册资产。资产注册失败会抛出 `SQLiteQueryToolError`，并且不留
伪成功结果。

## Evidence

新增：

```text
langgraph_langchain/evidence/sqlite.py
```

`SqliteQueryEvidence` 保存：

- `evidence_id`
- `evidence_type`
- `execution_id`
- `source_asset_id`
- `input_asset_ids`
- `output_asset_id`
- `operation`
- `query_fingerprint`
- `validation_status`
- `truncated`
- `provenance`
- `created_at`

Evidence 只能由真实 bounded query 和真实注册 asset 生成。验证状态为
`verified` 时必须存在 output content hash。

Evidence 不保存原始 SQL、原始绑定参数或数据库路径；SQL fingerprint 已包含
normalized SQL 与参数身份。
