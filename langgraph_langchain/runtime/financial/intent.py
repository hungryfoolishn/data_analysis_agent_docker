"""Explicit Financial intent resolution (B10.6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

__all__ = ["FinancialIntent", "FinancialIntentError", "resolve_financial_intent"]


class FinancialIntentError(ValueError):
    """Raised when a Financial intent is ambiguous or incomplete."""


@dataclass(frozen=True)
class FinancialIntent:
    market: str
    entity_id: str
    statement_type: str
    fiscal_year: int
    fiscal_period: str
    reporting_basis: str
    currency: str
    metric_id: str
    market_scope: str = "any"

    def to_parameters(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "entity_id": self.entity_id,
            "statement_type": self.statement_type,
            "fiscal_year": self.fiscal_year,
            "fiscal_period": self.fiscal_period,
            "reporting_basis": self.reporting_basis,
            "currency": self.currency,
            "metric_id": self.metric_id,
            "market_scope": self.market_scope,
        }


def resolve_financial_intent(request: Mapping[str, Any]) -> FinancialIntent:
    """Resolve an explicitly structured request without fuzzy parsing."""
    required = (
        "market",
        "entity_id",
        "statement_type",
        "fiscal_year",
        "fiscal_period",
        "reporting_basis",
        "currency",
        "metric_id",
    )
    missing = [field for field in required if request.get(field) in (None, "")]
    if missing:
        raise FinancialIntentError(
            "financial intent requires explicit fields: " + ", ".join(missing)
        )
    market = str(request["market"]).lower()
    if market not in {"us", "cn"}:
        raise FinancialIntentError(f"unsupported market: {market!r}")
    market_scope = str(request.get("market_scope") or market)
    if market_scope not in {"any", "us", "cn"}:
        raise FinancialIntentError(f"unsupported market scope: {market_scope!r}")
    try:
        fiscal_year = int(request["fiscal_year"])
    except (TypeError, ValueError) as exc:
        raise FinancialIntentError("fiscal_year must be an integer") from exc
    return FinancialIntent(
        market=market,
        entity_id=str(request["entity_id"]),
        statement_type=str(request["statement_type"]),
        fiscal_year=fiscal_year,
        fiscal_period=str(request["fiscal_period"]),
        reporting_basis=str(request["reporting_basis"]),
        currency=str(request["currency"]),
        metric_id=str(request["metric_id"]),
        market_scope=market_scope,
    )
