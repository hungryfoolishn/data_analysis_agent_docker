# Capability Inventory Audit — R2

## Contract lock (executed)

`tests/test_capability_inventory.py` (read + executed in suite) locks the real registry:

- exact tool names per toolset: analysis 23, memory 1, skills 2 = 26 tools;
- no duplicate names;
- financial pack contract code-anchored (`pack_id=financial`, `pack_version=0.2.0`,
  capabilities `financial_models`/`financial_normalization`, macro dependency boundary,
  unknown normalization policy `reject (never guess)`).

## Completeness method

Independently enumerated dynamic-code and tool dispatch sites (grep over production
code): `python_repl`, `delegate_analysis`, structured analysis operations, sqlite query,
load_data, EDA, 12 analysis-method tools, governance tools (record_finding, finish_report,
declare_metric, declare_assumption), skills (2), memory (1), plus platform capabilities
(runtime routing, worker execution, planner, evaluation). No production capability was
found outside the inventory coverage; the registry contract test makes drift detectable
going forward.

## Traceability

- Every inventory entry carries: code entry, purpose, Legacy/V2 availability, evidence
  support, tests/notes (01-capability-inventory.md verified against code).
- No false complete claims found: entries marked Legacy-only-until-parity (eda_profile)
  were subsequently covered in R2.5, and Method-candidate entries are honestly labeled.
- No missing material capability discovered in production code during this audit.
- Duplicates: none (registry test asserts uniqueness).

## Verdict

**VERIFIED** for the declared R2 purpose (a reliable, drift-locked baseline before
migration). Coverage: 26/26 registry tools locked; no independently discovered material gap.

Known boundary: the inventory intentionally excludes non-tool platform internals
(scheduler, persistence) which are covered by R1 docs instead — documented, not a gap.
