"""B10.3: explicit canonical financial period semantics."""

from __future__ import annotations

from decimal import Decimal

import pytest

from langgraph_langchain.runtime.financial.models import (
    FinancialLineItem,
    FinancialStatement,
    FinancialUnit,
)
from langgraph_langchain.runtime.financial.period import PeriodNormalizer
from langgraph_langchain.runtime.financial.periods import (
    PeriodSemanticsError,
    derive_single_quarter_item,
    derive_ttm_item,
)


def _unit(currency="CNY"):
    return FinancialUnit(
        statistic_kind="absolute_amount",
        numeric_unit=currency,
        currency=currency,
    )


def _item(value, currency="CNY", concept="revenue"):
    return FinancialLineItem(
        canonical_concept=concept,
        value=value,
        statistic_kind="absolute_amount",
        unit=_unit(currency),
    )


def _statement(
    start,
    end,
    fiscal_period,
    value,
    *,
    entity="ent_1",
    statement_type="income",
    currency="CNY",
    fiscal_year=2024,
    concept="revenue",
):
    return FinancialStatement(
        entity_id=entity,
        statement_type=statement_type,
        period_type="duration",
        period_start=start,
        period_end=end,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        reporting_basis="consolidated",
        currency=currency,
        line_items=(_item(value, currency=currency, concept=concept),),
    )


def test_cumulative_to_single_quarter_uses_statement_dates():
    q1 = _statement("2024-01-01", "2024-03-31", "Q1", Decimal("20"))
    h1 = _statement("2024-01-01", "2024-06-30", "H1", Decimal("50"))
    result = derive_single_quarter_item(h1, q1, concept="revenue")
    assert result.value == Decimal("30")
    assert (result.period_start, result.period_end) == ("2024-04-01", "2024-06-30")
    assert result.source_metadata["period_derivation"]["method"] == "cumulative_difference"
    assert result.source_metadata["period_derivation"]["input_values"] == {
        "current_value": Decimal("50"),
        "previous_value": Decimal("20"),
    }


def test_first_cumulative_period_is_not_differenced():
    q1 = _statement("2024-01-01", "2024-03-31", "Q1", Decimal("20"))
    result = derive_single_quarter_item(q1, None, concept="revenue")
    assert result.value == Decimal("20")
    assert result.source_metadata["period_derivation"]["method"] == (
        "identity_first_cumulative_period"
    )


def test_non_natural_fiscal_year_is_supported():
    q1 = _statement("2024-04-01", "2024-06-30", "Q1", Decimal("30"))
    h1 = _statement("2024-04-01", "2024-09-30", "H1", Decimal("70"))
    result = derive_single_quarter_item(h1, q1, concept="revenue")
    assert (result.period_start, result.period_end) == ("2024-07-01", "2024-09-30")
    assert result.value == Decimal("40")


def test_missing_previous_cumulative_period_is_rejected():
    h1 = _statement("2024-01-01", "2024-06-30", "H1", Decimal("50"))
    with pytest.raises(PeriodSemanticsError, match="requires a previous"):
        derive_single_quarter_item(h1, None, concept="revenue")


def test_inconsistent_cumulative_start_is_rejected():
    q1 = _statement("2024-01-01", "2024-03-31", "Q1", Decimal("20"))
    h1 = _statement("2024-01-01", "2024-06-30", "H1", Decimal("50"))
    h1 = h1.model_copy(update={"period_start": "2024-04-02"})
    with pytest.raises(PeriodSemanticsError, match="same fiscal-period start"):
        derive_single_quarter_item(h1, q1, concept="revenue")


def test_ttm_sums_four_complete_quarters():
    statements = [
        _statement("2024-01-01", "2024-03-31", "Q1", Decimal("20")),
        _statement("2024-01-01", "2024-06-30", "H1", Decimal("50")),
        _statement("2024-01-01", "2024-09-30", "9M", Decimal("80")),
        _statement("2024-01-01", "2024-12-31", "FY", Decimal("100")),
    ]
    result = derive_ttm_item(statements, concept="revenue")
    assert result.output_item.value == Decimal("100")
    assert [item.value for item in result.quarter_items] == [
        Decimal("20"),
        Decimal("30"),
        Decimal("30"),
        Decimal("20"),
    ]
    assert result.output_item.period_start == "2024-01-01"
    assert result.output_item.period_end == "2024-12-31"
    assert result.output_item.source_metadata["period_derivation"]["method"] == (
        "sum_complete_quarters"
    )


def test_ttm_rejects_incomplete_quarters():
    statements = [
        _statement("2024-01-01", "2024-03-31", "Q1", Decimal("20")),
        _statement("2024-01-01", "2024-06-30", "H1", Decimal("50")),
        _statement("2024-01-01", "2024-09-30", "9M", Decimal("80")),
    ]
    with pytest.raises(PeriodSemanticsError, match="exactly four"):
        derive_ttm_item(statements, concept="revenue")


def test_ttm_rejects_repeated_or_noncontiguous_quarters():
    statements = [
        _statement("2024-01-01", "2024-03-31", "Q1", Decimal("20")),
        _statement("2024-01-01", "2024-06-30", "H1", Decimal("50")),
        _statement("2024-01-01", "2024-06-30", "H1", Decimal("50")),
        _statement("2024-01-01", "2024-12-31", "FY", Decimal("100")),
    ]
    with pytest.raises(PeriodSemanticsError, match="must precede current"):
        derive_ttm_item(statements, concept="revenue")


