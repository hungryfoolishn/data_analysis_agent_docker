# ADR-018-020: Remaining P1 Control and Workbench

## Batch 18

The deterministic quality checker returns error, warning, and disclosure issues.
It verifies Finding lineage references, percentage denominator disclosures, time
windows for trend claims, causal-language evidence levels, and failed execution
disclosures. It is exposed at GET /analysis/runs/{run_id}/quality.

## Batch 19

POST /analysis/tasks creates an in-memory background task and returns immediately.
The task has an idempotency key, status, task/run IDs, event cursor, event replay,
and cancellation endpoint. Existing synchronous chat and plan controls remain
compatible.

## Batch 20

The workbench state now has quality, task status, and event cursor fields, plus a
quality result renderer. Existing plan, execution, artifact, and lineage views
remain the authoritative review surfaces.

These batches do not claim durable multi-worker persistence; that is the P2
persistence batch.
