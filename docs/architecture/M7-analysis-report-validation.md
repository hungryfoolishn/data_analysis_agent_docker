# B7 / M7: Evidence-backed Analysis Report

## 范围

B7 定义结构化分析报告、确定性报告组装与验证器。报告叙述可以后续由 LLM 组织，
但关键数字与结论必须绑定真实 Evidence。

## Report schema

`AnalysisReport` 包含：

- `report_id`
- `task_id`
- `user_goal`
- `status`: `success` / `partial_success` / `failed`
- `executive_summary`
- `sections`
- `findings`
- `artifact_ids`
- `limitations`
- `generated_at`

`ReportFinding` 包含：

- `finding_id`
- `claim`
- `supported_by`
- `referenced_asset_ids`
- `calculation_ref`
- `confidence`
- `limitation_notes`

## Validator

`validate_analysis_report()` 返回结构化 `errors` / `warnings` 和状态
`valid` / `warning` / `invalid`，不只返回布尔值。

校验：

- Evidence ID 存在且 verified；
- Finding 必须有 supporting evidence；
- 引用 asset 存在；
- 引用 artifact 存在；
- claim 中的数字必须出现在 evidence 的 numeric_values / evidence_text；
- 使用 truncated evidence 但未声明限制时产生 warning。

## Generator

`build_analysis_report()` 是确定性组装器，不会新增事实。
`render_analysis_report()` 输出稳定 Markdown。

部分成功报告必须使用 `status=partial_success`，不能包装成完整成功。
