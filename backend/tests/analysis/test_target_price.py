from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from app.analysis.service import AnalysisLine, AnalysisSource
from app.analysis.target_price import (
    KOSIS_CLASSIFIER_CODE,
    KOSIS_ITEM_ID,
    KOSIS_ORG_ID,
    KOSIS_TABLE_ID,
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


def _observation(raw_id: int, quality: str) -> tuple:
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
        Decimal("100") if raw_id == 1 else Decimal("1000"),
        date(2016, 1, 15),
        evidence,
        20 + raw_id,
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
