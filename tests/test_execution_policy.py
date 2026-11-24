"""Unit tests for the unified execution policy (R0a-1A).

The policy must fail closed: no configuration, unknown configuration, or an
unqualified backend must refuse execution instead of silently selecting an
unsafe backend.
"""

import pytest

from langgraph_langchain.execution.execution_policy import (
    TRUSTED_INTERNAL,
    UNTRUSTED,
    ExecutionBackendNotConfiguredError,
    ResolvedExecutionBackend,
    UnknownExecutionBackendError,
    UnsafeExecutionBackendDeniedError,
    resolve_execution_backend,
)
from langgraph_langchain.execution.worker_backend import (
    DockerWorkerBackend,
    ProcessWorkerBackend,
)


class TestUnconfiguredEnvironment:
    def test_untrusted_code_is_refused_when_no_backend_is_configured(self):
        with pytest.raises(ExecutionBackendNotConfiguredError):
            resolve_execution_backend(UNTRUSTED, environ={})

    def test_trusted_internal_code_keeps_bounded_process_default(self):
        resolved = resolve_execution_backend(TRUSTED_INTERNAL, environ={})
        assert isinstance(resolved.backend, ProcessWorkerBackend)
        assert resolved.backend_name == "process"
        assert resolved.config_source == "default_trusted_only"
        assert resolved.insecure is False

    def test_whitespace_only_backend_value_is_treated_as_unconfigured(self):
        with pytest.raises(ExecutionBackendNotConfiguredError):
            resolve_execution_backend(UNTRUSTED, environ={"WORKER_BACKEND": "   "})


class TestProcessBackend:
    def test_untrusted_code_refused_without_explicit_opt_in(self):
        with pytest.raises(UnsafeExecutionBackendDeniedError):
            resolve_execution_backend(
                UNTRUSTED, environ={"WORKER_BACKEND": "process"}
            )

    def test_untrusted_code_allowed_with_explicit_opt_in(self):
        resolved = resolve_execution_backend(
            UNTRUSTED,
            environ={
                "WORKER_BACKEND": "process",
                "ALLOW_UNTRUSTED_PROCESS_EXECUTION": "1",
            },
        )
        assert isinstance(resolved.backend, ProcessWorkerBackend)
        assert resolved.sandboxed is False
        assert resolved.insecure is True

    def test_opt_in_flag_is_explicit_about_values(self):
        for value in ("0", "false", "no", "off", "", "yes please"):
            with pytest.raises(UnsafeExecutionBackendDeniedError):
                resolve_execution_backend(
                    UNTRUSTED,
                    environ={
                        "WORKER_BACKEND": "process",
                        "ALLOW_UNTRUSTED_PROCESS_EXECUTION": value,
                    },
                )

    def test_trusted_code_does_not_need_the_opt_in_flag(self):
        resolved = resolve_execution_backend(
            TRUSTED_INTERNAL, environ={"WORKER_BACKEND": "process"}
        )
        assert isinstance(resolved.backend, ProcessWorkerBackend)
        assert resolved.insecure is False


class TestDockerBackend:
    def test_untrusted_code_routes_to_docker_without_extra_flags(self):
        resolved = resolve_execution_backend(
            UNTRUSTED, environ={"WORKER_BACKEND": "docker"}
        )
        assert isinstance(resolved.backend, DockerWorkerBackend)
        assert resolved.backend_name == "docker"
        assert resolved.sandboxed is True
        assert resolved.insecure is False

    def test_docker_backend_selection_is_case_insensitive(self):
        resolved = resolve_execution_backend(
            UNTRUSTED, environ={"WORKER_BACKEND": "Docker"}
        )
        assert resolved.backend_name == "docker"


class TestUnknownBackend:
    def test_unknown_backend_value_fails_closed(self):
        for value in ("inline", "eval", "subprocess", "host", "DOCKER-INLINE"):
            with pytest.raises(UnknownExecutionBackendError):
                resolve_execution_backend(UNTRUSTED, environ={"WORKER_BACKEND": value})

    def test_unknown_backend_never_returns_a_backend(self):
        with pytest.raises(UnknownExecutionBackendError):
            resolve_execution_backend(TRUSTED_INTERNAL, environ={"WORKER_BACKEND": "mystery"})


class TestResolvedContract:
    def test_resolved_record_is_frozen(self):
        resolved = resolve_execution_backend(
            UNTRUSTED,
            environ={
                "WORKER_BACKEND": "process",
                "ALLOW_UNTRUSTED_PROCESS_EXECUTION": "1",
            },
        )
        assert isinstance(resolved, ResolvedExecutionBackend)
        with pytest.raises(Exception):
            resolved.backend_name = "docker"
