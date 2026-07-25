from __future__ import annotations

from decimal import Decimal

import pytest

from app.matching.candidates import (
    CandidateItem,
    MatchQuery,
    rank_candidates,
)


def make_item(
    *,
    item_id: int,
    name: str,
    spec: str | None = None,
    unit: str | None = None,
    aliases: tuple[str, ...] = (),
) -> CandidateItem:
    return CandidateItem(
        standard_item_id=item_id,
        name=name,
        spec=spec,
        unit=unit,
        aliases=aliases,
    )


def test_unit_conflict_blocks_candidate() -> None:
    candidate = make_item(
        item_id=1,
        name="BEARING",
        spec="6204",
        unit="M",
    )
    result = rank_candidates(
        query=MatchQuery(name="BEARING", spec="6204", unit="EA"),
        items=[candidate],
    )
    assert result == []


def test_conflicting_explicit_model_tokens_block_candidate() -> None:
    result = rank_candidates(
        query=MatchQuery(
            name="SERVO MOTOR",
            spec="SGMAH-04AAA61",
            unit="EA",
        ),
        items=[
            make_item(
                item_id=1,
                name="SERVO MOTOR",
                spec="SGM7J-04A",
                unit="EA",
            )
        ],
    )
    assert result == []


def test_shared_rating_does_not_hide_model_number_conflict() -> None:
    result = rank_candidates(
        query=MatchQuery(
            name="SERVO MOTOR",
            spec="SGMAH-04AAA61 400W",
            unit="EA",
        ),
        items=[
            make_item(
                item_id=1,
                name="SERVO MOTOR",
                spec="SGM7J-04A 400W",
                unit="EA",
            )
        ],
    )
    assert result == []


def test_rating_only_match_does_not_promote_unrelated_items() -> None:
    result = rank_candidates(
        query=MatchQuery(name="MOTOR", spec="400W", unit="EA"),
        items=[
            make_item(
                item_id=1,
                name="HEATER",
                spec="400W",
                unit="EA",
            )
        ],
    )
    assert result == []


def test_rating_only_match_stays_below_model_token_minimum() -> None:
    result = rank_candidates(
        query=MatchQuery(name="SERVO MOTOR", spec="400W", unit="EA"),
        items=[
            make_item(
                item_id=1,
                name="SERVO MOTOR ASSEMBLY",
                spec="400W",
                unit="EA",
            )
        ],
    )[0]
    assert result.method == "LEXICAL_RULE_V1"
    assert result.matched_tokens == ("400W",)
    assert result.final_score < Decimal("0.900000")


def test_partially_shared_but_conflicting_model_identifiers_are_blocked() -> None:
    result = rank_candidates(
        query=MatchQuery(
            name="CONTROLLER",
            spec="ABC-123 DEF-456",
            unit="EA",
        ),
        items=[
            make_item(
                item_id=1,
                name="CONTROLLER",
                spec="ABC-123 XYZ-999",
                unit="EA",
            )
        ],
    )
    assert result == []


def test_model_identifier_subset_is_compatible() -> None:
    result = rank_candidates(
        query=MatchQuery(
            name="CONTROLLER",
            spec="ABC-123 DEF-456",
            unit="EA",
        ),
        items=[
            make_item(
                item_id=1,
                name="CONTROLLER",
                spec="ABC-123",
                unit="EA",
            )
        ],
    )[0]
    assert result.method == "MODEL_TOKEN_RULE_V1"
    assert result.matched_tokens == ("ABC-123",)
    assert result.final_score >= Decimal("0.900000")


def test_model_number_match_ranks_before_name_only_match() -> None:
    results = rank_candidates(
        query=MatchQuery(
            name="SERVO MOTOR",
            spec="SGMAH-04AAA61 400W",
            unit="EA",
        ),
        items=[
            make_item(
                item_id=1,
                name="SERVO MOTOR",
                spec="OTHER 400W",
                unit="EA",
            ),
            make_item(
                item_id=2,
                name="AC SERVO",
                spec="SGMAH-04AAA61",
                unit="EA",
            ),
        ],
    )
    assert results[0].standard_item_id == 2
    assert results[0].matched_tokens == ("SGMAH-04AAA61",)
    assert results[0].final_score >= Decimal("0.900000")


