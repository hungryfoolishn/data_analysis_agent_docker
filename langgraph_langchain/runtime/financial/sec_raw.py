"""SEC Company Concept raw-response parser (B10.8.1).

This layer is deliberately separate from the acquisition adapter so raw SEC
field names, filing versions, units, and response hashes remain auditable.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any, Mapping

from langgraph_langchain.data.adapters.base import stable_json

__all__ = [
    "SecConceptParseError",
    "SecConceptFact",
    "SecConceptResponse",
    "parse_sec_company_concept",
]

_CIK_PATTERN = re.compile(r"\d+")
_CURRENCY_PATTERN = re.compile(r"[A-Z]{3}")
_REQUIRED_FACT_FIELDS = ("val", "end", "accn", "filed", "form")


class SecConceptParseError(ValueError):
    """Raised when a SEC Company Concept response cannot be parsed safely."""


@dataclass(frozen=True)
class SecConceptFact:
    cik: str
    taxonomy: str
    concept: str
    entity_name: str
    unit: str
    value: Decimal
    period_type: str
    period_start: str | None
    period_end: str
    fiscal_year: int | None
    fiscal_period: str | None
    form: str
    accession_number: str
    filing_date: str
    frame: str | None
    source_url: str
    source_response_hash: str

    @property
    def source_document_id(self) -> str:
        start = self.period_start or ""
        return f"sec:{self.accession_number}:{self.unit}:{start}:{self.period_end}"


@dataclass(frozen=True)
class SecConceptResponse:
    cik: str
    taxonomy: str
    concept: str
    label: str
    description: str
    entity_name: str
    facts: tuple[SecConceptFact, ...]
    source_url: str
    source_response_hash: str


def _validate_iso_date(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise SecConceptParseError(f"{field_name} must be an ISO date string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise SecConceptParseError(
            f"{field_name} is not a valid ISO date: {value!r}"
        ) from exc
    if parsed.isoformat() != value:
        raise SecConceptParseError(f"{field_name} must use YYYY-MM-DD: {value!r}")
    return value


def _decimal_value(value: object) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise SecConceptParseError("fact val must be a finite numeric value")
    if isinstance(value, float) and not isfinite(value):
        raise SecConceptParseError("fact val must be a finite numeric value")
    if not isinstance(value, (int, float, Decimal, str)):
        raise SecConceptParseError("fact val must be a finite numeric value")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SecConceptParseError(f"fact val is not a Decimal: {value!r}") from exc
    if not result.is_finite():
        raise SecConceptParseError("fact val must be finite")
    return result


def _parse_fact(
    fact: Mapping[str, Any],
    *,
    cik: str,
    taxonomy: str,
    concept: str,
    entity_name: str,
    unit: str,
    source_url: str,
    source_response_hash: str,
) -> SecConceptFact:
    missing = [field for field in _REQUIRED_FACT_FIELDS if fact.get(field) in (None, "")]
    if missing:
        raise SecConceptParseError(
            "SEC fact is missing required field(s): " + ", ".join(missing)
        )

    period_start_value = fact.get("start")
    period_end_value = _validate_iso_date(fact.get("end"), "fact end")
    if period_start_value is None:
        period_type = "instant"
        period_start = None
    else:
        period_start = _validate_iso_date(period_start_value, "fact start")
        period_type = "duration"
        if date.fromisoformat(period_start) > date.fromisoformat(period_end_value):
            raise SecConceptParseError("fact start must not be after end")

    fiscal_year_value = fact.get("fy")
    if fiscal_year_value is not None and (
        isinstance(fiscal_year_value, bool) or not isinstance(fiscal_year_value, int)
    ):
        raise SecConceptParseError("fact fy must be an integer or null")
    fiscal_period_value = fact.get("fp")
    if fiscal_period_value is not None and (
        not isinstance(fiscal_period_value, str) or not fiscal_period_value
    ):
        raise SecConceptParseError("fact fp must be a non-empty string or null")
    if not isinstance(fact.get("form"), str) or not fact["form"]:
        raise SecConceptParseError("fact form must be a non-empty string")

    return SecConceptFact(
        cik=cik,
        taxonomy=taxonomy,
        concept=concept,
        entity_name=entity_name,
        unit=unit,
        value=_decimal_value(fact["val"]),
        period_type=period_type,
        period_start=period_start,
        period_end=period_end_value,
        fiscal_year=fiscal_year_value,
        fiscal_period=fiscal_period_value,
        form=fact["form"],
        accession_number=str(fact["accn"]),
        filing_date=str(fact["filed"]),
        frame=str(fact["frame"]) if fact.get("frame") is not None else None,
        source_url=source_url,
        source_response_hash=source_response_hash,
    )


def parse_sec_company_concept(
    payload: Any,
    *,
    taxonomy: str,
    concept: str,
    preferred_unit: str | None = None,
    source_url: str = "",
) -> SecConceptResponse:
    """Parse one SEC Company Concept response without collapsing filing versions.

    The result keeps every fact and accession.  Multiple units are only resolved
    when an explicit ``preferred_unit`` is supplied; otherwise ambiguity is an
    explicit error.
    """
    if not isinstance(payload, Mapping):
        raise SecConceptParseError("SEC Company Concept payload must be a JSON object")
    if not taxonomy or not concept:
        raise SecConceptParseError("taxonomy and concept are required")

    response_taxonomy = payload.get("taxonomy")
    response_tag = payload.get("tag")
    if response_taxonomy != taxonomy:
        raise SecConceptParseError(
            f"response taxonomy {response_taxonomy!r} does not match requested {taxonomy!r}"
        )
    if response_tag != concept:
        raise SecConceptParseError(
            f"response concept {response_tag!r} does not match requested {concept!r}"
        )

    cik_value = payload.get("cik")
    if (
        isinstance(cik_value, bool)
        or not isinstance(cik_value, (int, str))
        or not _CIK_PATTERN.fullmatch(str(cik_value))
    ):
        raise SecConceptParseError("SEC payload cik must contain digits")
    cik = str(cik_value)

    units = payload.get("units")
    if not isinstance(units, Mapping) or not units:
        raise SecConceptParseError("SEC payload units must be a non-empty object")

    if preferred_unit is not None:
        selected_unit = preferred_unit
        if selected_unit not in units:
            raise SecConceptParseError(
                f"preferred unit {selected_unit!r} is not present in SEC response"
            )
    else:
        if len(units) != 1:
            raise SecConceptParseError(
                "SEC response has ambiguous units; preferred_unit is required: "
                + ", ".join(sorted(map(str, units)))
            )
        selected_unit = next(iter(units))

    facts_payload = units[selected_unit]
    if not isinstance(facts_payload, list):
        raise SecConceptParseError(f"unit {selected_unit!r} facts must be an array")

    entity_name = payload.get("entityName")
    if not isinstance(entity_name, str) or not entity_name:
        raise SecConceptParseError("SEC payload entityName must be a non-empty string")
    label = str(payload.get("label") or "")
    description = str(payload.get("description") or "")
    source_response_hash = hashlib.sha256(stable_json(dict(payload)).encode("utf-8")).hexdigest()

    facts = tuple(
        _parse_fact(
            fact,
            cik=cik,
            taxonomy=taxonomy,
            concept=concept,
            entity_name=entity_name,
            unit=selected_unit,
            source_url=source_url,
            source_response_hash=source_response_hash,
        )
        for fact in facts_payload
        if isinstance(fact, Mapping)
    )
    if len(facts) != len(facts_payload):
        raise SecConceptParseError("SEC unit facts must all be JSON objects")

    return SecConceptResponse(
        cik=cik,
        taxonomy=taxonomy,
        concept=concept,
        label=label,
        description=description,
        entity_name=entity_name,
        facts=facts,
        source_url=source_url,
        source_response_hash=source_response_hash,
    )
