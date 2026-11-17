"""B10.2.0: Financial Pack Contract and registration tests.

Covers task §4.3: normal registration, duplicate behavior, invalid contract
rejection, Macro-Pack independence (AST-enforced), no impact on Macro Pack,
no import side effects on the platform.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from langgraph_langchain.runtime.domain_packs import (
    DomainPackContract,
    DomainPackRegistrationError,
    DomainPackRegistry,
)
from langgraph_langchain.runtime.financial.pack import (
    FINANCIAL_DEPENDENCY_BOUNDARY,
    FINANCIAL_PACK_ID,
    build_financial_pack_contract,
    register_financial_pack,
)

_FINANCIAL_ROOTS = [
    Path("langgraph_langchain") / "runtime" / "financial",
    Path("langgraph_langchain") / "runtime" / "financial_agent",
]


# ---------------------------------------------------------------------------
# §4.3-1: normal registration
# ---------------------------------------------------------------------------


def test_financial_pack_registers_normally():
    registry, contract = register_financial_pack()
    assert registry.has(FINANCIAL_PACK_ID)
    assert registry.get(FINANCIAL_PACK_ID) is contract
    assert contract.pack_id == "financial"
    assert contract.domain == "financial"
    assert "financial_models" in contract.supported_capabilities
    assert "financial_normalization" in contract.supported_capabilities
    # contract describes model + normalization semantics
    assert contract.model_contract["numeric_type"] == "Decimal"
    assert "instant" in contract.model_contract["period_semantics"]
    assert contract.normalization_contract["unknown_policy"] == "reject (never guess)"


def test_registration_idempotent_for_identical_contract():
    registry = DomainPackRegistry()
    first = build_financial_pack_contract()
    second = build_financial_pack_contract()
    registry.register(first)
    registry.register(second)
    assert len(registry) == 1


def test_explicit_registry_is_a_single_registration_point():
    registry = DomainPackRegistry()
    returned_registry, contract = register_financial_pack(registry)
    assert returned_registry is registry
    assert len(registry) == 1
    assert contract.pack_id == "financial"


# ---------------------------------------------------------------------------
# §4.3-2: duplicate registration behavior
# ---------------------------------------------------------------------------


def test_duplicate_registration_different_version_conflicts():
    registry, contract = register_financial_pack()
    bumped = contract.model_copy(update={"pack_version": "9.9.9"})
    with pytest.raises(DomainPackRegistrationError, match="conflict"):
        registry.register(bumped)


def test_duplicate_registration_different_content_conflicts():
    registry, contract = register_financial_pack()
    mutated = contract.model_copy(
        update={"supported_capabilities": ("financial_models",)}
    )
    with pytest.raises(DomainPackRegistrationError):
        registry.register(mutated)


# ---------------------------------------------------------------------------
# §4.3-3: invalid contracts are rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"pack_id": "Bad-ID"},
        {"pack_id": ""},
        {"pack_version": "1.0"},
        {"pack_version": "not-semver"},
        {"domain": "Financial"},
        {"domain": ""},
        {"supported_capabilities": ()},
        {"supported_capabilities": (" padded ",)},
    ],
)
def test_invalid_contracts_rejected(overrides):
    # Constructor path: model_copy(update=…) skips validation by design, so
    # invalid contracts must be exercised through __init__ (the path
    # register() actually validates).
    from langgraph_langchain.runtime.financial.pack import (
        FINANCIAL_DEPENDENCY_BOUNDARY,
    )

    valid = build_financial_pack_contract()
    fields = {
        "pack_id": valid.pack_id,
        "pack_version": valid.pack_version,
        "domain": valid.domain,
        "supported_capabilities": valid.supported_capabilities,
        "model_contract": dict(valid.model_contract),
        "normalization_contract": dict(valid.normalization_contract),
        "dependency_boundary": FINANCIAL_DEPENDENCY_BOUNDARY,
        "registration_identity": valid.registration_identity,
    }
    fields.update(overrides)
    with pytest.raises(ValidationError):
        DomainPackContract(**fields)


def test_non_contract_object_rejected():
    registry = DomainPackRegistry()
    with pytest.raises(DomainPackRegistrationError, match="DomainPackContract"):
        registry.register({"pack_id": "financial"})  # type: ignore[arg-type]


def test_unregistered_pack_lookup_raises():
    registry = DomainPackRegistry()
    with pytest.raises(KeyError):
        registry.get("financial")


# ---------------------------------------------------------------------------
# §4.3-4: Financial Pack does not depend on Macro Pack (AST-enforced)
# ---------------------------------------------------------------------------


def _iter_financial_python_files():
    for root in _FINANCIAL_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


def test_financial_pack_declares_macro_boundary():
    contract = build_financial_pack_contract()
    assert "langgraph_langchain.runtime.macro" in contract.dependency_boundary
    assert FINANCIAL_DEPENDENCY_BOUNDARY == contract.dependency_boundary


def test_financial_modules_never_import_macro_pack():
    """AST check over financial sources: no import of runtime.macro, ever.

    Declaration strings inside the contract (dependency_boundary) are data,
    not imports — AST inspection only sees actual Import/ImportFrom nodes.
    """
    offenders: list[str] = []
    for path in _iter_financial_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "runtime.macro" in alias.name or alias.name.endswith(".macro"):
                        offenders.append(f"{path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if "runtime.macro" in module or module.endswith(".macro"):
                    offenders.append(f"{path}: from {module} import …")
    assert offenders == [], "Financial Pack imports Macro internals: " + "; ".join(offenders)


# ---------------------------------------------------------------------------
# §4.3-5/6: no impact on Macro Pack / no import side effects
# ---------------------------------------------------------------------------


def test_registry_is_domain_neutral_and_macro_is_absent():
    """The platform registry registers nothing on import; Macro Pack has no
    contract here and registering Financial must not touch Macro behavior."""
    fresh = DomainPackRegistry()
    assert len(fresh) == 0  # importing platform modules registered nothing
    register_financial_pack(fresh)
    assert [item.pack_id for item in fresh.list_contracts()] == ["financial"]
    # frozen Macro code is untouched: catalog still loads, templates intact
    from langgraph_langchain.runtime.macro.models import MacroIndicatorCatalog
    from langgraph_langchain.runtime.macro.templates import list_templates

    catalog = MacroIndicatorCatalog.load(
        Path("langgraph_langchain") / "runtime" / "macro" / "catalog.json"
    )
    assert len(catalog.indicators) == 13
    assert "cross_country_comparison" in set(list_templates())


def test_contract_model_is_frozen():
    contract = build_financial_pack_contract()
    with pytest.raises(ValidationError):
        contract.pack_id = "other"
