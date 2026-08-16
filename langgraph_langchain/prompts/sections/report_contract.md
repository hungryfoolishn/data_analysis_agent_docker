## Final report contract

Before calling `finish_report`, perform this checklist against the markdown. Do not submit until every applicable item is present.

Use these exact top-level sections:

1. `## Summary` or `## 摘要`
2. `## Data Context` or `## 数据说明`
3. `## Key Findings` or `## 关键发现`
4. `## Data Quality` or `## 数据质量`
5. `## Analysis` or `## 分析`
6. `## Visualizations` or `## 图表说明` when any chart was created
7. `## Recommendations` or `## 建议` when recommendations are relevant

The Data Context section must explicitly document:

- Time range. When the dataset has no datetime column, write that no time field is available, the time range is not applicable, and the report is a static cross-sectional analysis.
- Definitions and calculation scope for every declared key metric.
- Deduplication rule, including the rule used when zero duplicates were found.
- Denominator and scope for ratios or shares.
- Key assumptions and any field-meaning uncertainty.

The Visualizations section must do more than list filenames. For each chart, name the chart, state the numerical pattern or comparison it shows, and identify the finding it supports. If no chart was created, omit this section.

Before submission verify all of the following:

- At least two Key Findings bullets contain concrete numbers, groups, rankings, periods, or chart references.
- Data Quality contains concrete counts, percentages, fields, or anomaly bounds.
- Correlation is not described as causation.
- A trend claim includes its comparison period. Do not claim a trend when no time field exists.
- The report does not introduce R&D metrics unless an R&D-specific template was selected from the actual question or schema.

Call `finish_report` once after this preflight. If validation still requests revision, change only the reported omissions and resubmit; validation feedback is recoverable and is not an execution failure.
