# DeepAnalyze 语义上下文集成契约

## 1. 边界

DeepAnalyze 不读取 WrenAI 的本体目录，不导入 WrenAI 内部模型，也不负责把自然语言转换成受治理 SQL。上游语义层完成解析后，只向 DeepAnalyze 提交稳定的 `SemanticResolution`。

```text
WrenAI / 其他语义层
  解析问题、选择指标与维度、应用权限、生成受治理查询或结果
                              ↓ SemanticResolution v1
DeepAnalyze
  持久化语义版本、分析查询结果、执行统计方法、生成产物和报告
```

本地文件模式不要求语义上下文。没有 provider 时，DeepAnalyze 继续使用物理 Schema、数据画像和分析假设。

## 2. SemanticResolution v1

核心字段：

| 字段 | 责任 |
|---|---|
| `provider` | 语义上下文来源，例如 `wren`、`wren-mock` |
| `context_version` | 本次解析使用的本体/语义包版本 |
| `question` | 被解析的原始问题 |
| `query` | 上游生成的受治理查询或查询引用；DeepAnalyze 不直接执行该文本 |
| `metric_definitions` | 指标名称、单位、粒度、可用维度、默认过滤和执行引用 |
| `entities` | 实体名称、粒度、维度和物理来源引用 |
| `dimensions` | 维度语义、来源字段、数据类型和角色 |
| `relationships` | 实体关系、基数和谓词 |
| `source_assets` | 上游查询结果或数据资产的稳定引用，不包含数据本身 |
| `assumptions` / `constraints` | 本次分析必须保留的假设和限制 |
| `permissions` | 可持久化的策略版本、数据范围、禁用字段和行过滤 |

请求示例：

```json
{
  "model": "deepseek-chat",
  "messages": [{"role": "user", "content": "按部门分析完成率"}],
  "file_path": "/workspace/session/result.csv",
  "semantic_context": {
    "provider": "wren",
    "context_version": "business_analytics:1.0.0",
    "question": "按部门分析完成率",
    "metric_definitions": [{
      "id": "quality.q21_completion_rate",
      "name": "q21CompletionRate",
      "display_name": "Q2.1 完成率",
      "unit": "percent",
      "grain_entity": "quality.requirement_application_scorecard",
      "allowed_dimensions": ["quality.requirement_application_scorecard.development_department"],
      "execution_ref": {"cube": "q21Scorecard", "measure": "scored_pct"}
    }],
    "permissions": {
      "policy_version": "policy-3",
      "allowed_data_scopes": ["quality.requirement_application_scorecard"],
      "denied_fields": []
    }
  }
}
```

## 3. WrenAI 映射

当前 WrenAI 本体 v2 可以按以下方式映射，不要求 DeepAnalyze 理解其内部文件布局：

| WrenAI | DeepAnalyze |
|---|---|
| `package.id + package.version` | `context_version` |
| `metrics[]` | `metric_definitions[]` |
| `entities[]` | `entities[]` |
| `entities[].attributes[]` | 扁平化后的 `dimensions[]` |
| `relationships[]` | `relationships[]` |
| `governance` 的执行结果 | `permissions` 与 `constraints` |
| 查询结果资产 | `source_assets[]` 和分析输入文件/表 |

WrenAI 适配器应位于 WrenAI 一侧或独立集成层。它可以通过 HTTP、SDK 或消息传递产生同一个 `SemanticResolution`；DeepAnalyze Agent 不为某种传输方式增加分支。

## 4. 安全和审计

- `SemanticResolution` 禁止 API key、token、Authorization、密码和 secret 字段。
- 权限只保存策略事实，不保存认证材料。
- 注入 Agent prompt 前只选择分析所需字段，限制数量和长度，并屏蔽指令注入文本。
- `query` 被视为上游证据，不由 Agent 直接执行。
- `.analysis_runtime.json` 保存完整契约版本，SSE 只发送 provider 和 context version。
- 相同问题使用不同 `context_version` 时必须创建新的 run，不能恢复旧运行。
- `semantic_context.question` 必须与当前用户问题一致；不一致时 API 拒绝执行并要求上游重新解析。

## 5. 当前实现与后续接入

当前提供：

- `MockSemanticContextProvider`：测试和联调使用。
- `LocalFileSemanticContextProvider`：读取配置好的 JSON/YAML resolution。
- `/v1/chat/completions` 的可选 `semantic_context` 字段。
- runtime 持久化、恢复、prompt 投影和 SSE 版本标识。

真实 WrenAI 联调当前暂缓。未来恢复实施时，只需实现 `SemanticContextProvider.resolve_question()` 或在 WrenAI API 层直接输出本契约，并将受治理查询结果作为 DeepAnalyze 的输入资产提交；近期顺序以 `deepanalyze-general-platform-roadmap-v2.md` 为准。
