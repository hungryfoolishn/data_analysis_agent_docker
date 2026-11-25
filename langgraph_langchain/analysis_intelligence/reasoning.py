"""Evidence-driven reasoning & multi-turn orchestration state (V1.0, P5).

Claims are bound to real evidence with deterministic verification. Unsupported
conclusions are rejected or downgraded — never passed through because "the run
succeeded". `AnalysisState` coordinates intent/plan/revisions/exploration/results
with an explicit termination contract.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from langgraph_langchain.analysis_intelligence.constraints import (
    PlanningBlockedError,
    PlanRevisionLedger,
)


class ClaimType(str, Enum):
    FACT = "fact"
    OBSERVATION = "observation"
    HYPOTHESIS = "hypothesis"
    CONCLUSION = "conclusion"
    LIMITATION = "limitation"


class ClaimVerdict(str, Enum):
    SUPPORTED = "supported"
    WEAKENED = "weakened"
    REJECTED = "rejected"


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    metric_id: Optional[str] = None
    dimension_id: Optional[str] = None
    data_scope: str = ""
    value: Optional[float] = None
    method: str = ""
    source_execution_id: Optional[str] = None
    verified: bool = False


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    claim_type: ClaimType
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    metric_id: Optional[str] = None
    dimension_id: Optional[str] = None
    data_scope: str = ""
    method: str = ""
    value: Optional[float] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    limitations: list[str] = Field(default_factory=list)


class ClaimVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    verdict: ClaimVerdict
    final_type: ClaimType
    reasons: list[str] = Field(default_factory=list)


class ClaimVerifier:
    """Deterministic claim-evidence consistency checks."""

    def __init__(self, evidence: Mapping[str, EvidenceRecord]) -> None:
        self._evidence = evidence

    def verify(self, claim: Claim) -> ClaimVerification:
        reasons: list[str] = []
        final_type = claim.claim_type

        if claim.claim_type in (ClaimType.FACT, ClaimType.CONCLUSION):
            if not claim.evidence_ids:
                return ClaimVerification(
                    claim_id=claim.claim_id,
                    verdict=ClaimVerdict.REJECTED,
                    final_type=ClaimType.HYPOTHESIS if claim.claim_type is ClaimType.CONCLUSION else claim.claim_type,
                    reasons=[f"{claim.claim_type.value} claim has no evidence; rejected"],
                )
            missing = [e for e in claim.evidence_ids if e not in self._evidence]
            if missing:
                return ClaimVerification(
                    claim_id=claim.claim_id,
                    verdict=ClaimVerdict.REJECTED,
                    final_type=claim.claim_type,
                    reasons=[f"evidence not found: {missing}"],
                )
            for eid in claim.evidence_ids:
                record = self._evidence[eid]
                if not record.verified:
                    reasons.append(f"evidence {eid} is not verified; claim weakened")
                if claim.metric_id and record.metric_id and claim.metric_id != record.metric_id:
                    return ClaimVerification(
                        claim_id=claim.claim_id,
                        verdict=ClaimVerdict.REJECTED,
                        final_type=claim.claim_type,
                        reasons=[f"evidence {eid} metric mismatch: {record.metric_id} != {claim.metric_id}"],
                    )
                if claim.value is not None and record.value is not None:
                    if abs(claim.value - record.value) > 1e-9:
                        return ClaimVerification(
                            claim_id=claim.claim_id,
                            verdict=ClaimVerdict.REJECTED,
                            final_type=claim.claim_type,
                            reasons=[
                                f"claim value {claim.value} does not match evidence "
                                f"{eid} value {record.value}"
                            ],
                        )
                if claim.data_scope and record.data_scope and claim.data_scope not in record.data_scope:
                    return ClaimVerification(
                        claim_id=claim.claim_id,
                        verdict=ClaimVerdict.REJECTED,
                        final_type=claim.claim_type,
                        reasons=[
                            f"claim scope {claim.data_scope!r} is broader than "
                            f"evidence {eid} scope {record.data_scope!r}"
                        ],
                    )
            if claim.claim_type is ClaimType.CONCLUSION:
                if any(unverified in reasons for unverified in reasons if "not verified" in unverified):
                    return ClaimVerification(
                        claim_id=claim.claim_id,
                        verdict=ClaimVerdict.WEAKENED,
                        final_type=ClaimType.OBSERVATION,
                        reasons=[*reasons, "conclusion downgraded to observation"],
                    )
                if claim.method == "correlation" and not any(
                    "correlation" in lim for lim in claim.limitations
                ):
                    return ClaimVerification(
                        claim_id=claim.claim_id,
                        verdict=ClaimVerdict.WEAKENED,
                        final_type=ClaimType.OBSERVATION,
                        reasons=[
                            "correlation-based conclusion requires an explicit "
                            "correlation-is-not-causality limitation; downgraded",
                        ],
                    )
        elif claim.claim_type is ClaimType.HYPOTHESIS and claim.evidence_ids:
            reasons.append("hypothesis retained with supporting observations; not promoted")
        if reasons:
            verdict = (
                ClaimVerdict.WEAKENED
                if final_type is not claim.claim_type
                else ClaimVerdict.SUPPORTED
            )
            return ClaimVerification(claim_id=claim.claim_id, verdict=verdict, final_type=final_type, reasons=reasons)
        return ClaimVerification(claim_id=claim.claim_id, verdict=ClaimVerdict.SUPPORTED, final_type=final_type)


class TaskOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: str
    value: Optional[float] = None
    evidence_ids: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    retryable: bool = False


class NextAction(str, Enum):
    EXECUTE = "execute"
    VERIFY = "verify"
    REVISE = "revise"
    PARTIAL_RESULT = "partial_result"
    ASK_USER = "ask_user"
    FINALIZE = "finalize"
    STOP = "stop"


class AnalysisState:
    """Multi-turn orchestration state with an explicit termination contract.

    Coordinates the P1-P4 components: the intent is never rewritten, revisions go
    through the bounded ledger, results are recorded once (no duplicate side
    effects), and claims are auto-verified at insertion.
    """

    def __init__(
        self,
        *,
        intent_user_goal: str,
        verifier: ClaimVerifier,
        revision_ledger: Optional[PlanRevisionLedger] = None,
    ) -> None:
        self.user_goal = intent_user_goal
        self.verifier = verifier
        self.revision_ledger = revision_ledger or PlanRevisionLedger(user_goal=intent_user_goal)
        self.results: dict[str, TaskOutcome] = {}
        self.claims: dict[str, ClaimVerification] = {}
        self.rejected_claims: list[str] = []
        self.completed_plan = False

    def record_result(self, outcome: TaskOutcome) -> None:
        if outcome.task_id in self.results:
            raise ValueError(f"task result already recorded: {outcome.task_id}")
        self.results[outcome.task_id] = outcome

    def add_claim(self, claim: Claim) -> ClaimVerification:
        if claim.claim_id in self.claims:
            raise ValueError(f"claim already evaluated: {claim.claim_id}")
        verification = self.verifier.verify(claim)
        self.claims[claim.claim_id] = verification
        if verification.verdict is ClaimVerdict.REJECTED:
            self.rejected_claims.append(claim.claim_id)
        return verification

    def mark_plan_complete(self) -> None:
        self.completed_plan = True

    def decide_next(self) -> NextAction:
        failures = [r for r in self.results.values() if r.status == "failed"]
        if self.revision_ledger.exhausted and failures:
            return NextAction.STOP
        if failures:
            retryable = any(f.retryable for f in failures)
            if retryable and not self.revision_ledger.exhausted:
                return NextAction.REVISE
            if self.results and any(r.status == "succeeded" for r in self.results.values()):
                return NextAction.PARTIAL_RESULT
            return NextAction.STOP
        if not self.completed_plan:
            return NextAction.EXECUTE
        unverified_conclusions = [
            claim_id
            for claim_id, verification in self.claims.items()
            if verification.final_type is None
        ]
        if self.claims and self.rejected_claims and len(self.rejected_claims) == len(self.claims):
            return NextAction.PARTIAL_RESULT
        if not self.claims:
            return NextAction.VERIFY
        return NextAction.FINALIZE
