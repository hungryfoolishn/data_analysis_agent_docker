# DeepAnalyze Product Vision

## One-line Vision

DeepAnalyze is a general-purpose data analysis Agent platform for tabular files
and relational databases, with macroeconomic and corporate financial analysis
as the first Domain Packs.

## Core Product Loop

```text
Acquire trusted data
  -> Understand schema and business semantics
  -> Propose an analysis plan
  -> Execute deterministic or Agent-assisted analysis tools
  -> Validate results and create evidence
  -> Produce a traceable report
```

## Product Layers

| Layer | Responsibility | Not Responsible For |
| --- | --- | --- |
| Data Analysis Platform | Files, databases, schema understanding, plans, execution, evidence, charts, reports, sessions | Domain-specific finance or macro rules |
| Data Acquisition Platform | Public APIs, adapters, collection jobs, normalization, quality checks | Analysis reasoning |
| Domain Packs | Domain metrics, semantics, formulas, rules, risk models | Generic orchestration |

## First Domain Packs

1. Chinese and U.S. macroeconomic indicators.
2. Listed-company financial statements and financial analysis.

These are Domain Packs, not core Runtime modules.

## First-phase User Value

A user should be able to upload a CSV/Excel file or connect to one relational
database, ask a natural-language question, and receive:

- A data/schema understanding summary.
- A reviewable or inspectable analysis plan.
- Executed Python/pandas or read-only SQL analysis.
- Tables, charts, and concise findings.
- A final report with evidence and caveats.

## Product Principles

1. **General Runtime first.** File/database execution, evidence, planning, and
   reporting belong to the platform.
2. **Domain knowledge is pluggable.** Finance and macro semantics should be
   removable without breaking the generic runtime.
3. **Deterministic before agentic.** Prefer repeatable tools, plans, and
   verification over unconstrained LLM reasoning.
4. **Every important claim is traceable.** Findings must connect to evidence,
   execution, artifact, and source data.
5. **Reports are the product.** Users need decision-ready, auditable output.
6. **Scale down before overpromising.** A small reliable MVP is better than a
   broad but untrusted agent.

## North-star Acceptance Statement

A user can upload one of the Phase 1 golden datasets or connect a golden
database, ask one of the golden questions, and receive a correct, traceable,
report-quality answer without the assistant inventing numbers.
