# M3: 文件资产统一输入契约

## 范围

B3 / M3 将 CSV、Excel 和 Parquet 文件资产接入与 SQLite snapshot 相同的 Runtime
输入解析接口。不引入金融专属语义、Planner 或默认 Runtime 切换。

## 文件资产

文件资产继续使用 `DataAsset.from_dataframe()` 创建：

- `source_type`: `csv`、`xlsx`、`xls`、`parquet`
- `location`: 真实注册文件路径
- `content_hash`: 注册时的文件 SHA-256
- `sheet_name`: Excel sheet 名
- `schema_snapshot`: schema 元数据

不把 SQLite 查询结果伪装成文件资产。

## AssetResolver 分支

`AssetResolver.resolve()` 现在区分：

- `source_type in {"csv", "xlsx", "xls", "parquet"}`：使用 `FileDataSource`
- `source_type == "sqlite"`：使用 SnapshotStore
- 其他类型：`AssetUnsupportedError`

文件加载前校验：

- 注册位置存在。
- 当前文件 SHA-256 等于注册时 `content_hash`。
- 文件被修改、删除或损坏时显式失败。
- 不支持格式显式失败。

`ResolvedAsset` 现在保留：

- `source_location`
- `sheet_name`
- `source_type`
- `selected_columns`
- `row_count`
- `truncated`
- `snapshot_reference`

SQLite asset 的 `source_location` 保持 `None`。

## Runtime 输入

文件资产和 SQLite snapshot asset 都通过相同 namespace 注入：

```python
runtime_inputs[asset_id] = dataframe
runtime_input_metadata[asset_id] = provenance
```

`asset_id` 是唯一 key，避免同名覆盖。

## 错误契约

- 文件删除：`AssetResolutionError`
- 文件修改：`AssetResolutionError`
- 读取失败：`AssetResolutionError`
- 不支持格式：`AssetUnsupportedError`

错误信息不暴露具体文件系统诊断路径之外的数据库连接信息。
