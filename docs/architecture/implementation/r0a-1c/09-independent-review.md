# R0a-1C Independent Review

Reviewer position: implementation-independent reverse pass over the final R0a-1C diff (Phase 11),
performed after all fixes and test updates.

## Independent Challenge (task §17.2, 20 items)

| # | Challenge | Verdict | Evidence |
| --- | --- | --- | --- |
| 1 | Any evidence default `verified` remaining? | SAFE (earned-by-construction) | domain collectors construct from internal deterministic results only; hash-bound; no user/LLM path; canonical default `unverified` |
| 2 | User/LLM can assign verified? | SAFE | no tool parameter exposes trust fields; sqlite `validation_status` param removed; `EvidenceItem(verification_status=...)` is constructor-internal only, reached from collector/record_finding paths with guards |
| 3 | Factory bypasses registry? | SAFE | no `create(..., verified=True)` pattern exists; adapters always emit `unverified` |
| 4 | Direct SQLite write bypasses authority? | SAFE | no direct evidence-status SQL writes exist; `evidence/sqlite.py` is a domain model, not a store (naming trap documented) |
| 5 | Restore auto-promotes? | FIXED/SAFE | V1 session restore downgrades (R1); runtime restore revalidates (A1.4.1); canonical restore resets |
| 6 | Consumer only checks status field? | SAFE | finding/report gates cross-check linked VerificationResult + linkage; locked by tests |
| 7 | Validator missing → pass? | SAFE | registry verify fails closed; test-locked |
| 8 | Validator exception → pass? | SAFE | exception propagates, status unchanged; test-locked |
| 9 | UNKNOWN → pass? | SAFE | unknown-style validator returns are errors → failed; test-locked |
| 10 | Hash covers only part of content? | SAFE | asset hashes cover full content (dataframe canonical / file stream); documented as integrity-not-correctness |
| 11 | Provenance missing but verified? | SAFE | registry requires execution linkage; sqlite provenance is mandatory model field; operation requires content hash (R3) |
| 12 | Legacy record directly trusted? | SAFE | all three restore paths downgrade or revalidate; no path trusts persisted status verbatim anymore |
| 13 | State artifact treated as evidence? | SAFE | `state__*` channel never feeds evidence models; verified by scan |
| 14 | Ordinary artifact auto-becomes evidence? | SAFE | no call site constructs evidence from accepted artifacts; trace performed |
| 15 | Evidence == execution result confusion? | SAFE | separate models; execution status is an input to verification, never the trust state |
| 16 | Docker success=true → verified? | SAFE | worker result carries no evidence; runtime evidence paths own trust |
| 17 | Image tag as immutable identity? | KNOWN LIMITATION | tag `1.1.0` only; **no digest claimed anywhere**; no digest fabricated; follow-up recorded |
| 18 | Multi-agent forwarding elevates trust? | SAFE | benchmark harness passes IDs only |
| 19 | Report hides unverified evidence? | SAFE | report_validator errors on unverified; report_claims degrades confidence |
| 20 | Untested production-reachable path? | UNKNOWN→covered | all traced creation/consumption paths have tests; residual UNKNOWN: none identified in production paths; evaluation/golden harnesses are offline-only |

## Additional Re-Scan

Post-fix scans for `verified`, `validation_status`, `verification_status`, `restore`, `persist`,
`deserialize` across `langgraph_langchain/` confirm: every remaining `"verified"` assignment is
(a) the registry promotion, (b) a guarded deterministic collector, (c) a gated runtime/financial
path, or (d) the metadata-only finding compatibility path (risk-tracked).

## UNKNOWN Items

- None blocking. The metadata-only compat path (item under `tool_record_finding.py:487`) is
  explicitly *not* provable as equally strong as the runtime path — it is retained for B11/V1
  compatibility, disclosed in the risk register rather than silently accepted.
