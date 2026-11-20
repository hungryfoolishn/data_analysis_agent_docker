import json

import pytest
from pydantic import ValidationError

from langgraph_langchain.execution.job_contract import (
    ArtifactPolicy,
    ArtifactReference,
    WorkerExecutionOptions,
    WorkerJob,
    new_job_id,
)


def artifact(**overrides):
    values = {
        "artifact_id": "asset_123",
        "artifact_type": "csv",
        "relative_path": "inputs/data.csv",
        "size_bytes": 10,
        "sha256": "a" * 64,
    }
    values.update(overrides)
    return ArtifactReference(**values)


def job(**overrides):
    values = {
        "code": "print('ok')",
        "workspace_dir": "/app/workspace/session-1",
        "source_path": "/app/temp_uploads/source.csv",
    }
    values.update(overrides)
    return WorkerJob(**values)


def test_valid_job_has_unique_job_id_and_safe_repr():
    item = job()
    assert item.job_id.startswith("job_")
    assert new_job_id() != new_job_id()
    assert "<redacted>" in repr(item)
    assert "print('ok')" not in repr(item)
    assert item.safe_payload()["code_length"] == len(item.code)
    assert "code" not in item.safe_payload()
    assert item.timeout_seconds == 60.0


def test_job_json_round_trip_preserves_contract_fields():
    item = job(
        session_id="session_1",
        task_id="task_1",
        execution_id="exec_1",
        correlation_id="trace_1",
        namespace={"name": "sales", "rows": 3},
        input_artifacts=(artifact(),),
    )
    payload = item.model_dump(mode="json")
    restored = WorkerJob.model_validate_json(json.dumps(payload))
    assert restored == item
    assert restored.input_artifacts[0].artifact_id == "asset_123"
    assert restored.namespace == {"name": "sales", "rows": 3}


def test_job_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        job(runtime_context={"object": "forbidden"})


def test_job_rejects_empty_code_and_invalid_job_id():
    with pytest.raises(ValidationError):
        job(code="")
    with pytest.raises(ValidationError, match="job_id"):
        job(job_id="bad id")


@pytest.mark.parametrize("seconds", [0, -1, 601])
def test_job_rejects_timeout_outside_bounds(seconds):
    with pytest.raises(ValidationError):
        WorkerExecutionOptions(timeout_seconds=seconds)


def test_artifact_reference_rejects_absolute_and_traversal_paths():
    with pytest.raises(ValidationError, match="must be relative"):
        artifact(relative_path="/etc/passwd")
    with pytest.raises(ValidationError, match="path traversal"):
        artifact(relative_path="../other/session.csv")
    with pytest.raises(ValidationError, match="must not be an absolute Windows path"):
        artifact(relative_path="C:/tmp/data.csv")
    with pytest.raises(ValidationError, match="normalized"):
        artifact(relative_path="inputs//data.csv")


def test_artifact_reference_rejects_invalid_hash_and_size():
    with pytest.raises(ValidationError, match="sha256"):
        artifact(sha256="A" * 64)
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        artifact(size_bytes=-1)


def test_job_rejects_input_artifacts_over_limit():
    options = WorkerExecutionOptions(max_artifacts=1)
    with pytest.raises(ValidationError, match="exceeds max_artifacts"):
        job(
            input_artifacts=(artifact(), artifact()),
            execution_options=options,
        )


def test_job_rejects_oversized_input_artifact():
    policy = ArtifactPolicy(max_artifact_bytes=5)
    with pytest.raises(ValidationError, match="exceeds max_artifact_bytes"):
        job(input_artifacts=(artifact(size_bytes=11),), output_policy=policy)


def test_job_rejects_non_json_namespace_objects():
    class RuntimeContext:
        pass

    with pytest.raises(ValidationError):
        job(namespace={"context": RuntimeContext()})
    with pytest.raises(ValidationError):
        job(namespace={"callable": lambda: None})


def test_execution_options_reject_secrets_and_large_environment():
    with pytest.raises(ValidationError, match="not allowed"):
        WorkerExecutionOptions(environment={"DEEPSEEK_API_KEY": "secret"})
    with pytest.raises(ValidationError, match="at most 32"):
        WorkerExecutionOptions(environment={f"VAR_{i}": str(i) for i in range(33)})


def test_output_policy_normalizes_and_rejects_bad_values():
    policy = ArtifactPolicy(allowed_extensions=("CSV", ".json", "csv"))
    assert policy.allowed_extensions == (".csv", ".json")
    with pytest.raises(ValidationError, match="simple file extensions"):
        ArtifactPolicy(allowed_extensions=("sub/dir.csv",))
    with pytest.raises(ValidationError, match="path traversal"):
        ArtifactPolicy(output_dir="../outside")
