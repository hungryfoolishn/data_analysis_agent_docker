"""P5 evidence reasoning & orchestration tests."""
from __future__ import annotations

import pytest

from langgraph_langchain.analysis_intelligence.reasoning import (
    AnalysisState,
    Claim,
    ClaimType,
    ClaimVerdict,
    ClaimVerifier,
    EvidenceRecord,
    NextAction,
    TaskOutcome,
)


@pytest.fixture()
def evidence() -> dict:
    return {
        "ev_1": EvidenceRecord(
            evidence_id="ev_1", metric_id="monthly_revenue", dimension_id="department",
            data_scope="department=杭州开发一部", value=491744.0, method="group_aggregate",
            source_execution_id="exec_1", verified=True,
        ),
        "ev_2": EvidenceRecord(
            evidence_id="ev_2", metric_id="monthly_revenue", dimension_id="department",
            data_scope="department=杭州开发一部", value=491744.0, method="group_aggregate",
            source_execution_id="exec_1", verified=False,
        ),
        "ev_corr": EvidenceRecord(
            evidence_id="ev_corr", metric_id="monthly_revenue", data_scope="all",
            value=None, method="correlation", verified=True,
        ),
    }


def test_fact_with_verified_evidence_supported(evidence):
    verifier = ClaimVerifier(evidence)
    claim = Claim(
        claim_id="c1", claim_type=ClaimType.FACT, text="杭州开发一部1月收入为491744",
        evidence_ids=["ev_1"], metric_id="monthly_revenue", data_scope="department=杭州开发一部",
        value=491744.0, confidence=1.0,
    )
    v = verifier.verify(claim)
    assert v.verdict is ClaimVerdict.SUPPORTED and v.final_type is ClaimType.FACT


def test_numeric_mismatch_rejects_claim(evidence):
    verifier = ClaimVerifier(evidence)
    claim = Claim(
        claim_id="c2", claim_type=ClaimType.FACT, text="收入为999999",
        evidence_ids=["ev_1"], metric_id="monthly_revenue", value=999999.0,
    )
    v = verifier.verify(claim)
    assert v.verdict is ClaimVerdict.REJECTED
    assert "does not match evidence" in v.reasons[0]


def test_unverified_evidence_downgrades_conclusion_to_observation(evidence):
    verifier = ClaimVerifier(evidence)
    claim = Claim(
        claim_id="c3", claim_type=ClaimType.CONCLUSION, text="收入为491744",
        evidence_ids=["ev_2"], metric_id="monthly_revenue", value=491744.0,
    )
    v = verifier.verify(claim)
    assert v.verdict is ClaimVerdict.WEAKENED
    assert v.final_type is ClaimType.OBSERVATION


def test_conclusion_without_evidence_rejected(evidence):
    verifier = ClaimVerifier(evidence)
    claim = Claim(claim_id="c4", claim_type=ClaimType.CONCLUSION, text="收入很高")
    v = verifier.verify(claim)
    assert v.verdict is ClaimVerdict.REJECTED


def test_correlation_conclusion_without_causality_limitation_downgraded(evidence):
    verifier = ClaimVerifier(evidence)
    claim = Claim(
        claim_id="c5", claim_type=ClaimType.CONCLUSION, text="A 导致 B",
        evidence_ids=["ev_corr"], method="correlation",
    )
    v = verifier.verify(claim)
    assert v.verdict is ClaimVerdict.WEAKENED
    assert "correlation" in v.reasons[0]
    claim_ok = claim.model_copy(update={"limitations": ["correlation is not causality"]})
    assert verifier.verify(claim_ok).verdict is ClaimVerdict.SUPPORTED


def test_scope_mismatch_rejected(evidence):
    verifier = ClaimVerifier(evidence)
    claim = Claim(
        claim_id="c6", claim_type=ClaimType.FACT, text="全公司收入为491744",
        evidence_ids=["ev_1"], metric_id="monthly_revenue", data_scope="company_wide",
        value=491744.0,
    )
    v = verifier.verify(claim)
    assert v.verdict is ClaimVerdict.REJECTED
    assert "broader than" in v.reasons[0]


