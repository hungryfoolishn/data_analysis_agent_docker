"""Pytest configuration for the R0a-1A execution boundary.

The execution policy refuses untrusted (LLM/user-derived) code execution when
no worker backend is configured. The test suite explicitly opts into the
bounded process backend as a documented development configuration:

    WORKER_BACKEND=process
    ALLOW_UNTRUSTED_PROCESS_EXECUTION=1

This is NOT a security sandbox declaration: the process backend provides
reliability isolation (cleared environment, memory limit, file audit hook,
timeout, cancellation) but no OS/container isolation. Production must
configure ``WORKER_BACKEND`` explicitly (docker recommended, R0a-1B).
"""

import os

os.environ.setdefault("WORKER_BACKEND", "process")
os.environ.setdefault("ALLOW_UNTRUSTED_PROCESS_EXECUTION", "1")