def test_per_share_cannot_be_ttm_summed():
    statements = []
    for start, end, label, value in [
        ("2024-01-01", "2024-03-31", "Q1", Decimal("1")),
        ("2024-01-01", "2024-06-30", "H1", Decimal("2")),
        ("2024-01-01", "2024-09-30", "9M", Decimal("3")),
        ("2024-01-01", "2024-12-31", "FY", Decimal("4")),
    ]:
        statements.append(
            FinancialStatement(
                entity_id="ent_1",
                statement_type="income",
                period_type="duration",
                period_start=start,
                period_end=end,
                fiscal_year=2024,
                fiscal_period=label,
                reporting_basis="consolidated",
                currency="CNY",
                line_items=(
                    FinancialLineItem(
                        canonical_concept="eps",
                        value=value,
                        statistic_kind="per_share",
                        unit=FinancialUnit(
                            statistic_kind="per_share",
                            numeric_unit="CNY",
                            currency="CNY",
                            unit_denominator="share",
                        ),
                    ),
                ),
            )
        )
    with pytest.raises(PeriodSemanticsError, match="absolute_amount"):
        derive_ttm_item(statements, concept="eps")


def test_existing_period_normalizer_semantics_do_not_regress():
    normalizer = PeriodNormalizer()
    assert normalizer.parse("2024").normalized_period == "2024"
    assert normalizer.parse("2025Q1").previous().normalized_period == "2024Q4"
    assert normalizer.parse("2025H2").previous().normalized_period == "2025H1"
    assert normalizer.parse("FY2025").previous().normalized_period == "2024"
    with pytest.raises(Exception, match="TTM/LTM"):
        normalizer.parse("TTM")

def test_q1_and_fy_cannot_produce_q4_without_intermediate_periods():
    q1 = _statement("2024-01-01", "2024-03-31", "Q1", Decimal("20"))
    fy = _statement("2024-01-01", "2024-12-31", "FY", Decimal("100"))
    with pytest.raises(PeriodSemanticsError, match="missing_intermediate_cumulative_period"):
        derive_single_quarter_item(fy, q1, concept="revenue")


def test_cumulative_conversion_requires_adjacent_fiscal_labels():
    q1 = _statement("2024-01-01", "2024-03-31", "Q1", Decimal("20"))
    nine_m = _statement("2024-01-01", "2024-09-30", "9M", Decimal("80"))
    with pytest.raises(PeriodSemanticsError, match="missing_intermediate_cumulative_period"):
        derive_single_quarter_item(nine_m, q1, concept="revenue")


def test_version_mismatch_is_rejected_without_explicit_alignment():
    q1 = _statement("2024-01-01", "2024-03-31", "Q1", Decimal("20"))
    h1 = _statement("2024-01-01", "2024-06-30", "H1", Decimal("50"))
    q1 = q1.model_copy(update={"accession_number": "acc-1", "source_document_id": "doc-1"})
    h1 = h1.model_copy(update={"accession_number": "acc-2", "source_document_id": "doc-2"})
    with pytest.raises(PeriodSemanticsError, match="filing version"):
        derive_single_quarter_item(h1, q1, concept="revenue")


def test_incompatible_identity_or_semantics_are_rejected():
    q1 = _statement("2024-01-01", "2024-03-31", "Q1", Decimal("20"))
    h1 = _statement("2024-01-01", "2024-06-30", "H1", Decimal("50"))
    cases = [
        (h1.model_copy(update={"entity_id": "ent_2"}), "one entity"),
        (h1.model_copy(update={"statement_type": "cash"}), "one statement type"),
        (h1.model_copy(update={"reporting_basis": "parent"}), "one reporting basis"),
        (h1.model_copy(update={"currency": "USD"}), "currency"),
        (h1.model_copy(update={"fiscal_year": 2025}), "one fiscal year"),
    ]
    for statement, reason in cases:
        with pytest.raises(PeriodSemanticsError, match=reason):
            derive_single_quarter_item(statement, q1, concept="revenue")


def test_non_natural_fiscal_year_does_not_guess_missing_calendar():
    q1 = _statement("2024-04-01", "2024-06-30", "Q1", Decimal("30"))
    fy = _statement("2024-04-01", "2025-03-31", "FY", Decimal("120"))
    with pytest.raises(PeriodSemanticsError, match="missing_intermediate"):
        derive_single_quarter_item(fy, q1, concept="revenue")


def test_negative_and_missing_cumulative_values_are_handled_explicitly():
    q1 = _statement("2024-01-01", "2024-03-31", "Q1", Decimal("-20"))
    h1 = _statement("2024-01-01", "2024-06-30", "H1", Decimal("-30"))
    q1 = q1.model_copy(update={"accession_number": "acc-1", "source_document_id": "doc-1"})
    h1 = h1.model_copy(update={"accession_number": "acc-1", "source_document_id": "doc-1"})
    result = derive_single_quarter_item(h1, q1, concept="revenue")
    assert result.value == Decimal("-10")

    missing = h1.model_copy(update={"line_items": (_item(None),)})
    with pytest.raises(PeriodSemanticsError, match="value is missing"):
        derive_single_quarter_item(missing, q1, concept="revenue")
