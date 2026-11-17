"""Financial Pack identity and contract (B10.2.0).

Declares the Financial domain pack's identity, capabilities and dependency
boundary for the platform ``DomainPackRegistry``.  This module contains NO
analysis logic — it is the registration surface only.  The Financial Pack
never imports Macro Pack internals (enforced by
``tests/test_financial_pack_contract.py`` via AST analysis) and no frozen
Macro code is modified to accommodate it.
"""

from __future__ import annotations

from langgraph_langchain.runtime.domain_packs import (
    DomainPackContract,
    DomainPackRegistry,
)

__all__ = [
    "FINANCIAL_PACK_ID",
    "FINANCIAL_PACK_VERSION",
    "build_financial_pack_contract",
    "register_financial_pack",
]

FINANCIAL_PACK_ID = "financial"
FINANCIAL_PACK_VERSION = "0.2.0"  # B10.2 scope: models + normalization base

# Import roots the Financial Pack forbids itself from importing (declared
# boundary; enforced by the contract tests, stored here as the declaration).
FINANCIAL_DEPENDENCY_BOUNDARY = (
    "langgraph_langchain.runtime.macro",
)


def build_financial_pack_contract() -> DomainPackContract:
    """Build the canonical B10.2 Financial Pack contract.

    Capabilities cover only what B10.2 delivers (canonical models +
    explicit normalization).  Intent / templates / metrics-catalog /
    runtime-integration capabilities are later B10 stages and are added to
    this contract as they land — never declared before they exist.
    """
    return DomainPackContract(
        pack_id=FINANCIAL_PACK_ID,
        pack_version=FINANCIAL_PACK_VERSION,
        domain="financial",
        supported_capabilities=(
            "financial_models",
            "financial_normalization",
        ),
        model_contract={
            "authority": "FinancialStatement / FinancialLineItem / Entity",
            "numeric_type": "Decimal",
            "period_semantics": ["instant", "duration"],
            "filing_metadata": [
                "filing_date",
                "accession_number",
                "source_document_id",
                "amendment",
            ],
            "legacy_compat": "explicit adapters; legacy models are never an "
            "authority for canonical data",
        },
        normalization_contract={
            "single_exit": "normalize_to_financial_line_item / "
            "normalize_financial_statement",
            "statistic_kinds": [
                "absolute_amount",
                "per_share",
                "percent_ratio",
                "times_ratio",
                "growth_rate",
                "yoy_index",
                "unknown",
            ],
            "unknown_policy": "reject (never guess)",
            "currency_policy": "preserve; no implicit FX conversion",
            "lineage": ["conversion_method", "normalization_version", "warnings"],
        },
        dependency_boundary=FINANCIAL_DEPENDENCY_BOUNDARY,
        registration_identity=FINANCIAL_PACK_ID,
    )


def register_financial_pack(
    registry: DomainPackRegistry | None = None,
) -> tuple[DomainPackRegistry, DomainPackContract]:
    """Register the Financial Pack contract (idempotent).

    A registry is created on first call when none is supplied; passing an
    explicit registry keeps a single application-wide registration point.
    """
    if registry is None:
        registry = DomainPackRegistry()
    contract = build_financial_pack_contract()
    registry.register(contract)
    return registry, contract
