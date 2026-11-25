# R0a Gate Worker Image Identity & Execution Provenance

## Ruling on K-1 (image tag anchoring)

**Implemented at this gate (FIXED).** A tag is configuration context, never identity.

## Implementation (minimal, no protocol change)

1. `DockerEngineClient.inspect_image(image)` — Engine API image inspect.
2. `ContainerManager.resolve_image_identity()` — resolves the configured image to a
   content-addressed **image ID** (`sha256:...`) plus registry `RepoDigests`, **before** container
   creation. `ImageIdentityMismatchError` if a pinned digest is configured
   (`DEEPANALYZE_WORKER_IMAGE_DIGEST`) and neither the image ID nor any repo digest matches.
3. `run_container()` — records the resolved identity on the `ContainerHandle`, then verifies the
   created container's actual `Image` field against the resolved ID; a tag re-pointed between
   resolution and creation yields a mismatch → job fails closed and the partial container is
   cleaned up.
4. `WorkerResult.image_id` — new optional field populated on docker success: audit provenance of
   which content-addressed image executed the job. Additive internal contract; wire protocol v1
   unchanged.
5. Digest pinning is operator-controlled via `DEEPANALYZE_WORKER_IMAGE_DIGEST`; unset keeps
   current behavior (identity resolved + container-verified, digest not pinned).

## What this proves / does not prove

- **Proves**: the container that executed a job was created from exactly the resolved image
  object (content-addressed ID); a configured digest pin is enforced before any container runs.
- **Does not prove**: registry-level authenticity of the image (no signature verification), and
  digest pinning is only active when the operator sets the variable. Image identity is audit
  provenance — it is **not** a substitute for evidence verification and never promotes trust.

## Execution Provenance recorded

`execution_id` (job), session id (job.session_id), worker backend, image tag, image ID,
protocol version, created-at, result status, artifact IDs + hashes (ArtifactReference),
state artifact names/sizes (manifest), input integrity (manager manifest hashes verified before
publish). All fields are existing verified values; nothing speculative was added.
