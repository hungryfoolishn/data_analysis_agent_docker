"""Unit tests for the Docker worker JSON protocol (WP10.1)."""

import json

import pytest

from langgraph_langchain.execution.docker_worker_protocol import (
    PROTOCOL_VERSION,
    DockerJobEnvelope,
    DockerResultEnvelope,
    ProtocolProtocolError,
)
from langgraph_langchain.execution.job_contract import (
    ArtifactPolicy,
    WorkerExecutionOptions,
    WorkerJob,
)


def make_job(**overrides) -> WorkerJob:
    defaults = dict(
        code="result = sum([1, 2, 3, 4, 5])\n",
        workspace_dir="/tmp/ws",
        execution_options=WorkerExecutionOptions(timeout_seconds=10),
    )
    defaults.update(overrides)
    return WorkerJob(**defaults)


def make_envelope_json(job: WorkerJob) -> bytes:
    return DockerJobEnvelope(protocol_version=PROTOCOL_VERSION, job=job).to_json_bytes()


class TestJobEnvelope:
    def test_valid_envelope_roundtrip(self):
        job = make_job()
        raw = make_envelope_json(job)
        envelope = DockerJobEnvelope.from_json_bytes(raw)
        assert envelope.job.job_id == job.job_id
        assert envelope.kind == "job"