def test_exact_normalized_name_spec_and_unit_has_perfect_score() -> None:
    results = rank_candidates(
        query=MatchQuery(
            name=" bearing ",
            spec="6204-zz",
            unit=" ea ",
        ),
        items=[
            make_item(
                item_id=7,
                name="BEARING",
                spec="6204 ZZ",
                unit="EA",
            )
        ],
    )
    assert results[0].final_score == Decimal("1.000000")
    assert results[0].method == "EXACT_RULE_V1"


def test_exact_model_token_sets_minimum_score() -> None:
    result = rank_candidates(
        query=MatchQuery(name="구동 장치", spec="SGMAH-04AAA61", unit="EA"),
        items=[
            make_item(
                item_id=1,
                name="AC SERVO ASSEMBLY",
                spec="SGMAH-04AAA61",
                unit="EA",
            )
        ],
    )[0]
    assert result.final_score >= Decimal("0.900000")
    assert result.method == "MODEL_TOKEN_RULE_V1"


def test_lexical_candidate_below_threshold_is_omitted() -> None:
    assert (
        rank_candidates(
            query=MatchQuery(name="BEARING", spec=None, unit=None),
            items=[
                make_item(
                    item_id=1,
                    name="HYDRAULIC CONTROL PANEL",
                    unit=None,
                )
            ],
        )
        == []
    )


def test_threshold_is_inclusive_and_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.matching.candidates._lexical_score",
        lambda **_: (
            Decimal("1.000000"),
            Decimal("0.000000"),
            Decimal("0.000000"),
        ),
    )
    result = rank_candidates(
        query=MatchQuery(name="A", spec=None, unit=None),
        items=[make_item(item_id=1, name="B")],
    )
    assert result[0].final_score == Decimal("0.650000")


def test_top_n_and_ties_are_stable_by_standard_item_id() -> None:
    items = [
        make_item(item_id=9, name="BALL BEARING"),
        make_item(item_id=3, name="BALL BEARING"),
        make_item(item_id=6, name="BALL BEARING"),
    ]
    result = rank_candidates(
        query=MatchQuery(name="BALL BEARING", spec=None, unit=None),
        items=items,
        top_n=2,
    )
    assert [candidate.standard_item_id for candidate in result] == [3, 6]


def test_top_n_must_be_positive() -> None:
    with pytest.raises(ValueError, match="top_n"):
        rank_candidates(
            query=MatchQuery(name="BEARING", spec=None, unit=None),
            items=[],
            top_n=0,
        )


def test_alias_can_supply_best_name_score() -> None:
    result = rank_candidates(
        query=MatchQuery(name="볼 베어링", spec=None, unit="EA"),
        items=[
            make_item(
                item_id=1,
                name="BALL BEARING",
                unit="EA",
                aliases=("볼 베어링",),
            )
        ],
    )[0]
    assert result.name_score == Decimal("1.000000")


def test_empty_specs_do_not_create_false_model_conflicts() -> None:
    result = rank_candidates(
        query=MatchQuery(name="AC MOTOR", spec=None, unit="EA"),
        items=[make_item(item_id=1, name="AC MOTOR", spec=None, unit="EA")],
    )
    assert result[0].final_score == Decimal("1.000000")


def test_all_decimal_scores_are_quantized_to_six_places() -> None:
    result = rank_candidates(
        query=MatchQuery(name="SERVO MOTOR", spec="400W", unit="EA"),
        items=[
            make_item(
                item_id=1,
                name="SERVO MOTOR UNIT",
                spec="400W TYPE",
                unit="EA",
            )
        ],
    )[0]
    for score in (
        result.name_score,
        result.spec_score,
        result.token_score,
        result.final_score,
    ):
        assert score.as_tuple().exponent == -6
    assert result.embedding_score is None
