# ADR-021-023: P2 Platform Completion

## Batch 21: Persistence

SQLite WAL storage records transactional run snapshots and monotonic events.
Writes use BEGIN IMMEDIATE and optional optimistic versions. JSON snapshots remain
compatible exports and can be migrated with scripts/migrate_runtime_sqlite.py.

## Batch 22: Security and Governance

API authentication is enabled by API_AUTH_TOKEN and uses Bearer tokens. Health
remains unauthenticated. Responses add nosniff, frame-deny, and no-referrer
headers. Shared helpers redact secret/contact fields and identify sensitive
columns. Existing workspace path and executor isolation controls remain active.

## Batch 23: Candidate Acceptance

Release candidate assessment requires deterministic evaluation approval, zero
critical quality errors, a passing regression suite, no orphan processes or
corrupt states, recovery checks, and package hash verification. The assessment is
available at POST /release/candidate/assess.

## Boundary

SQLite is the authoritative single-node metadata store. Multi-host consensus,
external identity providers, and untrusted-tenant container sandboxes remain
deployment-specific integrations rather than hidden guarantees.
