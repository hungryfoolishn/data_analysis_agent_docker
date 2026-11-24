# R0a-1B Docker Security Validation (real container runs)

## Container Runtime Parameters (enforced and inspected)

| Parameter | Value | Verified by |
| --- | --- | --- |
| User | `10001:10001` (non-root) | `test_container_runtime_config_is_enforced` + E2E `UID= 10001` |
| NetworkMode | `none` | runtime inspect + E2E socket probe (`NETWORK=blocked`) |
| ReadonlyRootfs | `true` | runtime inspect + E2E write probe (`OSError: Read-only file system`) |
| CapDrop | `ALL` | runtime inspect |
| SecurityOpt | `no-new-privileges` | runtime inspect |
| PidsLimit | `128` | runtime inspect |
| Memory / MemorySwap | `512MiB / 512MiB` | runtime inspect |
| NanoCpus | `1000000000` (1 CPU) | runtime inspect |
| Tmpfs | `/tmp` 256m | runtime config |
| Privileged | `false` | runtime inspect |
| WorkingDir | `/workspace/output` | image config |

- The worker image contains no docker CLI and the worker container receives no docker socket.

## E2E Security Assertions (all passing, real daemon)

1. `UID= 10001` — non-root execution.
2. Host sentinel `/tmp/r0a1b_host_sentinel_unique` not visible inside the container.
3. Rootfs write denied (`OSError` errno 30, read-only).
4. Egress network blocked (`socket.connect(('8.8.8.8', 53))` fails with OSError).
5. Two concurrent jobs cannot see each other's workspace files.
6. Missing worker image fails the job closed; no inline/process fallback occurs.

## Input Mirroring Security Notes

- `_mirror_inputs_into_cwd` creates symlinks in the writable output cwd pointing to staged input
  files, preserving legacy relative-path reads. Symlinks are **rejected** by `_collect_artifacts`
  and by the manager's archive fetch, so they cannot be exported or followed for exfiltration.
- Mirroring skips `job.json`, dotfiles, and `state__*` files (state has its own channel and must
  not shadow the emit target).
- `/etc`, `/proc`, `/sys`, `/dev`, `/root` writes are additionally blocked at the host-side
  static safety validator before dispatch (defense in depth).
