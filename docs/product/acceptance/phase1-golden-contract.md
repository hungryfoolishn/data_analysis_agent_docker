# Phase 1 Golden Contract

## Purpose

This document freezes the fixture, question, expected-result, and acceptance
contracts for Phase 1. It is the M1 gate for M2-M8 implementation.

## Directory Contract

Golden fixtures will live under:

```text
tests/fixtures/phase1/golden/
```

Recommended layout:

```text
tests/fixtures/phase1/golden/
  manifests/
    datasets.yaml
    questions.yaml
  files/
    D01_simple_sales.csv
    D02_grouped_sales.csv
    D03_time_series_daily.csv
    D04_orders_with_missing_values.csv
    D05_customer_orders_with_duplicates.csv
    D06_product_catalog_mixed_types.csv
    D07_events_outliers.csv
    D08_multi_sheet_inventory.xlsx
    D09_parquet_web_events.parquet
  database/
    golden_sqlite.py
```

The SQLite database must be created by `golden_sqlite.py` in a temporary
directory during tests. A checked-in binary SQLite file is not required and
should not be used as the primary fixture.

## Dataset Manifest Contract

`datasets.yaml` must contain one entry per fixture. Required fields:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `dataset_id` | string | yes | Stable ID such as `D01`. |
| `description` | string | yes | Short human-readable purpose. |
| `source_type` | enum | yes | `csv`, `xlsx`, `parquet`, or `sqlite`. |
| `fixture_path` | string | yes | Repository-relative fixture path or fixture builder. |
| `schema` | list | yes | Column/table names, logical types, and nullable flags. |
| `row_count` | integer | yes | Exact expected row count after loading. |
| `primary_key` | list | no | Columns that uniquely identify a row. |
| `nullability` | object | yes | Expected missing-value count by column. |
| `data_semantics` | list | yes | Business meaning, units, time grain, and caveats. |
| `expected_profile` | object | yes | Deterministic profile facts to assert. |
| `expected_artifacts` | list | yes | Artifact names or types that must be produced. |
| `tags` | list | yes | Scenario tags such as `grouping`, `time`, `quality`. |

Example:

```yaml
datasets:
  - dataset_id: D01
    description: Simple regional sales for totals and filters.
    source_type: csv
    fixture_path: files/D01_simple_sales.csv
    schema:
      - name: order_id
        logical_type: string
        nullable: false
      - name: region
        logical_type: string
        nullable: false
      - name: revenue
        logical_type: float
        nullable: false
    row_count: 12
    primary_key: [order_id]
    nullability:
      order_id: 0
      region: 0
      revenue: 0
    data_semantics:
      - revenue is in USD.
      - one row is one order line.
    expected_profile:
      total_revenue: 15450.0
      regions: ["East", "North", "South", "West"]
    expected_artifacts:
      - type: table
        name: revenue_by_region
    tags: [aggregation]
```

## Question Manifest Contract

`questions.yaml` must contain one entry per golden question. Required fields:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `question_id` | string | yes | Stable ID such as `Q001`. |
| `dataset_id` | string | yes | Referenced dataset ID. |
| `question` | string | yes | User-facing question. |
| `intent` | enum | yes | `profile`, `aggregate`, `trend`, `quality`, `relationship`, or `sql`. |
| `expected_answer_type` | enum | yes | `scalar`, `table`, `chart`, or `report`. |
| `expected_result` | any | yes | Exact or structurally comparable expected output. |
| `tolerance` | object | yes | Numeric tolerance rules. |
| `required_plan_methods` | list | yes | Methods that must appear in the plan. |
| `required_evidence` | object | yes | Evidence/artifact assertions. |
| `forbidden` | list | yes | Assertions that must not occur. |
| `priority` | enum | yes | `ci-smoke`, `ci-full`, or `nightly`. |

Example:

