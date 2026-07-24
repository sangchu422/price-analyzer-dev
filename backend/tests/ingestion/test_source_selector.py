import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.ingestion.source_selector import (
    SourceGroup,
    build_source_groups,
    logical_stem,
)


def test_original_and_unlocked_quote_form_one_logical_group() -> None:
    original = Path("5. 견적서.xls")
    unlocked = Path("5. 견적서_보안해제.xlsx")

    groups = build_source_groups([original, unlocked])

    assert groups == [
        SourceGroup(
            logical_name="5. 견적서",
            variants=[unlocked, original],
            preferred=unlocked,
        )
    ]


def test_logical_stem_removes_only_a_trailing_unlock_suffix() -> None:
    assert (
        logical_stem(Path("보안해제 장비_보안해제.xlsx"))
        == "보안해제 장비"
    )
    assert (
        logical_stem(Path("보안해제 장비_보안해제_extra.xlsx"))
        == "보안해제 장비_보안해제_extra"
    )
    assert (
        logical_stem(Path("  보안해제 장비_보안해제  .XLSX"))
        == "보안해제 장비"
    )


def test_preference_is_unlock_state_then_extension_then_path() -> None:
    paths = [
        Path("quote.xlsx"),
        Path("quote.xls"),
        Path("quote_보안해제.xls"),
        Path("quote_보안해제.xlsx"),
    ]

    forward = build_source_groups(paths)
    backward = build_source_groups(reversed(paths))

    assert forward == backward
    assert forward[0].variants == [
        Path("quote_보안해제.xlsx"),
        Path("quote_보안해제.xls"),
        Path("quote.xlsx"),
        Path("quote.xls"),
    ]
    assert forward[0].preferred == Path("quote_보안해제.xlsx")


def test_distinct_directories_with_the_same_stem_remain_separate() -> None:
    groups = build_source_groups(
        [
            Path("second/quote_보안해제.xlsx"),
            Path("first/quote.xls"),
            Path("second/quote.xls"),
            Path("first/quote_보안해제.xlsx"),
        ]
    )

    assert [group.logical_name for group in groups] == [
        "first/quote",
        "second/quote",
    ]
    assert all(len(group.variants) == 2 for group in groups)


def test_supplied_root_keeps_absolute_logical_names_machine_independent(
    tmp_path: Path,
) -> None:
    original = tmp_path / "nested" / "quote.xls"
    unlocked = tmp_path / "nested" / "quote_보안해제.xlsx"

    [group] = build_source_groups(
        [unlocked, original],
        root=tmp_path,
    )

    assert group.logical_name == "nested/quote"


def test_all_input_entries_are_preserved_without_deduplication() -> None:
    original = Path("quote.xls")
    unlocked = Path("quote_보안해제.xlsx")

    [group] = build_source_groups([original, unlocked, original])

    assert group.variants == [unlocked, original, original]


def test_source_group_is_frozen() -> None:
    path = Path("quote.xlsx")
    group = SourceGroup("quote", [path], path)

    with pytest.raises(FrozenInstanceError):
        group.preferred = Path("other.xlsx")


def test_real_unlocked_pair_fixture_groups_and_prefers_each_unlocked_path() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "unlocked_pairs.json"
    )
    pairs = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert len(pairs) == 12
    for pair in pairs:
        original = Path(pair["original"])
        unlocked = Path(pair["unlocked"])

        groups = build_source_groups([original, unlocked])

        assert len(groups) == 1, pair
        assert groups[0].variants == [unlocked, original]
        assert groups[0].preferred == unlocked
