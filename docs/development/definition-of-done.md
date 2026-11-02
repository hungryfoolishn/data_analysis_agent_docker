# Definition of Done

## Every Change

1. Scope is tied to a milestone or a registered P0/P1 issue.
2. No unrelated files are committed.
3. Documentation is updated when behavior, architecture, or acceptance changes.
4. Tests are added or updated meaningfully.
5. `git diff --check` passes.
6. Relevant tests pass locally and in CI.
7. No intentionally weakened assertion or ignored test.
8. New code has clear module boundaries.
9. Security, read-only guarantees, and workspace isolation are preserved.

## Feature Definition of Done

A feature is done only when:

1. A user-visible scenario is specified.
2. Inputs, outputs, and failure states are documented.
3. Deterministic tests cover the main path and known failure states.
4. Evidence or audit trail is produced where applicable.
5. API or UI behavior is documented.
6. Golden acceptance data is added or updated.
7. Full test suite passes.
8. CI passes.

## Documentation Definition of Done

Architecture or product documentation is done only when:

1. Claims cite real code paths or test results.
2. Current behavior is separated from target behavior.
3. Non-goals are explicit.
4. Acceptance criteria are testable.
5. Open questions are marked as open.

## Milestone Definition of Done

A milestone is done only when:

1. All acceptance scenarios pass.
2. Known P0/P1 issues are resolved or explicitly deferred with owner/reason.
3. Golden datasets are frozen.
4. Test evidence is recorded.
5. Documentation reflects the shipped behavior.
6. No new scope was added without a milestone decision.
7. The next milestone backlog is updated.

## Commit Discipline

- Commit only related files.
- Do not commit local settings, credentials, generated logs, or unrelated notes.
- Use focused commit messages.
- Default: commit and push after verified implementation.
- Push to a review branch unless the user explicitly requests master.
