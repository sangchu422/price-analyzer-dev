from __future__ import annotations

import pytest

from app.matching.normalization import model_tokens, normalize_search_text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  Servo   Motor ", "SERVO MOTOR"),
        ("BEARING（6204-ZZ）", "BEARING (6204 ZZ)"),
        ("AC-MOTOR_400W", "AC MOTOR 400W"),
        ("서보 Motor／감속기", "서보 MOTOR 감속기"),
        (None, ""),
    ],
)
def test_normalize_search_text(value: str | None, expected: str) -> None:
    assert normalize_search_text(value) == expected


def test_model_tokens_are_preserved_and_sorted() -> None:
    assert model_tokens("SERVO MOTOR SGMAH-04AAA61 400W") == (
        "400W",
        "SGMAH-04AAA61",
    )


def test_model_tokens_include_numeric_part_numbers_but_not_short_counts() -> None:
    assert model_tokens("BEARING 6204 ZZ 2 EA") == (
        "6204",
        "6204-ZZ",
    )


def test_industrial_ratings_are_tokens_but_not_model_identifiers() -> None:
    assert model_tokens("400VAC 24VDC 2KVA 5HP 12NM 10BAR 100PSI") == (
        "100PSI",
        "10BAR",
        "12NM",
        "24VDC",
        "2KVA",
        "400VAC",
        "5HP",
    )


def test_model_separator_is_preserved_only_inside_explicit_model_token() -> None:
    assert normalize_search_text("SGMAH-04AAA61 / AC-MOTOR") == (
        "SGMAH-04AAA61 AC MOTOR"
    )
