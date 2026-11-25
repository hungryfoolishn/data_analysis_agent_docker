# R2 Capability Classification and Convergence Decisions

## Classification

| Class | Capabilities | Decision |
| --- | --- | --- |
| Native Unified Runtime | `run_analysis_operation`, `load_data`, `run_sqlite_query`, `detect_anomalies`, plan/task/execution/evidence/report platform | Keep as Runtime V2 foundation |
| Legacy ReAct native, Runtime V2 method candidates | Structured analysis method tools | Migrate behind Runtime V2 method registry in R2.5 without changing outputs |
| Legacy-only / parity gap | `eda_profile`, richer dynamic exploration, iterative tool selection | Migrate behavior into V2 with budget/governance; do not copy unsafe host execution |
| Shared governance | `record_finding`, `finish_report`, metric/assumption declaration | Preserve contracts; extend only with tests |
| Domain/frozen | Financial models/normalization, Macro transforms, B8 acquisition, B11 multi-agent | Do not modify frozen logic in R2; wrap/adapter only |
| Deprecated | None identified | No capability is safe to delete solely because of convergence |

## Divergence findings

1. **Runtime routing divergence.** Runtime V1 remains default; Runtime V2 is opt-in. This is a
   preserved compatibility decision, not a capability loss.
2. **Dynamic exploration divergence.** V1 ReAct can iterate freely across tools; V2's deterministic
   plan path is safer but less exploratory. R2.5 must add bounded exploration.
3. **Tool identity divergence.** The same logical analysis operation can be reached from a Legacy
   tool and a V2 structured operation. These are method candidates, not duplicate products; R2.5
   will normalize entry points while preserving result semantics.
4. **Domain divergence.** Financial is formalized as a Domain Pack contract; Macro is not yet. Macro
   should gain a contract only after R2.5 parity, without changing frozen behavior.
5. **Evidence divergence.** R0a removed self-declared verified paths. Domain collectors still enter
   through registry verification rather than a second Evidence authority.

## Migration candidates for R2.5

| Priority | Capability | Required parity behavior |
| --- | ---: | --- |
| M-01 | Dynamic schema/data exploration | Bounded iterative load/profile/select with stop conditions |
| M-02 | Multi-round tool calls | Tool-call budget, intermediate state, result feedback |
| M-03 | Intermediate result inspection | Runtime-visible variables/artifacts without host bypass |
| M-04 | Result-driven next tool selection | Planner can revise plan from execution/evidence state |
| M-05 | Failure recovery | Retry/alternative strategy within policy and budget |
| M-06 | EDA profile | V2 access to legacy EDA artifacts/findings |
| M-07 | Structured method registry | Expose all analysis methods through V2 with tests |

## Explicit non-goals

- No frozen financial/macro/B8/B11 contract changes.
- No removal of Legacy Runtime before parity.
- No new execution backend or Evidence authority.
- No planner intelligence implementation in R2; that is R3 after R2.5.