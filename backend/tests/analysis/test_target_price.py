from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal

from app.analysis.service import AnalysisLine, AnalysisSource
from app.analysis.target_price import (
    CPI_KOSIS_CLASSIFIER_CODE,
    CPI_KOSIS_ITEM_ID,
    CPI_KOSIS_ORG_ID,
    CPI_KOSIS_TABLE_ID,
    KOSIS_CLASSIFIER_CODE,
    KOSIS_ITEM_ID,
    KOSIS_ORG_ID,
    KOSIS_TABLE_ID,
    _target_line_from_cpi,
    _validated_cpi_kosis_rates,
    _target_line,
    _validated_kosis_points,
)


def _matched_line() -> AnalysisLine:
    return AnalysisLine(
        raw_item_id=91,
        item_name="MOTOR",
        spec="1KW",
        spec_source_status="PRESENT",
        unit="EA",
        quantity=Decimal("2"),
        quote_unit_price=Decimal("180"),
        quote_amount=Decimal("360"),
        match_status="MATCHED",
        assessment="HIGH",
        reference_price=Decimal("100"),
        minimum_price=Decimal("100"),
        average_price=Decimal("100"),
        maximum_price=Decimal("100"),
        variance_amount=Decimal("80"),
        variance_percent=Decimal("80"),
        clean_decision_id=1,
        membership_decision_id=1,
        standard_item_id=1,
        standard_item_version_id=1,
        canonical_name="MOTOR",
        canonical_spec="1KW",
        canonical_unit="EA",
        standard_price_version_id=7,
        standard_price_item_version_id=1,
        standard_observation_count=2,
        evidence_quality="MULTI_OBSERVATION",
        market_price_lookup_required=False,
        market_price_lookup_status="NOT_REQUIRED",
        candidates=(),
        source=AnalysisSource(9, "new.xlsx", 9, "new.xlsx", "a" * 64, "Sheet1", None, 2, "A2:G2", "xlsx", "v1"),
    )


def _observation(
    raw_id: int,
    quality: str,
    *,
    unit_price: Decimal | None = None,
    document_id: int | None = None,
) -> tuple:
    evidence = json.dumps(
        {
            "quote_date": {
                "quality": quality,
                "use_for_index": "EXACT_DATE",
            }
        }
    )
    return (
        raw_id,
        raw_id + 100,
        unit_price if unit_price is not None else Decimal("100") if raw_id == 1 else Decimal("1000"),
        date(2016, 1, 15),
        evidence,
        document_id if document_id is not None else 20 + raw_id,
        f"historical-{raw_id}.xlsx",
        30 + raw_id,
        "Sheet1",
        None,
        5,
        "A5:G5",
    )


def test_target_price_uses_only_source_confirmed_exact_dates() -> None:
    result = _target_line(
        _matched_line(),
        (
            _observation(1, "SOURCE_CONFIRMED"),
            _observation(2, "REFERENCE_BACKFILL"),
        ),
        {"201601": Decimal("80"), "202606": Decimal("120")},
        "202606",
        Decimal("120"),
    )

    assert result.status == "AVAILABLE"
    assert result.target_unit_price == Decimal("150.000000")
    assert result.target_amount == Decimal("300.000000")
    assert result.variance_amount == Decimal("60.000000")
    assert result.unit_variance_amount == Decimal("30.000000")
    assert result.variance_percent == Decimal("20.000000")
    assert result.used_observation_count == 1
    assert result.excluded_observation_count == 1
    assert result.evidence[0].source_period == "201601"


def test_target_price_does_not_guess_when_date_evidence_is_not_confirmed() -> None:
    result = _target_line(
        _matched_line(),
        (_observation(2, "FILE_DATE_INFERRED"),),
        {"201601": Decimal("80"), "202606": Decimal("120")},
        "202606",
        Decimal("120"),
    )

    assert result.status == "DATE_UNAVAILABLE"
    assert result.target_unit_price is None
    assert result.used_observation_count == 0


def test_kosis_validation_accepts_only_the_fixed_official_series() -> None:
    valid = {
        "ORG_ID": KOSIS_ORG_ID,
        "TBL_ID": KOSIS_TABLE_ID,
        "ITM_ID": KOSIS_ITEM_ID,
        "C1": KOSIS_CLASSIFIER_CODE,
        "PRD_SE": "M",
        "PRD_DE": "202606",
        "DT": "130.03",
    }
    wrong_classifier = {**valid, "C1": "OTHER", "DT": "999"}

    assert _validated_kosis_points([valid, wrong_classifier]) == {
        "202606": Decimal("130.030000")
    }


