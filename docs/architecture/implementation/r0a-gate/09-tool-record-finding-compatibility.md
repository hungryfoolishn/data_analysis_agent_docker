# R0a Gate Metadata-only Finding Adjudication (K-2)

## Path inventory

`tools/tool_record_finding.py` has three paths:
1. **Runtime V2 active** (`runtime.scheduler is not None`): full lineage gate — evidence must cite
   a succeeded execution with a passing verification; FindingBuilder provenance checks run.
2. **Metadata-only runtime** (`runtime` exists, `scheduler is None` — the common V1 session,
   `plan_first=True` creates a plan but no scheduler): recorded a self-referential
   `evidence_existence` verification and **promoted the evidence to `verified`**.
3. **No runtime**: findings appended to session only (no runtime trust semantics).

## Vulnerability assessment (pre-fix)

The metadata-only path promoted LLM-authored evidence text to `verified` based on existence
alone. That status would satisfy the report gate (`verification_status == verified` +
`verification_result_id` present), i.e. a **real trust bypass** for the dominant V1 session flow.
User-controlled inputs: the evidence text/fields (LLM) — no execution provenance required.

## Adjudication: RESTRICTED_COMPATIBILITY — fixed within R0a

- The existence check is **kept and recorded** (traceability + V1 compatibility: the finding is
  still recorded, state machine progress unchanged).
- The evidence **is no longer promoted** to `verified`; it keeps `unverified` with the
  verification link (`check_type=evidence_existence`), so every downstream consumer treats it as
  unverified until the Runtime V2 provenance gate re-establishes trust.
- No test in the repository depended on the promoted status (verified by scan before the change).

## Evidence

- Gate regression: `tests/test_r0a_gate_e2e.py::test_e2e_n16_metadata_only_finding_not_verified`
  (real tool invocation on a V1 session): evidence unverified, link recorded, check type
  `evidence_existence`, and `validate_report` rejects a finding citing it as verified support.
- Full affected-suite regression green (finding/lineage/report/validator suites).
