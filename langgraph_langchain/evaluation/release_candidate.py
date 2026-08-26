"""Candidate release acceptance across quality, reliability, and test gates."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ReleaseCandidateInput(BaseModel):
    evaluation_allowed: bool
    critical_quality_errors: int = Field(ge=0)
    tests_passed: int = Field(ge=0)
    tests_failed: int = Field(ge=0)
    orphan_processes: int = Field(default=0, ge=0)
    corrupted_states: int = Field(default=0, ge=0)
    recovery_checks_passed: bool = False
    package_hash_verified: bool = False


class ReleaseCandidateReport(BaseModel):
    accepted: bool
    reasons: list[str]
    input: ReleaseCandidateInput


def assess_release_candidate(value: ReleaseCandidateInput) -> ReleaseCandidateReport:
    reasons=[]
    if not value.evaluation_allowed: reasons.append("deterministic evaluation gate rejected the candidate")
    if value.critical_quality_errors: reasons.append(f"{value.critical_quality_errors} critical quality error(s)")
    if value.tests_failed: reasons.append(f"{value.tests_failed} test(s) failed")
    if value.tests_passed == 0: reasons.append("no passing regression tests recorded")
    if value.orphan_processes: reasons.append(f"{value.orphan_processes} orphan process(es)")
    if value.corrupted_states: reasons.append(f"{value.corrupted_states} corrupted state file(s)")
    if not value.recovery_checks_passed: reasons.append("recovery checks did not pass")
    if not value.package_hash_verified: reasons.append("analysis package hash was not verified")
    return ReleaseCandidateReport(accepted=not reasons,reasons=reasons,input=value)
