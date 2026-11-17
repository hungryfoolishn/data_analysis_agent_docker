"""B10.9.2 asset-backed financial runtime factory.

Bridges the gap between the B8 AcquisitionService (DataAsset/SnapshotStore)
and the B10.6 FinancialDomainRuntime. Reads a persisted DataAsset snapshot
and constructs a FinancialContext suitable for platform-verified execution.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from langgraph_langchain.data.assets import DataAsset

from .context import FinancialContext
from .intent import resolve_financial_intent
from .models import FinancialLineItem, FinancialStatement, FinancialStatisticKind, FinancialUnit

__all__ = [
    "FinancialAssetBridgeError",
    "load_financial_context_from_snapshot",
]

# Canonical concepts that are per-share metrics
_PER_SHARE_CONCEPTS = {"eps_basic", "eps_diluted"}


class FinancialAssetBridgeError(ValueError):
    """Raised when a DataAsset snapshot cannot be converted to a FinancialContext."""


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise FinancialAssetBridgeError(f"non-decimal value in snapshot: {value!r}") from exc


def load_financial_context_from_snapshot(
    *,
    snapshot_path: str | Path,
    asset: DataAsset,
    entity_id: str,
    statement_type: str,
    fiscal_year: int,
    fiscal_period: str,
    reporting_basis: str = "consolidated",
    currency: str = "CNY",
    metric_id: str,
) -> FinancialContext:
    """Read a persisted snapshot parquet and build a FinancialContext.

    The snapshot must have the standard acquisition columns including
    ``canonical_concept``, ``value``, ``statement_type``, ``period_type``,
    ``period_start``, ``period_end`` and ``source_document_id``.
    """
    import pandas as pd

    snap_path = Path(snapshot_path)
    if not snap_path.exists():
        raise FinancialAssetBridgeError(f"snapshot not found: {snap_path}")
    frame = pd.read_parquet(snap_path)
    if frame.empty:
        raise FinancialAssetBridgeError("snapshot has no rows")
    filtered = frame[
        (frame["statement_type"] == statement_type)
        & (frame["entity_id"] == entity_id)
        & (frame["fiscal_year"] == fiscal_year)
        & (frame["fiscal_period"] == fiscal_period)
    ]
    if filtered.empty:
        raise FinancialAssetBridgeError(
            f"no rows for {entity_id}/{statement_type}/{fiscal_year}/{fiscal_period}"
        )
    period_type = "instant" if statement_type == "balance" else "duration"
    period_end = str(filtered.iloc[0]["period_end"])
    period_start = str(filtered.iloc[0]["period_start"]) if period_type == "duration" else None
    accession = str(filtered.iloc[0]["accession_number"]) if "accession_number" in filtered.columns else None
    source_doc = str(filtered.iloc[0]["source_document_id"]) if "source_document_id" in filtered.columns else None

    items: list[FinancialLineItem] = []
    seen_concepts: set[str] = set()
    for _, row in filtered.iterrows():
        concept = str(row["canonical_concept"])
        if concept in seen_concepts:
            continue
        seen_concepts.add(concept)
        value = _decimal(row["value"])
        kind = "per_share" if concept in _PER_SHARE_CONCEPTS else "absolute_amount"
        unit = FinancialUnit(
            statistic_kind=kind,
            numeric_unit=currency,
            currency=currency,
            unit_denominator="share" if kind == "per_share" else None,
        )
        items.append(FinancialLineItem(
            canonical_concept=concept,
            value=value,
            statistic_kind=kind,
            unit=unit,
        ))
    statement = FinancialStatement(
        entity_id=entity_id,
        statement_type=statement_type,
        period_type=period_type,
        period_start=period_start,
        period_end=period_end,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        reporting_basis=reporting_basis,
        currency=currency,
        accession_number=accession,
        source_document_id=source_doc,
        line_items=tuple(items),
    )
    intent = resolve_financial_intent({
        "market": "cn",
        "entity_id": entity_id,
        "statement_type": statement_type,
        "fiscal_year": fiscal_year,
        "fiscal_period": fiscal_period,
        "reporting_basis": reporting_basis,
        "currency": currency,
        "metric_id": metric_id,
        "market_scope": "cn",
    })
    return FinancialContext(
        intent=intent,
        statements=[statement],
        input_asset_ids=(asset.asset_id,),
    )