```yaml
questions:
  - question_id: Q006
    dataset_id: D01
    question: What is total revenue by region in D01?
    intent: aggregate
    expected_answer_type: table
    expected_result:
      columns: [region, total_revenue]
      rows:
        - [East, 5000.0]
        - [North, 4200.0]
        - [South, 2600.0]
        - [West, 3650.0]
    tolerance:
      numeric: 0.0001
    required_plan_methods: [load_data, python_repl]
    required_evidence:
      min_evidence_items: 1
      must_link_execution: true
      must_link_artifact: true
    forbidden:
      - unsupported causal claim
    priority: ci-smoke
```

## Numeric Tolerance Rules

1. Currency and numeric aggregates use absolute or relative tolerance `1e-4`
   unless the fixture defines another value.
2. Percentages use tolerance `1e-4` percentage points.
3. Profile counts must match exactly.
4. Row ordering is only asserted when the question requires ordering.
5. Floating-point output must not be compared by serialized string alone.
6. Where a deterministic pandas and SQL result are both required, both must pass
   the same tolerance.

## Evidence And Artifact Assertions

Each executable golden question must assert:

1. A valid plan exists.
2. Referenced data fields exist.
3. Each required method executed successfully.
4. Each key finding links to at least one evidence item.
5. Evidence links to a successful execution.
6. Evidence links to required artifact or data source.
7. Artifacts referenced by evidence exist on disk.
8. SQL questions prove the query text and result artifact.
9. The report includes limitations or data-quality caveats when relevant.

A question fails if:

1. Expected values are missing or outside tolerance.
2. Evidence is missing, unverified, or unrelated.
3. A required artifact does not exist.
4. The plan references unavailable methods or unknown fields.
5. SQL performs a mutation.
6. The report asserts causality without support.
7. Runtime V2 silently falls back to legacy ReAct.

## M1 Freeze Scope

M1 freezes the contract schema, field names, fixture-building rules, tolerance
rules, evidence assertions, coverage matrix, and CI tiers.

M1 does not freeze the final `datasets.yaml`, `questions.yaml`, every fixture
file, or all expected numeric values. Those are implemented in later milestones:

| Deliverable | Milestone |
| --- | --- |
| SQLite fixture builder | M2 |
| Deterministic file-data profiles | M3 |
| Complete question expected values and CI matrix | M8 |

Changing a field or tolerance after review requires a milestone decision and a
documented reason. Adding or correcting fixture data after M8 is a release-gate
change, not a routine test tweak.

## Source Type Boundary

Golden manifest `source_type` values are:

```text
csv
xlsx
parquet
sqlite
```

In-memory DataFrames are runtime inputs, not standalone Golden fixtures. They may
be produced by a test helper, file adapter, or SQL query, but they are not
assigned a separate `dataframe` source_type in the dataset manifest. The
manifest still records the originating persisted source or fixture builder.

## SQLite CI Baseline

- Use a temporary SQLite file or `:memory:` database created per test.
- Initialize with a deterministic Python fixture helper and fixed seed.
- Do not require network, Docker services, or developer-local databases.
- Use one isolated database instance per test unless a test explicitly documents
  sharing.
- Production connection configuration must not be used by Golden tests.
- Fixture cleanup must remove temporary files even on failure.

## Question Coverage

The Phase 1 matrix must retain at least 30 questions and cover:

1. Dataset understanding.
2. Aggregation and grouping.
3. Time trend and comparison.
4. Missing values, duplicates, and quality.
5. Outliers and distribution.
6. Relational joins and read-only SQL.
7. At least one file and SQLite path.
8. At least one chart/report-producing scenario.

## CI Tiers

| Tier | Scope | Requirement |
| --- | --- | --- |
| `ci-smoke` | 8-12 questions | Must pass on every pull request. |
| `ci-full` | All 30+ questions | Must pass before milestone completion. |
| `nightly` | Full matrix plus performance/noisy-data variants | Run on schedule or release candidate. |

## Contract Freeze Boundary

M1 is complete when the contract schema, fixture-building rules, tolerance
rules, evidence assertions, coverage matrix, and CI tiers are stable. Actual
manifests, fixtures, and final expected values are delivered in M2/M3/M8 as
described above. M2 may begin after review; later contract changes require a
milestone decision and a documented reason.
