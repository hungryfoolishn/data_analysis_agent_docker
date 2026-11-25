# Stage A — R0a Final Independent Review & Freeze

## Executive decision

**FROZEN — INDEPENDENTLY VERIFIED.**

Stage A revalidated the R0a execution and trust baseline on the new convergence branch before
starting R0b. No new security defect was found.

## Convergence baseline

| Item | Value |
| --- | --- |
| Execution worktree | `/python/pragrams/data_analysis_agent_convergence` |
| Branch | `codex/architecture-convergence-v1.1-e2e` |
| Starting HEAD | `cbaecd4` |
| Base content | identical to R0a Gate final commit |
| Working tree | clean at Stage A start |
| Authority document | `docs/202611/DeepAnalyze Architecture Convergence Blueprint V1.1.md` |

## Git inheritance verification

Verified with `git merge-base --is-ancestor` against `cbaecd4`:

| Stage | Commit | Ancestor of HEAD | Scope |
| --- | --- | --- | --- |
| R0a-1A | `83c8bfa` | yes | Execution policy, product entry hardening, delegate migration, policy tests |
| R0a-1B | `4878adc` | yes | Docker worker product integration, job contract, workspace/artifact isolation, E2E |
| R0a-1C | `a802571` | yes | Evidence registry trust, persistence/restore downgrade, consumer enforcement |
| R0a Gate | `cbaecd4` | HEAD | Independent Gate E2E, metadata-only downgrade, image identity/provenance |

Each commit's file inventory was inspected with `git show --stat`; there was no cross-stage
mixture outside its stated security boundary.

## Execution entry verification

All three untrusted product entries resolve through `resolve_execution_backend(UNTRUSTED)`:

- `langgraph_langchain/langgraph_agent.py:1166`
- `langgraph_langchain/tools/tool_delegate.py:179`
- `langgraph_langchain/execution/python_task_executor.py:135`

Dispatch is then performed through `WorkerDispatcher`. Unknown/empty backend configuration raises.
A process backend for untrusted code requires both explicit backend selection and
`ALLOW_UNTRUSTED_PROCESS_EXECUTION=1`; Docker failure has no silent process fallback.

## Docker Worker verification

Current creation config is explicit, not merely implied by Docker defaults:

- `langgraph_langchain/execution/container_manager.py:600` — `NetworkingConfig: {"EndpointsConfig": {}}`
- `:604-607` — CPU, memory, memory swap and PID limits
- `:608` — `CapDrop: ["ALL"]`
- `:609` — `SecurityOpt: ["no-new-privileges"]`
- `:610` — `ReadonlyRootfs: true`

This resolves the historical ambiguity about `CapDrop ALL`: it is configured in code and locked by
both unit and real-container tests.

Image identity was also rechecked:

- configured reference is resolved to a content-addressed image ID before creation;
- optional `DEEPANALYZE_WORKER_IMAGE_DIGEST` pinning is enforced;
- the created container's `Image` field must equal the resolved ID;
- mismatch fails closed and cleans up the partial container.

## Evidence trust verification

The direct `verified` promotion at
`langgraph_langchain/tools/tool_record_finding.py:457` belongs only to the Runtime V2 provenance
path after a succeeded execution and passing verification. The metadata-only compatibility path
explicitly does **not** promote evidence (`:487-495`).

Restore revalidation at `langgraph_langchain/runtime/context.py:773-805` retains verified status
only when the original verification passed and all execution/artifact/source checks still hold;
otherwise it downgrades to `unverified` or `failed`.

`EvidenceRegistry.verify()` requires a content validator and fails closed when absent.

## Stage A test evidence

Executed on the convergence worktree:

```bash
python -m pytest \
  tests/test_r0a_gate_e2e.py \
  tests/test_execution_policy.py \
  tests/test_execution_entry_hardening.py \
  tests/test_evidence_trust_boundary.py \
  tests/test_docker_product_e2e.py \
  tests/test_container_manager.py \
  tests/test_docker_worker_backend.py \
  tests/test_product_job.py -q
```

Result:

```text
87 passed, 2 warnings in 150.75s
```

The 2 warnings are only unregistered pytest marks; they are not functional failures.

## Full regression basis

The convergence starting tree is identical to R0a Gate commit `cbaecd4`. The R0a Gate full
regression for that exact code content was:

```text
2 failed, 1944 passed, 13 skipped
```

The only failures are the frozen A1.3.2 Dashboard baseline. No new R0a regression was observed.

## Known limitations carried into later stages

These remain documented in R0a Gate and are not Stage A blockers:

- digest pinning is operator-controlled rather than mandatory;
- registry-level image signing is out of scope;
- Docker hardening is strong but may still add an explicit seccomp/AppArmor profile later;
- state deserialization depends on patched parquet/NumPy libraries;
- Dashboard failures remain outside architecture convergence scope.

## Freeze decision

R0a is accepted as a stable execution/trust baseline. Stage B — R0b Identity & Path Boundary — is
unlocked.