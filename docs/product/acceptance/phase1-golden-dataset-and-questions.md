# Phase 1 Golden Dataset and Question Plan

## Purpose

Phase 1 is accepted by a fixed dataset/question matrix, not by continuously
adding semantic tests.

## Dataset Plan

Create fixtures under:

```text
tests/fixtures/phase1/golden/
```

and a database fixture:

```text
tests/fixtures/phase1/golden/golden.sqlite
```

### Datasets

| ID | Dataset | Format | Purpose |
| --- | --- | --- | --- |
| D01 | simple_sales | CSV | Basic shape, totals, filters |
| D02 | grouped_sales | CSV | Group comparison |
| D03 | time_series_daily | CSV | Trend and time aggregation |
| D04 | orders_with_missing_values | CSV | Missing value detection |
| D05 | customer_orders_with_duplicates | CSV | Duplicate detection |
| D06 | product_catalog_mixed_types | CSV | Type coercion and profiling |
| D07 | events_outliers | CSV | Outlier detection |
| D08 | multi_sheet_inventory | XLSX | Multi-sheet Excel |
| D09 | parquet_web_events | Parquet | Parquet source |
| D10 | golden_sqlite_orders | SQLite | Relational schema and read-only SQL |

Each dataset must include:

- Source file.
- Expected schema.
- Expected row count.
- Intentional data-quality traits.
- Expected answer skeleton.
- Required evidence/artifacts.

## Question Matrix

At least 30 questions must be defined. The matrix must cover:

### 1. Dataset Understanding

1. How many rows and columns are in `D01`?
2. What are the column types in `D02`?
3. Which columns in `D04` contain missing values?
4. How many duplicate rows exist in `D05`?
5. What is the date range in `D03`?

### 2. Aggregation And Grouping

6. What is total revenue by region in `D01`?
7. Which department has the largest revenue decline in `D02`?
8. What is average order value by customer segment in `D10`?
9. What are the top five products by quantity in `D08`?
10. What is revenue share by category in `D01`?

### 3. Trend And Time Analysis

11. What is the monthly revenue trend in `D03`?
12. Which month had the largest month-over-month change in `D03`?
13. What is the seven-day rolling average for metric X in `D03`?
14. Compare first-half and second-half totals in `D03`.
15. Which event dates have abnormal volume in `D07`?

### 4. Quality And Risk

16. Which columns in `D06` have mixed or inconsistent types?
17. Which orders have missing customer IDs in `D04`?
18. Are there duplicate customer records in `D05`?
19. Which numeric columns have impossible negative values in `D04`?
20. What data-quality warnings should be disclosed for `D08`?

### 5. Relationship And Distribution

21. What is the correlation between marketing spend and revenue in `D01`?
22. How does value distribution differ across regions in `D02`?
23. Which outliers appear in transaction amount in `D07`?
24. What is the distribution of order status in `D10`?
25. What is the median and p90 delivery time in `D04`?

### 6. SQL-specific Questions

26. Join orders and customers in `D10` and report revenue by customer tier.
27. Use read-only SQL to count orders per month in `D10`.
28. Use SQL to find the top three products by revenue in `D10`.
29. Use SQL to identify customers with more than N orders in `D10`.
30. Compare SQL result row count and total revenue to a pandas result.

## Acceptance Rules

A question passes only if:

1. The answer is numerically correct against the fixture.
2. The plan is visible and references valid data fields.
3. Every key claim has evidence.
4. Evidence links to an execution and artifact/source.
5. Missing data or ambiguity is disclosed.
6. No unsupported recommendation or causal claim is generated.
7. SQL queries are read-only.

## Required Reports

Each golden report must include:

- Summary.
- Data context.
- Metric definitions.
- Data quality notes.
- Key findings.
- Tables or charts.
- Evidence references.
- Limitations.

## CI Strategy

Phase 1 CI must include:

1. Unit tests for data adapters/planner/evidence.
2. SQLite end-to-end golden test.
3. At least one file-based e2e test.
4. Report contract validation.
5. Artifact existence verification.
6. A subset of golden questions as smoke tests.

The full 30-question matrix may run nightly or as a manual release gate, but the
CI suite must not rely only on broad financial tests.