def _cpi_rates_2017_to_2025() -> dict[str, Decimal]:
    return {
        "2017": Decimal("1.9"),
        "2018": Decimal("1.5"),
        "2019": Decimal("0.4"),
        "2020": Decimal("0.5"),
        "2021": Decimal("2.5"),
        "2022": Decimal("5.1"),
        "2023": Decimal("3.6"),
        "2024": Decimal("2.3"),
        "2025": Decimal("2.1"),
    }


def test_cpi_target_compounds_confirmed_annual_rates_and_rounds_to_krw() -> None:
    result = _target_line_from_cpi(
        _matched_line(),
        (
            _observation(
                1,
                "SOURCE_CONFIRMED",
                unit_price=Decimal("1000000"),
            ),
        ),
        _cpi_rates_2017_to_2025(),
        "2025",
        77,
    )

    assert result.status == "AVAILABLE"
    assert result.target_unit_price == Decimal("1216544")
    assert result.target_amount == Decimal("2433088")
    assert result.variance_amount == Decimal("-2432728")
    assert result.unit_variance_amount == Decimal("-1216364")
    assert result.evidence[0].source_period == "2016"
    assert result.evidence[0].inflation is not None
    assert result.evidence[0].inflation.sync_run_id == 77
    assert result.evidence[0].inflation.factor == Decimal("1.216544")
    assert result.evidence[0].inflation.cumulative_percent == Decimal("21.654370")
    assert [rate.year for rate in result.evidence[0].inflation.annual_rates] == [
        str(year) for year in range(2017, 2026)
    ]


def test_cpi_purchase_target_uses_lowest_adjusted_historical_price() -> None:
    result = _target_line_from_cpi(
        _matched_line(),
        (
            _observation(
                1,
                "SOURCE_CONFIRMED",
                unit_price=Decimal("1000000"),
            ),
            _observation(
                2,
                "SOURCE_CONFIRMED",
                unit_price=Decimal("1200000"),
            ),
        ),
        _cpi_rates_2017_to_2025(),
        "2025",
        77,
    )

    assert result.status == "AVAILABLE"
    assert result.target_unit_price == Decimal("1216544")
    assert result.target_amount == Decimal("2433088")
    assert [item.raw_item_id for item in result.evidence] == [1, 2]
    assert "가장 낮은 금액을 협상 목표로 채택" in result.reason


def test_cpi_target_counts_repeated_rows_from_one_quote_once() -> None:
    result = _target_line_from_cpi(
        _matched_line(),
        (
            _observation(
                1,
                "SOURCE_CONFIRMED",
                unit_price=Decimal("1000000"),
                document_id=41,
            ),
            _observation(
                2,
                "SOURCE_CONFIRMED",
                unit_price=Decimal("1200000"),
                document_id=41,
            ),
        ),
        _cpi_rates_2017_to_2025(),
        "2025",
        77,
    )

    assert result.status == "AVAILABLE"
    assert result.used_observation_count == 1
    assert result.excluded_observation_count == 1
    assert result.evidence[0].raw_item_id == 1


def test_cpi_target_waits_when_standard_group_is_not_comparable() -> None:
    result = _target_line_from_cpi(
        replace(_matched_line(), evidence_quality="NON_COMPARABLE"),
        (_observation(1, "SOURCE_CONFIRMED"),),
        _cpi_rates_2017_to_2025(),
        "2025",
        77,
    )

    assert result.status == "COMPARABILITY_REVIEW_REQUIRED"
    assert result.target_unit_price is None


def test_cpi_target_reports_rate_gap_without_ppi_fallback() -> None:
    rates = _cpi_rates_2017_to_2025()
    del rates["2021"]

    result = _target_line_from_cpi(
        _matched_line(),
        (_observation(1, "SOURCE_CONFIRMED"),),
        rates,
        "2025",
        77,
    )

    assert result.status == "RATE_GAP"
    assert result.target_unit_price is None
    assert result.used_observation_count == 0


def test_cpi_validation_accepts_negative_rates_above_minus_100() -> None:
    valid = {
        "ORG_ID": CPI_KOSIS_ORG_ID,
        "TBL_ID": CPI_KOSIS_TABLE_ID,
        "ITM_ID": CPI_KOSIS_ITEM_ID,
        "C1": CPI_KOSIS_CLASSIFIER_CODE,
        "PRD_SE": "A",
        "PRD_DE": "2020",
        "DT": "-0.4",
    }
    invalid = {**valid, "PRD_DE": "2021", "DT": "-100"}

    assert _validated_cpi_kosis_rates([valid, invalid]) == {
        "2020": Decimal("-0.400000")
    }