def test_metric_mismatch_rejected(evidence):
    verifier = ClaimVerifier(evidence)
    claim = Claim(
        claim_id="c7", claim_type=ClaimType.FACT, text="订单数为491744",
        evidence_ids=["ev_1"], metric_id="order_count", value=491744.0,
    )
    v = verifier.verify(claim)
    assert v.verdict is ClaimVerdict.REJECTED


def test_state_records_results_once():
    state = AnalysisState(intent_user_goal="g", verifier=ClaimVerifier({}))
    state.record_result(TaskOutcome(task_id="t1", status="succeeded", value=10))
    with pytest.raises(ValueError, match="already recorded"):
        state.record_result(TaskOutcome(task_id="t1", status="succeeded", value=10))


def test_scenario_a_basic_metric_no_over_exploration():
    state = AnalysisState(intent_user_goal="上个月的收入是多少", verifier=ClaimVerifier({}))
    state.record_result(TaskOutcome(task_id="t_sum", status="succeeded", value=100.0))
    state.mark_plan_complete()  # single-task plan fully executed
    assert state.decide_next() is NextAction.VERIFY


def test_scenario_failure_retryable_revise_then_bounded_stop():
    state = AnalysisState(intent_user_goal="g", verifier=ClaimVerifier({}))
    state.record_result(TaskOutcome(task_id="t_fail", status="failed", error="timeout", retryable=True))
    assert state.decide_next() is NextAction.REVISE
    for i in range(3):
        state.revision_ledger.record(
            reason=f"retry {i}", trigger_evidence=["timeout"], previous_plan_id="p",
            new_plan_id=f"p{i + 1}", changed_task_ids=[f"t{i}"], preserved_result_task_ids=[],
        )
    assert state.revision_ledger.exhausted
    assert state.decide_next() is NextAction.STOP


def test_partial_result_preserved_when_non_retryable_failure():
    state = AnalysisState(intent_user_goal="g", verifier=ClaimVerifier({}))
    state.record_result(TaskOutcome(task_id="t_ok", status="succeeded", value=5))
    state.record_result(TaskOutcome(task_id="t_bad", status="failed", error="bad input", retryable=False))
    assert state.decide_next() is NextAction.PARTIAL_RESULT


def test_scenario_finalize_after_verified_claims(evidence):
    state = AnalysisState(intent_user_goal="g", verifier=ClaimVerifier(evidence))
    state.record_result(TaskOutcome(task_id="t1", status="succeeded", value=491744.0, evidence_ids=["ev_1"]))
    state.mark_plan_complete()
    assert state.decide_next() is NextAction.VERIFY
    claim = Claim(
        claim_id="cl1", claim_type=ClaimType.CONCLUSION, text="1月收入491744",
        evidence_ids=["ev_1"], metric_id="monthly_revenue", value=491744.0,
    )
    v = state.add_claim(claim)
    assert v.verdict is ClaimVerdict.SUPPORTED
    assert state.decide_next() is NextAction.FINALIZE


def test_rejected_claims_force_partial_result(evidence):
    state = AnalysisState(intent_user_goal="g", verifier=ClaimVerifier(evidence))
    state.mark_plan_complete()
    state.add_claim(Claim(claim_id="bad", claim_type=ClaimType.CONCLUSION, text="无证据结论"))
    assert state.decide_next() is NextAction.PARTIAL_RESULT
    assert state.rejected_claims == ["bad"]


def test_duplicate_claim_rejected():
    state = AnalysisState(intent_user_goal="g", verifier=ClaimVerifier({}))
    state.add_claim(Claim(claim_id="dup", claim_type=ClaimType.OBSERVATION, text="x"))
    with pytest.raises(ValueError, match="already evaluated"):
        state.add_claim(Claim(claim_id="dup", claim_type=ClaimType.OBSERVATION, text="x"))
