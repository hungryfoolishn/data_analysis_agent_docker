# B10 Master Execution Plan

Status date: 2026-11-17  
Branch: `codex/phase1-m2.4-sqlite-schema`  
Authority: repository B10.1 architecture, frozen B9/B8 contracts, and the B10.2 independent audit findings.

## 1. Objective

Complete Financial Domain Pack stages B10.1-B10.7 without creating a parallel Macro/Asset/Snapshot/Evidence/Runtime stack. Each stage must pass its own gate before dependent work starts.

## 2. Stage Map

| Stage | Scope | Primary outputs | Dependency gate |
|---|---|---|---|
| B10.1 | Pack architecture/contract | Existing `DomainPackRegistry` + Financial contract | Completed |
| B10.2 | Canonical models, identity, normalization, compatibility | Close four audited P1s and required P2s | Independent audit found NOT FROZEN; repair required |
| B10.3 | Financial period semantics | TTM, cumulative-to-quarter, date boundaries, non-natural fiscal periods | B10.2 re-frozen |
| B10.4 | Financial indicator catalog | Typed metric definitions, units, periods, dependencies, versions | B10.3 frozen |
| B10.5 | SEC / China acquisition adapters | B8 `FetchResult -> AcquisitionService -> SnapshotStore -> DataAsset` path, filing metadata | B10.4 frozen; real network evidence separated from fixtures |
| B10.6 | Per repository definition: intent/context/templates + Runtime V2 + Evidence bridge | Financial Pack workflows through existing Runtime V2/EvidenceRegistry | B10.5 frozen |
| B10.7 | Golden Dataset / E2E / final acceptance | Deterministic US/CN fixtures and repeatable commands | B10.6 frozen |

## 3. B10.2 Repair Matrix

| Issue | Risk | Blocks next stage | Handling | Acceptance |
|---|---|---|---|---|
| P1-1 invalid statement dates | Invalid periods enter canonical layer | Yes | B10.2 | strict ISO dates and order validation |
| P1-2 lossy canonical-to-legacy conversion | Data loss and semantic mismatch | Yes | B10.2 | reject missing source metadata, extras, wrong kind/currency |
| P1-3 registry accepts key=None or asset_id="" | Untraceable fact identity | Yes | B10.2 | valid key + non-empty asset_id/source/content version |
| P1-4 statement/item currency mismatch | Cross-currency ambiguity | Yes | B10.2 | reject mismatch without FX; explicit FX updates both currencies |
| P2-1 item/unit kind mismatch | Unit semantics conflict | Yes | B10.2 | item and unit kinds must match |
| P2-2 inconsistent item periods | Period semantics conflict | Yes | B10.2 | when present, item period must match statement |
| P2-3 empty FX metadata | Missing rate traceability | Yes | B10.2 | date/source/version required |
| P2-4 lexical accession ordering | Unstable latest semantics | No, but repaired cheaply | B10.2 | retain registration order |
| P2-5 source version omits source_document_id | Same accession, different source can collide | Yes | B10.2 | include source_document_id in source_version |
| P2-6 implicit yoy base | Hard to audit conversion | No | B10.2 | lineage records explicit base 100 |
| P2-7 mutable canonical models | Input can be mutated externally | No, but repaired cheaply | B10.2 | canonical line/statement models frozen |

## 4. Test Gates

Each stage runs at least:

1. new unit tests;
2. stage-specific tests;
3. direct dependency regression;
4. Financial/Agent regression;
5. Platform/Asset/Snapshot/Acquisition/Evidence/Macro regression;
6. full suite where the environment permits.

Known historical failures are compared by nodeid; they are not treated as new B10 regressions.

## 5. Commit Plan

| Stage | Suggested commit |
|---|---|
| Plan | `docs(financial): add B10 master execution plan` |
| B10.2 | `fix(financial): close B10.2 identity and normalization gaps` |
| B10.3 | `feat(financial): extend financial period semantics` |
| B10.4 | `feat(financial): add indicator catalog` |
| B10.5 | `feat(financial): integrate SEC and China filing adapters` |
| B10.6 | `feat(financial): implement financial domain workflows` |
| B10.7 | `test(financial): add golden dataset and end-to-end coverage` |
