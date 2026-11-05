# B6 / Phase 1C: Phase 1 Golden Freeze

## Baseline version

`phase1-mvp-v1`

## Scope

The golden suite is deterministic and does not call an LLM. It evaluates:

- General Planner output;
- deterministic dataframe operations;
- SQLite SQL Tool execution;
- bounded SQLite result registration;
- Evidence creation;
- report validation;
- exact numeric expectations.

## Frozen catalog

- 10 datasets:
  - simple numeric;
  - regional sales;
  - time series;
  - missing values;
  - duplicates;
  - outliers;
  - mixed types;
  - insufficient data;
  - customers SQLite database;
  - employees SQLite database.
- 30 questions:
  - describe statistics;
  - filter / sort / top-N;
  - grouped comparison;
  - time trend;
  - anomaly detection;
  - correlation / ratio-like checks;
  - data quality;
  - SQLite JOIN / CTE / aggregate.

## Harness

New module:

```text
langgraph_langchain/evaluation/golden_phase1.py
```

Outputs:

- `golden_results.json`;
- `golden_report.md`;
- per-question result JSON.

Metrics:

- task success rate;
- numeric correctness rate;
- evidence completeness rate;
- report validity rate.

## Gate

`tests/test_golden_phase1.py` requires:

- at least 10 datasets;
- at least 30 questions;
- at least 28 passed cases;
- numeric correctness >= 90%;
- evidence completeness = 100%;
- report validity >= 90%.

Do not change existing expected values when adding features; add new golden cases instead.
