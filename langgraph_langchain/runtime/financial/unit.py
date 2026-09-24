"""Financial unit normalization for Runtime V9.3."""

from __future__ import annotations

import math
from dataclasses import dataclass


CALCULATED = "calculated"
UNAVAILABLE = "unavailable"
INVALID = "invalid"
NORMALIZED_UNIT = "CNY"


_UNIT_ALIASES = {
    "CNY": 1.0,
    "RMB": 1.0,
    "YUAN": 1.0,
    "元": 1.0,
    "CNY_THOUSAND": 1_000.0,
    "千元": 1_000.0,
    "WAN_YUAN": 10_000.0,
    "万元": 10_000.0,
    "CNY_TEN_THOUSAND": 10_000.0,
    "MILLION": 1_000_000.0,
    "CNY_MILLION": 1_000_000.0,
    "百万元": 1_000_000.0,
    "BILLION": 1_000_000_000.0,
    "CNY_BILLION": 1_000_000_000.0,
    "亿元": 1_000_000_000.0,
}


class UnitNormalizationError(ValueError):
    """Raised when a monetary unit cannot be normalized safely."""


@dataclass(frozen=True)
class UnitConversion:
    original_value: float | None
    original_unit: str
    normalized_value: float | None
    normalized_unit: str
    conversion_rule: str
    status: str
    reason: str | None = None


class UnitNormalizer:
    """Normalize supported CNY monetary units with conversion provenance."""

    def normalize(self, value: float | None, original_unit: str) -> UnitConversion:
        unit = original_unit.strip().upper()
        if value is None:
            return UnitConversion(
                original_value=None,
                original_unit=original_unit,
                normalized_value=None,
                normalized_unit=NORMALIZED_UNIT,
                conversion_rule="none",
                status=UNAVAILABLE,
                reason="value_missing",
            )
        if not math.isfinite(float(value)):
            return UnitConversion(
                original_value=float(value),
                original_unit=original_unit,
                normalized_value=None,
                normalized_unit=NORMALIZED_UNIT,
                conversion_rule="none",
                status=INVALID,
                reason="value_not_finite",
            )

        factor = _UNIT_ALIASES.get(unit)
        if factor is None:
            raise UnitNormalizationError(f"Unsupported CNY unit: {original_unit}")
        normalized = float(value) * factor
        return UnitConversion(
            original_value=float(value),
            original_unit=original_unit,
            normalized_value=normalized,
            normalized_unit=NORMALIZED_UNIT,
            conversion_rule=f"multiply_by_{factor:g}",
            status=CALCULATED,
        )
