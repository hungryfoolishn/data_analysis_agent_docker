# R0b Security Review

## Controls verified

| Control | Result | Evidence |
| --- | --- | --- |
| Session ID whitelist | PASS | `security.validate_session_id`; direct and API tests |
| Path traversal rejection | PASS | `test_download_blocks_traversal_and_symlink_escape`; `test_workspace_root_rejects_symlink_session` |
| Symlink escape rejection | PASS | safe workspace join plus R0b tests |
| Unsafe persisted record quarantine | PASS | `test_unsafe_persisted_session_is_skipped_without_external_delete` |
| Cross-owner read/write/delete denial | PASS | `test_cross_owner_session_is_hidden_and_not_mutated` |
| Cross-owner run denial | PASS | `test_cross_owner_run_access_is_denied` |
| Client absolute file boundary | PASS | `test_data_file_resolution_is_session_scoped` |
| No sensitive disclosure | PASS | Ownership mismatch returns session-not-found |
| No unauthorized cleanup | PASS | cleanup uses canonical identity validation |
| Run package/report path boundary | PASS | canonical safe join and affected suites |

## Adversarial conclusions

- Session IDs cannot traverse the workspace root.
- A symlinked session directory cannot be used to escape the root.
- Cross-owner API requests receive the same 404 as missing sessions.
- Invalid/unsafe persisted session records cannot trigger arbitrary deletion.
- Run IDs cannot access another owner's live session resources.
- Client-supplied data paths cannot read arbitrary host files.
- Runtime history listing, package and report rebuild cannot use unsafe persisted session paths.

## Remaining limitations

| ID | Limitation | Severity | Impact | Mitigation / future |
| --- | --- | ---: | --- | --- |
| R0B-L1 | Identity is deployment-single-owner, not per-human multi-user. | P2 | Multi-user isolation requires a real identity provider. | R1+ user identity work |
| R0B-L2 | Legacy run snapshots without owner metadata are treated as deployment-owner. | P2 | Only acceptable while single-owner compatibility is required. | Migration in R1 |
| R0B-L3 | Filesystem TOCTOU is reduced but cannot be fully eliminated by Python path checks alone. | P2 | Very narrow race remains on hostile local filesystems. | Runtime/user namespaces and OS-level sandboxing in later hardening |
| R0B-L4 | Dashboard uses a separate legacy surface and remains outside R0b scope. | P2 | No API-side regression is claimed for Dashboard. | Dashboard work package |

No Critical/High issue remains open in the R0b API/filesystem boundary.