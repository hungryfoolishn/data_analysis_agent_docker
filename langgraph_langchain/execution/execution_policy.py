"""Unified execution policy for dynamic code entry points (R0a-1A).

All product-reachable dynamic Python execution (``python_repl``, delegated
analysis, Runtime V2 python tasks) must resolve its worker backend through
this policy and dispatch through ``WorkerDispatcher``.  There is no
host-process inline execution and no automatic fallback between backends.

Trust classification
--------------------
``TRUSTED_INTERNAL``
    Fixed code shipped with the platform.  Never derived from user input,
    LLM output, or external data.

``UNTRUSTED``
    Code whose content originates from user requests, LLM output, or
    external data.  This is the classification used by every entry point
    that executes agent/LLM generated source text.

Backend selection (fail closed)
-------------------------------
``WORKER_BACKEND=docker``
    ``DockerWorkerBackend``.  Container, protocol, and lifecycle failures
    surface as failed jobs from the backend itself; they are never retried
    on another backend.

``WORKER_BACKEND=process``
    ``ProcessWorkerBackend`` (bounded child process: cleared environment,
    optional memory limit, file audit hook, timeout and cancellation).
    It provides process isolation for reliability -- it is NOT an
    OS/container security sandbox and must never be presented as one.
    Executing ``UNTRUSTED`` code on it additionally requires the explicit
    operator opt-in ``ALLOW_UNTRUSTED_PROCESS_EXECUTION=1``; otherwise the
    request is refused.

``WORKER_BACKEND`` unset
    No backend may be selected from missing configuration for untrusted
    code.  ``UNTRUSTED`` execution is refused with
    ``ExecutionBackendNotConfiguredError``.  ``TRUSTED_INTERNAL`` execution
    keeps a bounded process default so internal machinery (which never runs
    untrusted source text) keeps working.

Any other ``WORKER_BACKEND`` value is refused.  There is no silent
downgrade and no automatic fallback to an unsafe backend.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

from langgraph_langchain.execution.worker_backend import (
    DockerWorkerBackend,
    ProcessWorkerBackend,
    WorkerBackend,
)

TRUSTED_INTERNAL = "trusted_internal"
UNTRUSTED = "untrusted"

__all__ = [
    "TRUSTED_INTERNAL",
    "UNTRUSTED",
    "ExecutionPolicyError",
    "ExecutionBackendNotConfiguredError",
    "UnsafeExecutionBackendDeniedError",
    "UnknownExecutionBackendError",
    "ResolvedExecutionBackend",
    "resolve_execution_backend",
]


class ExecutionPolicyError(RuntimeError):
    """Base error for execution policy denials."""


class ExecutionBackendNotConfiguredError(ExecutionPolicyError):
    """No worker backend is configured; untrusted execution is refused."""


class UnsafeExecutionBackendDeniedError(ExecutionPolicyError):
    """The configured backend is not qualified for untrusted code."""


class UnknownExecutionBackendError(ExecutionPolicyError):
    """``WORKER_BACKEND`` contains an unknown value; fail closed."""


@dataclass(frozen=True)
class ResolvedExecutionBackend:
    """A policy-approved backend binding for one execution context."""

    backend: WorkerBackend
    backend_name: str
    trust: str
    config_source: str
    sandboxed: bool
    insecure: bool


def _flag_enabled(env: Mapping[str, str], name: str) -> bool:
    return str(env.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def resolve_execution_backend(
    trust: str,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> ResolvedExecutionBackend:
    """Resolve the configured worker backend for the requested trust level.

    Raises an ``ExecutionPolicyError`` subclass when the request cannot be
    satisfied safely.  Callers must treat any policy error as a hard
    refusal: no inline execution, no fallback to another backend.
    """
    env = os.environ if environ is None else environ
    name = str(env.get("WORKER_BACKEND", "")).strip().lower()

    if name == "docker":
        # Construction is lazy; engine problems surface as closed failures
        # from ``DockerWorkerBackend.execute`` (never as process fallback).
        return ResolvedExecutionBackend(
            backend=DockerWorkerBackend(),
            backend_name="docker",
            trust=trust,
            config_source="env",
            sandboxed=True,
            insecure=False,
        )

    if name == "process":
        allow_untrusted = _flag_enabled(env, "ALLOW_UNTRUSTED_PROCESS_EXECUTION")
        if trust == UNTRUSTED and not allow_untrusted:
            raise UnsafeExecutionBackendDeniedError(
                "WORKER_BACKEND=process is not qualified to execute untrusted "
                "(LLM/user-derived) code. Set WORKER_BACKEND=docker for sandboxed "
                "execution, or explicitly set ALLOW_UNTRUSTED_PROCESS_EXECUTION=1 "
                "to accept non-sandboxed execution in a development environment."
            )
        return ResolvedExecutionBackend(
            backend=ProcessWorkerBackend(),
            backend_name="process",
            trust=trust,
            config_source="env",
            sandboxed=False,
            insecure=(trust == UNTRUSTED),
        )

    if name == "":
        if trust == UNTRUSTED:
            raise ExecutionBackendNotConfiguredError(
                "WORKER_BACKEND is not configured; refusing to execute untrusted "
                "(LLM/user-derived) code. Set WORKER_BACKEND=docker (recommended) "
                "or WORKER_BACKEND=process with ALLOW_UNTRUSTED_PROCESS_EXECUTION=1 "
                "for explicit development use."
            )
        return ResolvedExecutionBackend(
            backend=ProcessWorkerBackend(),
            backend_name="process",
            trust=trust,
            config_source="default_trusted_only",
            sandboxed=False,
            insecure=False,
        )

    raise UnknownExecutionBackendError(
        f"Unknown WORKER_BACKEND value {name!r}; refusing to select a backend."
    )
