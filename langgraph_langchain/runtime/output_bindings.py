"""Authoritative output asset binding registry for Runtime V2.

Implements the B11.2.1.0 v3 frozen design (§3.2, §4):

- ``OutputAssetBinding`` uniqueness key ``(run_id, step_id, output_name, attempt)``;
- idempotent registration, all-or-nothing batch registration;
- exact-attempt resolution (no cross-run / cross-attempt inheritance);
- the shared physical-ID / logical-reference classifier used by both plan
  validation and execution request building.
"""

from __future__ import annotations

import re
from typing import Iterable, Literal, Optional, Sequence

from langgraph_langchain.schemas import OutputAssetBinding

__all__ = [
    "OutputBindingConflictError",
    "OutputBindingNotFoundError",
    "OutputBindingRegistry",
    "classify_input_reference",
    "parse_logical_reference",
]

# Candidate logical reference shape: ``{step_id}.{output_name}`` (v3 §3.2).
_LOGICAL_REF_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")

# New plan step IDs must match this format (v3 §3.2).
STEP_ID_PATTERN = re.compile(r"^step_[0-9a-f]{32}$")


class OutputBindingConflictError(ValueError):
    """Raised when a binding registration would overwrite different content."""


class OutputBindingNotFoundError(KeyError):
    """Raised when a logical output reference cannot be resolved."""


def parse_logical_reference(reference: str) -> tuple[str, str]:
    """Split a candidate logical reference into ``(step_id, output_name)``."""
    step_id, separator, output_name = reference.partition(".")
    if not separator or not step_id or not output_name:
        raise OutputBindingConflictError(
            f"Invalid logical output reference: {reference!r}"
        )
    if STEP_ID_PATTERN.match(step_id) is None:
        raise OutputBindingConflictError(
            f"Logical reference step ID must match step_[0-9a-f]{{32}}: {reference!r}"
        )
    return step_id, output_name


def classify_input_reference(
    value: str,
    known_step_ids: Iterable[str],
    registered_asset_ids: Iterable[str] = (),
) -> Literal["asset_id", "logical_ref"]:
    """Classify one input value as a physical asset ID or a logical reference.

    A value is a logical reference only when it matches the candidate syntax
    AND its ``step_id`` segment belongs to the current plan's known step IDs.
    A value that is simultaneously a registered physical asset ID and a
    candidate logical reference is ambiguous and raises
    :class:`OutputBindingConflictError` (v3 §3.2 rule 3).
    """
    if not isinstance(value, str) or not value:
        return "asset_id"
    if _LOGICAL_REF_PATTERN.match(value) is None:
        return "asset_id"
    step_id = value.partition(".")[0]
    if step_id not in set(known_step_ids):
        return "asset_id"
    if value in set(registered_asset_ids):
        raise OutputBindingConflictError(
            f"Ambiguous input reference {value!r}: it is both a registered "
            "asset ID and a logical output reference"
        )
    return "logical_ref"


class OutputBindingRegistry:
    """In-memory authoritative registry owned by one ``AnalysisRuntime``.

    The registry intentionally holds only bindings for the current run; the
    run snapshot (``.analysis_runs/{run_id}.json``) is the durable record and
    historical attempts stay immutable in their own archive files.
    """

    def __init__(self) -> None:
        self._bindings: dict[tuple[str, str, str, int], OutputAssetBinding] = {}

    @staticmethod
    def _key(binding: OutputAssetBinding) -> tuple[str, str, str, int]:
        return (binding.run_id, binding.step_id, binding.output_name, binding.attempt)

    def register(self, binding: OutputAssetBinding) -> OutputAssetBinding:
        """Register one binding; identical content is idempotent, conflicts raise."""
        key = self._key(binding)
        existing = self._bindings.get(key)
        if existing is not None:
            if (
                existing.asset_id == binding.asset_id
                and existing.execution_id == binding.execution_id
            ):
                return existing
            raise OutputBindingConflictError(
                "Output binding conflict for "
                f"(run={binding.run_id}, step={binding.step_id}, "
                f"output={binding.output_name}, attempt={binding.attempt}): "
                f"existing asset={existing.asset_id} execution={existing.execution_id}, "
                f"new asset={binding.asset_id} execution={binding.execution_id}"
            )
        self._bindings[key] = binding
        return binding

    def register_many(
        self, bindings: Sequence[OutputAssetBinding]
    ) -> list[OutputAssetBinding]:
        """Register a batch atomically: any conflict leaves the registry untouched."""
        staged: dict[tuple[str, str, str, int], OutputAssetBinding] = {}
        for binding in bindings:
            key = self._key(binding)
            staged_existing = staged.get(key)
            if staged_existing is not None:
                if (
                    staged_existing.asset_id == binding.asset_id
                    and staged_existing.execution_id == binding.execution_id
                ):
                    continue
                raise OutputBindingConflictError(
                    "Duplicate conflicting binding inside one batch for "
                    f"(run={binding.run_id}, step={binding.step_id}, "
                    f"output={binding.output_name}, attempt={binding.attempt})"
                )
            existing = self._bindings.get(key)
            if existing is not None and (
                existing.asset_id != binding.asset_id
                or existing.execution_id != binding.execution_id
            ):
                raise OutputBindingConflictError(
                    "Output binding conflict for "
                    f"(run={binding.run_id}, step={binding.step_id}, "
                    f"output={binding.output_name}, attempt={binding.attempt})"
                )
            staged[key] = binding
        self._bindings.update(staged)
        return list(staged.values())

    def resolve(
        self,
        *,
        run_id: str,
        step_id: str,
        output_name: str,
        attempt: int,
    ) -> OutputAssetBinding:
        """Exact-match resolution; missing bindings raise (no silent fallback)."""
        binding = self._bindings.get((run_id, step_id, output_name, int(attempt)))
        if binding is None:
            raise OutputBindingNotFoundError(
                f"Output binding not found: run={run_id}, step={step_id}, "
                f"output={output_name}, attempt={attempt}"
            )
        return binding

    def resolve_for_step(
        self, *, run_id: str, step_id: str, attempt: int
    ) -> list[OutputAssetBinding]:
        return [
            binding
            for key, binding in sorted(self._bindings.items())
            if key[0] == run_id and key[1] == step_id and key[3] == int(attempt)
        ]

    def list_for_run(
        self, *, run_id: str, attempt: Optional[int] = None
    ) -> list[OutputAssetBinding]:
        return [
            binding
            for key, binding in sorted(self._bindings.items())
            if key[0] == run_id and (attempt is None or key[3] == int(attempt))
        ]

    def list_all(self) -> list[OutputAssetBinding]:
        return [binding for _, binding in sorted(self._bindings.items())]

    def discard(self, binding: OutputAssetBinding) -> None:
        self._bindings.pop(self._key(binding), None)

    def clear(self) -> None:
        """Drop every binding (used when a retry run rebuilds its registry)."""
        self._bindings.clear()

    def __len__(self) -> int:
        return len(self._bindings)
