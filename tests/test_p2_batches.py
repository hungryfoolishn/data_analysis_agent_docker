import json

import pytest

from langgraph_langchain.evaluation.release_candidate import ReleaseCandidateInput, assess_release_candidate
from langgraph_langchain.runtime.context import AnalysisRuntime
from langgraph_langchain.runtime.sqlite_store import SQLiteMetadataStore, VersionConflictError
from langgraph_langchain.security import redact_sensitive, sensitive_columns, verify_bearer_token
from langgraph_langchain.schemas import RunMetrics
from langgraph_langchain.error_classifier import FailoverReason, classify_api_error


def test_sqlite_store_persists_runtime_and_events(tmp_path):
    workspace=tmp_path/"session"
    runtime=AnalysisRuntime(workspace_dir=workspace,session_id="session",question="test")
    store=SQLiteMetadataStore(tmp_path/".analysis_metadata.sqlite")
    loaded=store.get_run(runtime.run.run_id)
    assert loaded["task"]["session_id"]=="session"
    assert store.events(runtime.run.run_id)
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM analysis_tasks").fetchone()[0] == 1


def test_sqlite_store_optimistic_version_and_migration(tmp_path):
    store=SQLiteMetadataStore(tmp_path/"state.sqlite")
    snapshot={"task":{"task_id":"task_1","session_id":"s","question":"q","input_asset_ids":[],"constraints":{},"external_context":None,"created_at":"2026-01-01T00:00:00+00:00"},"run":{"run_id":"run_"+"1"*32,"task_id":"task_1","plan_version":1,"status":"running","plan_status":"active","current_step_id":None,"steps":[],"created_at":"2026-01-01T00:00:00+00:00","updated_at":"2026-01-01T00:00:00+00:00","parent_run_id":None,"retry_of_step_id":None,"attempt":1,"pause_reason":None,"paused_at":None,"plan_revisions":[],"plan_confirmation":None,"plan":None},"assets":[],"executions":[],"artifacts":[],"findings":[],"metric_definitions":[],"assumptions":[]}
    assert store.save_snapshot(snapshot)==1
    with pytest.raises(VersionConflictError):
        store.save_snapshot(snapshot,expected_version=0)
    archive=tmp_path/"s"/".analysis_runs"; archive.mkdir(parents=True)
    (archive/(snapshot["run"]["run_id"]+".json")).write_text(json.dumps(snapshot),encoding="utf-8")
    assert store.migrate_workspace(tmp_path)==1


def test_security_token_redaction_and_sensitive_columns():
    assert verify_bearer_token("Bearer secret","secret")
    assert not verify_bearer_token("Bearer wrong","secret")
    redacted=redact_sensitive({"api_key":"x","nested":{"email":"a@b.com"},"safe":1})
    assert redacted["api_key"]=="***REDACTED***"
    assert redacted["nested"]["email"]=="***REDACTED***"
    assert sensitive_columns(["name","email","手机号"])==["email","手机号"]


def test_release_candidate_accepts_only_complete_evidence():
    accepted=assess_release_candidate(ReleaseCandidateInput(evaluation_allowed=True,critical_quality_errors=0,tests_passed=520,tests_failed=0,recovery_checks_passed=True,package_hash_verified=True))
    assert accepted.accepted
    rejected=assess_release_candidate(ReleaseCandidateInput(evaluation_allowed=False,critical_quality_errors=1,tests_passed=1,tests_failed=2))
    assert not rejected.accepted
    assert len(rejected.reasons)>=4


def test_release_endpoint_registered():
    from langgraph_langchain.api_server_langgraph import app
    assert "/release/candidate/assess" in {route.path for route in app.routes}


@pytest.mark.parametrize("failure_code", ["overloaded", "server_error", "rate_limit", "unknown"])
def test_transient_provider_failure_codes_are_valid_metrics(failure_code):
    metrics = RunMetrics(
        session_id="session",
        start_time="2026-01-01T00:00:00+00:00",
        final_status="failed",
        failure_code=failure_code,
    )
    assert metrics.failure_code == failure_code


def test_task_public_response_never_exposes_worker_or_events():
    from langgraph_langchain.api_server_langgraph import _task_public

    assert _task_public({"task_id": "task_1", "events": [1], "_worker": object()}) == {
        "task_id": "task_1"
    }


def test_provider_concurrency_limit_is_retryable_rate_limit():
    classified = classify_api_error(RuntimeError("Concurrency limit exceeded for user"))
    assert classified.reason is FailoverReason.rate_limit
    assert classified.is_retryable
