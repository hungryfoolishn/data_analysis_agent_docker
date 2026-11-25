# Golden Evaluation Audit — 50 Cases

## Inventory

50 cases across 7 files, zero duplicate IDs (scripted count):
monthly_trend 6, q2_1_metrics 10, multi_metric 6, anomaly_detection 6, period_comparison 8,
department_compare 8, comprehensive_analysis 6. Sampled case (`trend_analysis_001`)
carries: real question, versioned dataset with SHA-256, numeric ground truth per period
with tolerance 0.01, dimension/group filters, minimum verified evidence (2), required
finding, required report with content tokens.

## Quality assessment

| Quality dimension | Verdict |
| --- | --- |
| Numeric ground truth | PRESENT — per-metric values + tolerance, context-matched (period/group/filters) |
| Can distinguish correct vs incorrect analysis | YES — numeric score weight 0.30; wrong numbers fail |
| Keyword gaming | PARTIALLY RESISTANT — report tokens contribute 0.20 but cannot pass alone; pass requires correctness-critical components (runner gate) |
| Evidence correctness | REQUIRED — verified-evidence minimums enforced |
| Hardcoded-response pass | NOT FEASIBLE — metrics are computed from versioned datasets |
| Negative/adversarial cases | MISSING — no cases assert wrong-analysis rejection end-to-end |
| Duplicates / overrepresentation | None found; mix of trend/aggregation/anomaly/comparison/multi-metric |
| Boundary data | Present via anomaly/comparison cases; no adversarial edge fixtures |

## Scoring framework (runner.py read)

Weights: execution 0.10, verification 0.10, numeric 0.30, evidence 0.15, finding 0.15,
report 0.20. Failed execution → execution_score 0; missing answers score 0 in numeric
comparisons (no accidental credit); `passed` requires correctness-critical components
beyond the weighted sum. Deterministic; versioned datasets make results comparable across
runs. Skill attribution keyed by stable id/version/hash, not display name.

Framework layers verified: `GoldenEvaluationRunner` (50-case), `DeterministicEvaluator`
(fact/forbidden-claim/lineage assertions), runtime golden E2E with real planner + real
executors + real evidence. 37 evaluation/planner tests green this audit.

## Verdict

**VALID_WITH_LIMITATIONS** — a genuine correctness baseline for regression tracking and
future R3 iteration. Not yet an adversarial or planning-quality benchmark (matches the
R3 deferred-with-safe-default list; smallest useful next step is adding negative cases
that assert rejection of incorrect analyses).
